"""Flight searches for the flights stage: the query a plan makes, ranking by preferences, and
the candidates (with the changes picking them makes). No database: the provider is a fake."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from features.planning.flight_search import (
    flight_query,
    misses,
    outbound_flights,
    rank_for,
    return_flights,
)
from features.planning.schemas import RunSearch
from features.planning.search_types import SearchContext, SearchFailedError
from features.plans.schemas import (
    FlightPreferences,
    PlanPreferences,
    PlanSnapshot,
    PlanStage,
    SlotName,
)
from shared.domain import Money, Place, Travelers
from shared.flights.airports import city_airports
from shared.flights.fake import FakeFlightProvider
from shared.flights.types import (
    FlightLayover,
    FlightOption,
    FlightSearchError,
    FlightSegment,
    FlightStop,
)
from shared.hotels.fake import FakeHotelProvider
from shared.places.fake import FakePlaceProvider
from tests.features.plans.builders import outbound_flight, snapshot

TODAY = date(2026, 10, 15)

OUTBOUND = RunSearch(
    kind="flights_outbound",
    slot=SlotName.FLIGHT_OUTBOUND,
    stage=PlanStage.FLIGHTS,
    label="Find flights out",
)
RETURN = RunSearch(
    kind="flights_return",
    slot=SlotName.FLIGHT_RETURN,
    stage=PlanStage.FLIGHTS,
    label="Find flights home",
)


def _plan(prefs: FlightPreferences | None = None, **overrides: Any) -> PlanSnapshot:
    preferences = PlanPreferences(flights=prefs or FlightPreferences())
    return snapshot(stage=PlanStage.FLIGHTS, preferences=preferences, **overrides)


def _ctx(flights: FakeFlightProvider) -> SearchContext:
    # The flight runners never touch the database.
    return SearchContext(
        session=cast(AsyncSession, None),
        today=TODAY,
        flights=flights,
        hotels=FakeHotelProvider(),
        places=FakePlaceProvider(),
    )


def _option(hour: int, minutes: int, price: int, stops: int = 0) -> FlightOption:
    departs = datetime(2027, 5, 1, hour, tzinfo=UTC)
    arrives = departs + timedelta(minutes=minutes)
    return FlightOption(
        segments=[
            FlightSegment(
                departure=FlightStop(iata="SFO", name="SFO", time=departs, timezone="UTC"),
                arrival=FlightStop(iata="NRT", name="NRT", time=arrives, timezone="UTC"),
                airline="Example Air",
                duration_minutes=minutes,
            )
        ],
        layovers=[FlightLayover(iata="ICN", name="Incheon", duration_minutes=90)] * stops,
        total_duration_minutes=minutes,
        price=Money(amount=Decimal(price)),
    )


# ---------------------------------------------------------------------------
# Airports and the query
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("names", "airports"),
    [
        (("Lisbon",), ("LIS",)),
        (("Tokyo",), ("HND", "NRT")),
        # Small fields share the name; the international airport is the one people use.
        (("Philadelphia",), ("PHL",)),
        # An airport code works as a name too, and a city wins over a street address.
        (("NRT",), ("NRT",)),
        ((None, "San Francisco", "12 Pine St"), ("SFO", "OAK")),
        (("Atlantis",), ()),
    ],
)
def test_city_airports(names: tuple[str | None, ...], airports: tuple[str, ...]) -> None:
    assert city_airports(*names) == airports


def test_the_query_is_a_round_trip_for_everyone_in_the_chosen_cabin() -> None:
    plan = _plan(
        FlightPreferences(cabin="business"),
        travelers=Travelers(adults=2, children=1),
        budget=Money(amount=Decimal(9000), currency="EUR"),
    )

    query = flight_query(plan)

    assert (query.origin, query.destination) == (("SFO", "OAK"), ("HND", "NRT"))
    assert (query.depart_date, query.return_date) == (date(2027, 5, 1), date(2027, 5, 4))
    assert (query.cabin, query.adults, query.currency) == ("business", 3, "EUR")


def test_the_one_way_query_home_leaves_on_the_last_day() -> None:
    query = flight_query(_plan(), one_way_home=True)

    assert (query.origin, query.destination) == (("HND", "NRT"), ("SFO", "OAK"))
    assert (query.depart_date, query.return_date) == (date(2027, 5, 4), None)


def test_a_place_without_an_airport_says_what_to_do() -> None:
    plan = _plan(destination=Place(name="Atlantis"))

    with pytest.raises(SearchFailedError, match="couldn't find an airport for Atlantis"):
        flight_query(plan)


def test_too_many_travelers_for_the_search() -> None:
    with pytest.raises(SearchFailedError, match="at most 9 travelers"):
        flight_query(_plan(travelers=Travelers(adults=10)))


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


def test_misses_names_each_preference_an_option_breaks() -> None:
    prefs = FlightPreferences(max_stops=0, depart_window="morning")

    assert misses(_option(9, 600, 900), prefs) == []
    assert misses(_option(19, 600, 900, stops=1), prefs) == [
        "1 stop (ICN 1h 30m), not nonstop",
        "leaves at 19:00, not in the morning",
    ]


def test_options_that_fit_come_first_then_shortest_then_cheapest() -> None:
    evening_short = _option(19, 500, 900)
    morning_long = _option(8, 700, 700)
    morning_short_dear = _option(9, 600, 1200)
    morning_short_cheap = _option(10, 600, 800)

    ranked = rank_for(
        [evening_short, morning_long, morning_short_dear, morning_short_cheap],
        FlightPreferences(depart_window="morning"),
    )

    assert ranked == [morning_short_cheap, morning_short_dear, morning_long, evening_short]


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


async def test_flights_out_carry_an_add_flight_and_the_token_for_the_flights_home() -> None:
    flights = FakeFlightProvider()

    candidates = await outbound_flights(_ctx(flights), _plan(), OUTBOUND)

    assert [c.id for c in candidates] == ["1", "2"]
    first = candidates[0]
    assert first.title == "Example Air EX 101"
    assert first.subtitle.startswith("SFO Sat May 1 09:00 → HND")
    assert first.price == Money(amount=Decimal(1800))
    assert first.follow_up_token == "fake-departure-0"
    [change] = first.changes
    assert change.name == "add_flight"
    assert change.arguments["direction"] == "outbound"
    assert change.arguments["cost"] == {"amount": "1800", "currency": "USD", "basis": "exact"}
    assert change.arguments["start"].startswith("2027-05-01T09:00")


async def test_flights_out_that_miss_a_preference_say_so() -> None:
    plan = _plan(FlightPreferences(depart_window="morning"))

    candidates = await outbound_flights(_ctx(FakeFlightProvider()), plan, OUTBOUND)

    assert candidates[0].warnings == []
    assert candidates[1].warnings == ["leaves at 18:30, not in the morning"]


async def test_flights_home_move_the_round_trip_fare_onto_the_flight_out() -> None:
    flights = FakeFlightProvider()
    out = outbound_flight()
    plan = _plan(FlightPreferences(outbound_token="fake-departure-1"), items=[out])

    [candidate] = await return_flights(_ctx(flights), plan, RETURN)

    assert flights.searches[0].departure_token == "fake-departure-1"
    update, add = candidate.changes
    assert update.name == "update_item"
    assert update.arguments == {
        "item_id": str(out.id),
        "changes": {"cost": {"amount": "2400", "currency": "USD", "basis": "exact"}},
    }
    assert add.name == "add_flight"
    assert add.arguments["direction"] == "return"
    assert add.arguments["cost"]["amount"] == "0"


async def test_without_a_token_the_flights_home_are_searched_one_way() -> None:
    flights = FakeFlightProvider()

    candidates = await return_flights(_ctx(flights), _plan(), RETURN)

    search = flights.searches[0]
    assert search.departure_token is None and search.query.return_date is None
    assert search.query.origin == ("HND", "NRT")
    [change] = candidates[0].changes
    assert change.arguments["direction"] == "return"
    assert "One-way fare for everyone" in candidates[0].details


async def test_a_provider_failure_is_a_failed_search() -> None:
    flights = FakeFlightProvider([FlightSearchError("Flight search is down")])

    with pytest.raises(SearchFailedError, match="Flight search is down"):
        await outbound_flights(_ctx(flights), _plan(), OUTBOUND)
