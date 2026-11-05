"""Hotel searches for the lodging stage: one per run of nights without a place to stay.

The engine names the nights (`RunSearch.nights`); this module turns them and the lodging
preferences into a `HotelQuery`, ranks what comes back by rules, and gives each candidate
the `add_lodging` change picking it makes. No model is involved.

Times are worked out here, not taken on trust: a stay's zone is where the trip's flights
and transfers have the traveller by the end of check-in day (else the destination's
airport zone), check-in waits until an hour after a leg that lands that day, and check-out
leaves three hours before a leg that leaves that day. The same zone rule
(`itinerary.zones`) is what `chat.tools.check_change` checks a pick against.

Candidates are ranked: those that fit the preferences (class, type, nightly budget) first,
then best rated, then cheapest for the stay. The rest stay listed with a warning saying
why, so a strict preference still leaves something to pick.
"""

import logging
import math
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import JsonValue, ValidationError

from features.itinerary.schemas import ItineraryItem, LodgingKind, active
from features.itinerary.zones import legs, zone_at
from features.planning.schemas import Candidate, CandidateChange, NightRange, RunSearch
from features.planning.search_types import SearchContext, SearchFailedError, unseen
from features.plans.schemas import HotelClass, LodgingPreferences, PlanSnapshot, active_budget
from shared.costs import format_money
from shared.domain import Money, Place
from shared.flights.airports import airport_timezone, city_airports
from shared.hotels.types import HotelOption, HotelQuery, HotelSearchError, HotelStars

logger = logging.getLogger(__name__)

# How many places to stay an offer lists.
TOP_CANDIDATES = 5

# When a listing doesn't give its own times.
DEFAULT_CHECK_IN = time(15, 0)
DEFAULT_CHECK_OUT = time(11, 0)
# Time from landing to checking in, and from checking out to the next departure.
AFTER_ARRIVAL = timedelta(hours=1)
BEFORE_DEPARTURE = timedelta(hours=3)
# Bounds that keep a stay overnight (`chat.tools._check_overnight`) however late the
# traveller lands or however early they leave. Check-out after midnight, not at it: a
# stay ending at 00:00 would leave its last night uncovered (`itinerary.days.item_days`).
_LATEST_CHECK_IN = time(23, 0)
_EARLIEST_CHECK_OUT = time(1, 0)

_CLASS_STARS: dict[HotelClass, tuple[HotelStars, ...]] = {
    "budget": (2,),
    "midrange": (3,),
    "upscale": (4,),
    "luxury": (5,),
}
# What a location search is prefixed with for kinds that aren't plain hotels.
_KIND_SEARCH: dict[LodgingKind, str] = {"hostel": "hostels", "resort": "resorts"}
_MAX_TITLE = 200
_MAX_URL = 2000

# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def _preferences(plan: PlanSnapshot) -> LodgingPreferences:
    prefs = plan.preferences.lodging
    if prefs is None:
        # The engine asks for the preferences before it offers a search.
        raise SearchFailedError("Tell me what kind of place you want to stay in first.")
    return prefs


def _nights(action: RunSearch) -> NightRange:
    if action.nights is None:
        raise SearchFailedError("There are no nights to find a place for.")
    return action.nights


def _location(destination: Place, prefs: LodgingPreferences) -> str:
    """What to type into the hotel search: "hostels in Alfama, Lisbon, Portugal"."""
    parts = (prefs.neighborhood, destination.name, destination.country)
    # dict.fromkeys drops repeats ("Lisbon, Lisbon") and keeps the order.
    place = ", ".join(dict.fromkeys(part for part in parts if part))
    noun = _KIND_SEARCH.get(prefs.kind)
    return f"{noun} in {place}" if noun else place


def _searches_stars(prefs: LodgingPreferences) -> bool:
    """Whether the hotel class applies: rentals and hostels have no stars."""
    return prefs.kind not in ("rental", "hostel")


def _currency(plan: PlanSnapshot, prefs: LodgingPreferences) -> str:
    if prefs.nightly_budget is not None:
        return prefs.nightly_budget.currency
    budget = active_budget(plan.budget)
    return budget.currency if budget is not None else "USD"


def hotel_query(plan: PlanSnapshot, nights: NightRange) -> HotelQuery:
    """The search for one run of nights, shaped by the lodging preferences."""
    prefs = _preferences(plan)
    if plan.destination is None or plan.travelers is None:
        raise SearchFailedError("The trip needs a destination and travelers first.")
    nightly = prefs.nightly_budget
    try:
        return HotelQuery(
            location=_location(plan.destination, prefs),
            check_in=nights.check_in,
            check_out=nights.check_out,
            adults=plan.travelers.adults,
            children=plan.travelers.children,
            currency=_currency(plan, prefs),
            stars=_CLASS_STARS[prefs.hotel_class] if _searches_stars(prefs) else (),
            # Whole units, rounded up so a budget of 120.50 still finds a 120.50 room.
            max_nightly=math.ceil(nightly.amount) if nightly and nightly.amount > 0 else None,
            vacation_rentals=prefs.kind == "rental",
        )
    except ValidationError as exc:
        logger.info("Plan %s: no hotel query (%s)", plan.id, exc.errors(include_url=False)[:1])
        raise SearchFailedError(
            "I can't search places to stay for this trip: the search takes at most 20 adults "
            "and 20 children."
        ) from exc


async def _search(ctx: SearchContext, query: HotelQuery) -> list[HotelOption]:
    try:
        return await ctx.hotels.search(query)
    except HotelSearchError as exc:
        raise SearchFailedError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Ranking by preferences
# ---------------------------------------------------------------------------


def _nightly(option: HotelOption, nights: int) -> Money | None:
    if option.nightly_rate is not None:
        return option.nightly_rate
    if option.total_rate is not None:
        amount = (option.total_rate.amount / nights).quantize(Decimal("0.01"))
        return Money(amount=amount, currency=option.total_rate.currency)
    return None


def _total(option: HotelOption, nights: int) -> Money | None:
    if option.total_rate is not None:
        return option.total_rate
    if option.nightly_rate is not None:
        rate = option.nightly_rate
        return Money(amount=rate.amount * nights, currency=rate.currency)
    return None


def misses(option: HotelOption, prefs: LodgingPreferences, nights: int) -> list[str]:
    """How `option` falls short of the preferences, one phrase each; empty when it fits."""
    found: list[str] = []
    if prefs.kind == "rental" and option.kind == "hotel":
        found.append("a hotel, not a rental")
    elif prefs.kind != "rental" and option.kind == "vacation_rental":
        found.append(f"a vacation rental, not a {prefs.kind}")
    stars = _CLASS_STARS[prefs.hotel_class]
    if _searches_stars(prefs) and option.stars is not None and option.stars not in stars:
        found.append(f"{option.stars}-star, not {prefs.hotel_class}")
    limit = prefs.nightly_budget
    nightly = _nightly(option, nights)
    if (
        limit is not None
        and nightly is not None
        and nightly.currency == limit.currency
        and nightly.amount > limit.amount
    ):
        found.append(f"{format_money(nightly)} a night, over {format_money(limit)}")
    return found


def rank_for(
    options: Sequence[HotelOption], prefs: LodgingPreferences, nights: int
) -> list[HotelOption]:
    """Best first: the options that fit, then the rest; each best rated, then cheapest."""

    def key(option: HotelOption) -> tuple[bool, float, Decimal]:
        total = _total(option, nights)
        return (
            bool(misses(option, prefs, nights)),
            -(option.rating or 0.0),
            total.amount if total is not None else Decimal("Infinity"),
        )

    return sorted(options, key=key)


# ---------------------------------------------------------------------------
# Times: when and in which zone the stay starts and ends
# ---------------------------------------------------------------------------


def active_legs(plan: PlanSnapshot) -> list[ItineraryItem]:
    """The plan's active flights and transfers, in the order they land (shared with the
    activity and transfer searches)."""
    return legs(active(plan.items))


def destination_zone(plan: PlanSnapshot) -> str | None:
    """The zone of the destination's main airport, or None when it has none we know."""
    place = plan.destination
    if place is None:
        return None
    codes = city_airports(place.iata, place.city, place.name)
    return airport_timezone(codes[0]) if codes else None


def stay_zone(plan: PlanSnapshot, nights: NightRange) -> str:
    """The zone the traveller is in by the end of check-in day: where the trip's legs have
    them, else the destination's."""
    destination = destination_zone(plan)
    ordered = active_legs(plan)
    if ordered:
        probe = datetime.combine(nights.check_in, time(23, 59), ZoneInfo(destination or "UTC"))
        zone = zone_at(probe, ordered)
        if zone is not None:
            return zone
    if destination is not None:
        return destination
    name = plan.destination.name if plan.destination else "the destination"
    raise SearchFailedError(
        f"I couldn't work out the local time in {name}. Go back to the basics and give the "
        "nearest city with an airport."
    )


def check_in_at(plan: PlanSnapshot, day: date, zone: str, listed: time | None) -> datetime:
    """The listing's check-in time on `day`, or an hour after a leg that lands later."""
    tz = ZoneInfo(zone)
    at = datetime.combine(day, listed or DEFAULT_CHECK_IN, tz)
    landings = [
        leg.end
        for leg in active_legs(plan)
        if leg.end.astimezone(tz).date() == day and leg.end > at
    ]
    if landings:
        latest = datetime.combine(day, _LATEST_CHECK_IN, tz)
        at = min(max(landings) + AFTER_ARRIVAL, latest)
    return at


def check_out_at(plan: PlanSnapshot, day: date, zone: str, listed: time | None) -> datetime:
    """The listing's check-out time on `day`, or three hours before a leg that leaves
    sooner after it."""
    tz = ZoneInfo(zone)
    at = datetime.combine(day, listed or DEFAULT_CHECK_OUT, tz)
    departures = [
        leg.start
        for leg in active_legs(plan)
        if leg.start.astimezone(tz).date() == day and leg.start < at + BEFORE_DEPARTURE
    ]
    if departures:
        earliest = datetime.combine(day, _EARLIEST_CHECK_OUT, tz)
        at = max(min(departures) - BEFORE_DEPARTURE, earliest)
    return at


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


def _day(moment: datetime) -> str:
    return f"{moment:%b} {moment.day} at {moment:%H:%M}"


def _count(count: int, one: str) -> str:
    return f"{count} {one}{'' if count == 1 else 's'}"


def _place(option: HotelOption, destination: Place) -> dict[str, JsonValue]:
    place = {
        "name": option.name[:_MAX_TITLE],
        "address": option.address,
        "city": destination.city or destination.name,
        "country": destination.country,
        "lat": option.lat,
        "lng": option.lng,
    }
    return {key: value for key, value in place.items() if value is not None}


def _kind(option: HotelOption, prefs: LodgingPreferences) -> LodgingKind:
    if option.kind == "vacation_rental":
        return "rental"
    # A hotel listed for a rental search is still a hotel.
    return "hotel" if prefs.kind == "rental" else prefs.kind


def _subtitle(option: HotelOption) -> str:
    parts = ["Vacation rental" if option.kind == "vacation_rental" else "Hotel"]
    if option.stars is not None:
        parts[0] = f"{option.stars}-star hotel"
    if option.rating is not None:
        reviews = f" ({option.reviews:,} reviews)" if option.reviews else ""
        parts.append(f"rated {option.rating:.1f}{reviews}")
    return " · ".join(parts)


def _add_lodging(
    option: HotelOption,
    prefs: LodgingPreferences,
    destination: Place,
    zone: str,
    start: datetime,
    end: datetime,
    total: Money | None,
    nightly: Money | None,
) -> CandidateChange:
    arguments: dict[str, JsonValue] = {
        "title": option.name[:_MAX_TITLE],
        # Local wall-clock times in `timezone`, as every item takes them.
        "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
        "timezone": zone,
        "place": _place(option, destination),
        "kind": _kind(option, prefs),
    }
    if nightly is not None:
        arguments["nightly_rate"] = nightly.model_dump(mode="json")
    if total is not None:
        # The search's own price for the whole stay.
        arguments["cost"] = {**total.model_dump(mode="json"), "basis": "exact"}
    if option.link and len(option.link) <= _MAX_URL:
        arguments["source_url"] = option.link
    summary = f"Add lodging “{arguments['title']}” from {_day(start)} to {_day(end)}"
    return CandidateChange(name="add_lodging", arguments=arguments, summary=summary)


def _candidate(
    number: int,
    option: HotelOption,
    plan: PlanSnapshot,
    prefs: LodgingPreferences,
    nights: NightRange,
    zone: str,
) -> Candidate:
    count = (nights.check_out - nights.check_in).days
    start = check_in_at(plan, nights.check_in, zone, option.check_in_time)
    end = check_out_at(plan, nights.check_out, zone, option.check_out_time)
    total, nightly = _total(option, count), _nightly(option, count)
    details = [f"{_count(count, 'night')}: in {_day(start)}, out {_day(end)}"]
    if nightly is not None:
        details.append(f"{format_money(nightly)} a night, total for everyone")
    if option.amenities:
        details.append(", ".join(option.amenities[:4]))
    assert plan.destination is not None  # checked by `hotel_query`
    return Candidate(
        id=str(number),
        title=option.name[:_MAX_TITLE],
        subtitle=_subtitle(option),
        details=details,
        warnings=misses(option, prefs, count),
        price=total,
        source_url=option.link if option.link and len(option.link) <= _MAX_URL else None,
        changes=[_add_lodging(option, prefs, plan.destination, zone, start, end, total, nightly)],
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def stays(ctx: SearchContext, plan: PlanSnapshot, action: RunSearch) -> list[Candidate]:
    """Places to stay for the action's nights, best first."""
    prefs = _preferences(plan)
    nights = _nights(action)
    query = hotel_query(plan, nights)
    zone = stay_zone(plan, nights)
    options = await _search(ctx, query)
    # Places already offered for these nights ("More options") make way for the next best.
    fresh = [
        option
        for option in rank_for(options, prefs, query.nights)
        if unseen(ctx, option.name[:_MAX_TITLE])
    ]
    ranked = fresh[:TOP_CANDIDATES]
    return [
        _candidate(number, option, plan, prefs, nights, zone)
        for number, option in enumerate(ranked, start=1)
    ]
