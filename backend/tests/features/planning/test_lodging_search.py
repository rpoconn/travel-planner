"""Hotel searches for the lodging stage: the query a plan makes, ranking by preferences, the
stay's zone and times, and the candidates (with the change picking one makes). No
database: the provider is a fake."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from features.planning.lodging_search import (
    check_in_at,
    check_out_at,
    hotel_query,
    misses,
    rank_for,
    stay_zone,
    stays,
)
from features.planning.schemas import NightRange, RunSearch
from features.planning.search_types import SearchContext, SearchFailedError
from features.plans.schemas import (
    LodgingPreferences,
    PlanPreferences,
    PlanSnapshot,
    PlanStage,
    SlotName,
)
from shared.domain import Money, Place
from shared.flights.fake import FakeFlightProvider
from shared.hotels.fake import FakeHotelProvider
from shared.hotels.types import HotelOption, HotelSearchError
from shared.places.fake import FakePlaceProvider
from tests.features.itinerary.factories import NRT, SFO, TOKYO_TZ, flight
from tests.features.plans.builders import outbound_flight, snapshot

TODAY = date(2026, 10, 15)
TOKYO = Place(name="Tokyo", city="Tokyo", country="Japan")
# The builders' trip: May 1-4 2027, two adults. The flight out lands May 2, so the night
# of May 1 is spent on the plane.
NIGHTS = NightRange(check_in=date(2027, 5, 2), check_out=date(2027, 5, 4))
MIDRANGE = LodgingPreferences(hotel_class="midrange")


def _plan(prefs: LodgingPreferences | None = MIDRANGE, **overrides: Any) -> PlanSnapshot:
    preferences = PlanPreferences(lodging=prefs)
    fields: dict[str, Any] = {"destination": TOKYO, **overrides}
    return snapshot(stage=PlanStage.LODGING, preferences=preferences, **fields)


def _ctx(hotels: FakeHotelProvider) -> SearchContext:
    # The lodging runner never touches the database.
    return SearchContext(
        session=cast(AsyncSession, None),
        today=TODAY,
        flights=FakeFlightProvider(),
        hotels=hotels,
        places=FakePlaceProvider(),
    )


def _action(nights: NightRange | None = NIGHTS) -> RunSearch:
    return RunSearch(
        slot=SlotName.LODGING_STAY,
        stage=PlanStage.LODGING,
        label="Find a place to stay",
        nights=nights,
    )


def _hotel(
    name: str, nightly: int, *, rating: float | None = 4.0, stars: int | None = 3, **fields: Any
) -> HotelOption:
    return HotelOption(
        name=name,
        nightly_rate=Money(amount=Decimal(nightly)),
        total_rate=Money(amount=Decimal(nightly * 2)),
        rating=rating,
        stars=stars,
        **fields,
    )


def _tokyo(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), ZoneInfo(TOKYO_TZ))


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def test_the_query_is_the_nights_the_party_and_the_class() -> None:
    query = hotel_query(_plan(travelers={"adults": 2, "children": 1}), NIGHTS)

    assert query.location == "Tokyo, Japan"
    assert (query.check_in, query.check_out, query.nights) == (NIGHTS.check_in, NIGHTS.check_out, 2)
    assert (query.adults, query.children) == (2, 1)
    assert query.stars == (3,)
    assert (query.vacation_rentals, query.max_nightly, query.currency) == (False, None, "USD")


def test_the_neighborhood_and_the_kind_shape_the_location() -> None:
    hostel = LodgingPreferences(hotel_class="budget", kind="hostel", neighborhood="Asakusa")

    query = hotel_query(_plan(hostel), NIGHTS)

    assert query.location == "hostels in Asakusa, Tokyo, Japan"
    # Hostels have no stars to filter by.
    assert query.stars == ()


def test_a_rental_searches_vacation_rentals() -> None:
    query = hotel_query(_plan(LodgingPreferences(hotel_class="upscale", kind="rental")), NIGHTS)

    assert query.vacation_rentals is True
    assert query.stars == ()


def test_the_nightly_budget_caps_the_price_rounded_up_in_its_currency() -> None:
    nightly = Money(amount=Decimal("120.50"), currency="EUR")
    prefs = LodgingPreferences(hotel_class="midrange", nightly_budget=nightly)

    query = hotel_query(_plan(prefs, budget={"amount": 5000, "currency": "USD"}), NIGHTS)

    assert (query.max_nightly, query.currency) == (121, "EUR")


def test_without_a_nightly_budget_the_trip_budgets_currency_is_used() -> None:
    query = hotel_query(_plan(budget={"amount": 5000, "currency": "JPY"}), NIGHTS)

    assert query.currency == "JPY"


def test_no_preferences_means_no_search() -> None:
    with pytest.raises(SearchFailedError, match="what kind of place"):
        hotel_query(_plan(None), NIGHTS)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_misses_name_each_preference_an_option_falls_short_of() -> None:
    limit = Money(amount=Decimal(150))
    prefs = LodgingPreferences(hotel_class="midrange", nightly_budget=limit)

    assert misses(_hotel("Fits", 140), prefs, 2) == []
    assert misses(_hotel("Pricey", 200, stars=4), prefs, 2) == [
        "4-star, not midrange",
        "200.00 USD a night, over 150.00 USD",
    ]
    assert misses(_hotel("Flat", 100, stars=None, kind="vacation_rental"), prefs, 2) == [
        "a vacation rental, not a hotel"
    ]
    rental = LodgingPreferences(hotel_class="midrange", kind="rental")
    assert misses(_hotel("Hotel", 100), rental, 2) == ["a hotel, not a rental"]


def test_a_nightly_rate_is_worked_out_from_the_total_when_missing() -> None:
    prefs = LodgingPreferences(hotel_class="midrange", nightly_budget=Money(amount=Decimal(100)))
    option = HotelOption(name="Total only", total_rate=Money(amount=Decimal(300)), stars=3)

    assert misses(option, prefs, 2) == ["150.00 USD a night, over 100.00 USD"]


def test_options_that_fit_come_first_then_best_rated_then_cheapest() -> None:
    options = [
        _hotel("Wrong class", 100, rating=4.9, stars=5),
        _hotel("Cheap", 100, rating=4.2),
        _hotel("Dear", 180, rating=4.2),
        _hotel("Loved", 150, rating=4.7),
        _hotel("Unrated", 90, rating=None),
    ]

    ranked = rank_for(options, MIDRANGE, 2)

    assert [o.name for o in ranked] == ["Loved", "Cheap", "Dear", "Unrated", "Wrong class"]


# ---------------------------------------------------------------------------
# Zone and times
# ---------------------------------------------------------------------------


def test_without_legs_the_stay_is_in_the_destinations_zone() -> None:
    assert stay_zone(_plan(), NIGHTS) == TOKYO_TZ


def test_the_stay_is_where_the_flight_out_lands() -> None:
    # A destination with no known airport: the flight says where the traveller is.
    plan = _plan(destination=Place(name="Hakone"), items=[outbound_flight()])

    assert stay_zone(plan, NIGHTS) == TOKYO_TZ


def test_no_zone_to_go_by_fails_the_search() -> None:
    with pytest.raises(SearchFailedError, match="local time in Hakone"):
        stay_zone(_plan(destination=Place(name="Hakone")), NIGHTS)


def test_check_in_waits_for_a_late_landing() -> None:
    late = flight("2027-05-01T14:00", "2027-05-02T19:20")  # lands 19:20 Tokyo time
    plan = _plan(items=[late])

    assert check_in_at(plan, NIGHTS.check_in, TOKYO_TZ, None) == _tokyo(NIGHTS.check_in, 20, 20)
    # An early landing leaves the listed time alone.
    early = _plan(items=[outbound_flight()])  # lands 14:30
    listed = time(16, 0)
    assert check_in_at(early, NIGHTS.check_in, TOKYO_TZ, listed) == _tokyo(NIGHTS.check_in, 16)


def test_check_in_is_never_after_eleven_at_night() -> None:
    plan = _plan(items=[flight("2027-05-01T18:00", "2027-05-02T23:40")])

    assert check_in_at(plan, NIGHTS.check_in, TOKYO_TZ, None) == _tokyo(NIGHTS.check_in, 23)


def test_check_out_leaves_three_hours_before_an_early_flight() -> None:
    home = flight(
        "2027-05-04T12:30",
        "2027-05-04T06:00",
        from_place=NRT,
        to_place=SFO,
        timezone=TOKYO_TZ,
        end_timezone="America/Los_Angeles",
    )
    plan = _plan(items=[home])

    assert check_out_at(plan, NIGHTS.check_out, TOKYO_TZ, None) == _tokyo(NIGHTS.check_out, 9, 30)
    # A flight long after check-out doesn't move it.
    later = home.model_copy(update={"start": _tokyo(NIGHTS.check_out, 18)})
    assert check_out_at(_plan(items=[later]), NIGHTS.check_out, TOKYO_TZ, None) == _tokyo(
        NIGHTS.check_out, 11
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def test_candidates_carry_the_add_lodging_change() -> None:
    hotels = FakeHotelProvider()

    first, second = await stays(_ctx(hotels), _plan(items=[outbound_flight()]), _action())

    assert (first.id, first.title) == ("1", "Example Riverside Hotel")
    assert first.subtitle == "3-star hotel · rated 4.5 (300 reviews)"
    assert first.details[0] == "2 nights: in May 2 at 15:00, out May 4 at 11:00"
    assert first.warnings == []
    assert first.price == Money(amount=Decimal(280))
    [change] = first.changes
    assert change.name == "add_lodging"
    assert change.arguments == {
        "title": "Example Riverside Hotel",
        "start": "2027-05-02T15:00:00",
        "end": "2027-05-04T11:00:00",
        "timezone": TOKYO_TZ,
        "place": {
            "name": "Example Riverside Hotel",
            "address": "Tokyo, Japan",
            "city": "Tokyo",
            "country": "Japan",
        },
        "kind": "hotel",
        "nightly_rate": {"amount": "140", "currency": "USD"},
        "cost": {"amount": "280", "currency": "USD", "basis": "exact"},
    }
    assert second.title == "Example Midtown Hotel"


async def test_only_the_top_five_are_offered() -> None:
    hotels = FakeHotelProvider([[_hotel(f"Hotel {n}", 100 + n) for n in range(8)]])

    candidates = await stays(_ctx(hotels), _plan(), _action())

    assert [c.id for c in candidates] == ["1", "2", "3", "4", "5"]


async def test_a_provider_failure_fails_the_search() -> None:
    hotels = FakeHotelProvider([HotelSearchError("Hotel search timed out.")])

    with pytest.raises(SearchFailedError, match="Hotel search timed out"):
        await stays(_ctx(hotels), _plan(), _action())


async def test_a_search_without_nights_fails() -> None:
    with pytest.raises(SearchFailedError, match="no nights"):
        await stays(_ctx(FakeHotelProvider()), _plan(), _action(nights=None))
