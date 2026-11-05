"""Flight searches for the flights stage: the flights out, then the flights home.

A round trip is searched in two steps, as the provider does it: the flights out, each
priced as the whole round trip with its cheapest flight home, and, once one is picked, the
flights home for it (searched with the picked flight's `departure_token`, each priced as
the whole trip). Without a token (the flight out was added by hand) the flights home are
searched one way.

Candidates are ranked by rules: the ones that match the flight preferences first, then
shortest, then cheapest. Each carries the `add_flight` change picking it makes, with the
provider's own airport-local times, so nothing here (or anywhere) writes a flight's times.
"""

import logging
from decimal import Decimal

from pydantic import JsonValue, ValidationError

from features.itinerary.schemas import FlightItem, active
from features.planning.flight_choice import (
    flight_arguments,
    format_minutes,
    local_time,
    rank_flights,
    stops_label,
)
from features.planning.schemas import Candidate, CandidateChange, RunSearch
from features.planning.search_types import SearchContext, SearchFailedError, unseen
from features.plans.schemas import (
    STOP_LABELS,
    DepartWindow,
    FlightPreferences,
    PlanSnapshot,
    active_budget,
)
from shared.domain import Money, Place
from shared.flights.airports import city_airports
from shared.flights.types import FlightOption, FlightQuery, FlightSearchError

logger = logging.getLogger(__name__)

# How many flights an offer lists.
TOP_CANDIDATES = 5

# Local departure hours [from, to) of each time of day the preferences can ask for.
_WINDOWS: dict[DepartWindow, tuple[int, int]] = {
    "morning": (5, 12),
    "afternoon": (12, 17),
    "evening": (17, 24),
}

# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def _airports(place: Place, role: str) -> tuple[str, ...]:
    codes = city_airports(place.iata, place.city, place.name)
    if not codes:
        raise SearchFailedError(
            f"I couldn't find an airport for {place.name} (where the trip {role}). Go back to "
            "the basics and give the nearest city with an airport, or skip flights."
        )
    return codes


def _preferences(plan: PlanSnapshot) -> FlightPreferences:
    prefs = plan.preferences.flights
    if prefs is None:
        # The engine asks for the preferences before it offers a search.
        raise SearchFailedError("Tell me how you like to fly first.")
    return prefs


def flight_query(plan: PlanSnapshot, *, one_way_home: bool = False) -> FlightQuery:
    """The search for the plan's flights: a round trip from the origin, or (`one_way_home`)
    one way back from the destination on the trip's last day."""
    prefs = _preferences(plan)
    if plan.origin is None or plan.destination is None or plan.travelers is None:
        raise SearchFailedError("The trip needs a destination, an origin and travelers first.")
    if plan.start_date is None or plan.end_date is None:
        raise SearchFailedError("The trip needs dates first.")
    home = _airports(plan.origin, "starts")
    away = _airports(plan.destination, "goes")
    budget = active_budget(plan.budget)
    try:
        return FlightQuery(
            origin=away if one_way_home else home,
            destination=home if one_way_home else away,
            depart_date=plan.end_date if one_way_home else plan.start_date,
            return_date=None if one_way_home else plan.end_date,
            cabin=prefs.cabin,
            # The search only takes adults; children are counted as adults so the price
            # covers everyone (most international fares for children are close to adults').
            adults=plan.travelers.adults + plan.travelers.children,
            currency=budget.currency if budget is not None else "USD",
        )
    except ValidationError as exc:
        logger.info("Plan %s: no flight query (%s)", plan.id, exc.errors(include_url=False)[:1])
        raise SearchFailedError(
            "I can't search flights for this trip: the search takes at most 9 travelers."
        ) from exc


async def _search(
    ctx: SearchContext, query: FlightQuery, departure_token: str | None = None
) -> list[FlightOption]:
    try:
        return await ctx.flights.search(query, departure_token=departure_token)
    except FlightSearchError as exc:
        raise SearchFailedError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Ranking by preferences
# ---------------------------------------------------------------------------


def misses(option: FlightOption, prefs: FlightPreferences) -> list[str]:
    """How `option` falls short of the preferences, one phrase each; empty when it fits."""
    found: list[str] = []
    if prefs.max_stops is not None and len(option.layovers) > prefs.max_stops:
        found.append(f"{stops_label(option)}, not {STOP_LABELS[prefs.max_stops]}")
    window = _WINDOWS.get(prefs.depart_window)
    departs = option.departure.time
    if window is not None and not window[0] <= departs.hour < window[1]:
        found.append(f"leaves at {departs:%H:%M}, not in the {prefs.depart_window}")
    return found


def rank_for(options: list[FlightOption], prefs: FlightPreferences) -> list[FlightOption]:
    """Best first: the options that fit the preferences, then the rest, each shortest then
    cheapest. The rest are kept so a strict preference still leaves something to pick."""
    ranked = rank_flights(options)
    # `sorted` is stable, so the travel-time order holds within each group.
    return sorted(ranked, key=lambda option: bool(misses(option, prefs)))


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


def _title(option: FlightOption) -> str:
    numbers = ", ".join(s.flight_number for s in option.segments if s.flight_number)
    airline = " / ".join(option.airlines)
    return f"{airline} {numbers}" if numbers else airline


def _summary(option: FlightOption, leg: str) -> str:
    depart = option.departure
    return f"Add flight {leg} {depart.iata} → {option.arrival.iata} on {depart.time:%Y-%m-%d %H:%M}"


def _add_flight(
    option: FlightOption, prefs: FlightPreferences, direction: str, cost: Money | None
) -> CandidateChange:
    arguments: dict[str, JsonValue] = {
        **flight_arguments(option, prefs.cabin, cost),
        "direction": direction,
        "title": f"Flight {option.departure.iata} to {option.arrival.iata}",
    }
    leg = "out" if direction == "outbound" else "home"
    return CandidateChange(name="add_flight", arguments=arguments, summary=_summary(option, leg))


def _candidate(
    number: int,
    option: FlightOption,
    prefs: FlightPreferences,
    price_note: str,
    changes: list[CandidateChange],
) -> Candidate:
    details = [format_minutes(option.total_duration_minutes), stops_label(option)]
    if option.price is not None:
        details.append(price_note)
    return Candidate(
        id=str(number),
        title=_title(option),
        subtitle=f"{local_time(option.departure)} → {local_time(option.arrival)}",
        details=details,
        warnings=misses(option, prefs),
        price=option.price,
        changes=changes,
        follow_up_token=option.departure_token,
    )


def _top(
    ctx: SearchContext, options: list[FlightOption], prefs: FlightPreferences
) -> list[FlightOption]:
    """The best few, leaving out flights already offered ("More options")."""
    fresh = [option for option in rank_for(options, prefs) if unseen(ctx, _title(option))]
    return fresh[:TOP_CANDIDATES]


def _outbound_flight(plan: PlanSnapshot) -> FlightItem | None:
    """The plan's (first) active flight out."""
    flights = [
        item
        for item in active(plan.items)
        if isinstance(item, FlightItem) and item.direction == "outbound"
    ]
    return min(flights, key=lambda flight: flight.start) if flights else None


def _round_trip_home(
    number: int, option: FlightOption, prefs: FlightPreferences, outbound: FlightItem | None
) -> Candidate:
    """A flight home priced as the whole trip. The fare goes on the flight out (replacing
    the estimate it was picked with) and the flight home costs 0, as with any round-trip
    fare, so the budget counts it once."""
    if option.price is None or outbound is None:
        changes = [_add_flight(option, prefs, "return", None)]
    else:
        fare = {**option.price.model_dump(mode="json"), "basis": "exact"}
        update = CandidateChange(
            name="update_item",
            arguments={"item_id": str(outbound.id), "changes": {"cost": fare}},
            summary=f"Set the round-trip fare on “{outbound.title}”",
        )
        free = Money(amount=Decimal(0), currency=option.price.currency)
        changes = [update, _add_flight(option, prefs, "return", free)]
    return _candidate(number, option, prefs, "Round-trip total for everyone", changes)


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------


async def outbound_flights(
    ctx: SearchContext, plan: PlanSnapshot, action: RunSearch
) -> list[Candidate]:
    prefs = _preferences(plan)
    options = await _search(ctx, flight_query(plan))
    return [
        _candidate(
            number,
            option,
            prefs,
            "Round-trip fare for everyone, with the cheapest flight home",
            [_add_flight(option, prefs, "outbound", option.price)],
        )
        for number, option in enumerate(_top(ctx, options, prefs), start=1)
    ]


async def return_flights(
    ctx: SearchContext, plan: PlanSnapshot, action: RunSearch
) -> list[Candidate]:
    prefs = _preferences(plan)
    if prefs.outbound_token is None:
        options = await _search(ctx, flight_query(plan, one_way_home=True))
        return [
            _candidate(
                number,
                option,
                prefs,
                "One-way fare for everyone",
                [_add_flight(option, prefs, "return", option.price)],
            )
            for number, option in enumerate(_top(ctx, options, prefs), start=1)
        ]
    options = await _search(ctx, flight_query(plan), departure_token=prefs.outbound_token)
    outbound = _outbound_flight(plan)
    return [
        _round_trip_home(number, option, prefs, outbound)
        for number, option in enumerate(_top(ctx, options, prefs), start=1)
    ]
