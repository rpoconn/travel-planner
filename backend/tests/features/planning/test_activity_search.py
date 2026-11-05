"""Place searches for the activities stage: the query per interest, price estimates and what
counts as a misfit, ranking, and the candidates (with the change picking one makes). No
database: the provider is a fake."""

from datetime import date, time
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from features.itinerary.schemas import ActivityCategory
from features.planning.activity_search import (
    activities,
    budget_left,
    estimate,
    misses,
    place_query,
    rank,
)
from features.planning.schemas import RunSearch
from features.planning.search_types import SearchContext, SearchFailedError
from features.plans.schemas import (
    ActivityPreferences,
    PlanPreferences,
    PlanSnapshot,
    PlanStage,
    SlotName,
)
from shared.domain import Cost, Money, Place
from shared.flights.fake import FakeFlightProvider
from shared.hotels.fake import FakeHotelProvider
from shared.places.fake import FakePlaceProvider
from shared.places.types import OpeningHours, PlaceOption, PlaceSearchError
from tests.features.itinerary.factories import lodging
from tests.features.plans.builders import snapshot
from tests.json_path import at

TODAY = date(2026, 10, 15)
TOKYO = Place(name="Tokyo", city="Tokyo", country="Japan", lat=35.68, lng=139.69)
ACTION = RunSearch(
    slot=SlotName.ACTIVITIES,
    stage=PlanStage.ACTIVITIES,
    label="Find things to do",
)


def _plan(
    interests: list[ActivityCategory] | None = None, dietary: str = "", **overrides: object
) -> PlanSnapshot:
    prefs = ActivityPreferences(interests=interests or ["museum", "food"], dietary=dietary)
    fields: dict[str, object] = {"destination": TOKYO, **overrides}
    preferences = PlanPreferences(activities=prefs)
    # No items, passed positionally so mypy doesn't match the `object` overrides to `items`.
    return snapshot((), stage=PlanStage.ACTIVITIES, preferences=preferences, **fields)


def _ctx(places: FakePlaceProvider) -> SearchContext:
    # The activities runner never touches the database.
    return SearchContext(
        session=cast(AsyncSession, None),
        today=TODAY,
        flights=FakeFlightProvider(),
        hotels=FakeHotelProvider(),
        places=places,
    )


def _place(name: str, rating: float | None = 4.5, **fields: object) -> PlaceOption:
    return PlaceOption.model_validate({"name": name, "rating": rating, **fields})


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def test_each_interest_searches_near_the_destination() -> None:
    query = place_query(_plan(), "museum")

    assert (query.what, query.near, query.lat, query.lng) == (
        "museums",
        "Tokyo, Japan",
        35.68,
        139.69,
    )


def test_restaurants_carry_the_dietary_needs() -> None:
    plan = _plan(dietary="vegetarian")

    assert place_query(plan, "food").what == "vegetarian restaurants"
    assert place_query(plan, "museum").what == "museums"


# ---------------------------------------------------------------------------
# Prices and fit
# ---------------------------------------------------------------------------


def test_estimates_are_per_person_times_the_party() -> None:
    plan = _plan()  # two adults

    assert estimate(_place("Sushi", price_level=3), "food", plan) == Money(amount=Decimal(140))
    # Without a price level, a typical price for the kind of place.
    assert estimate(_place("Park"), "outdoors", plan).amount == 0
    assert estimate(_place("Museum"), "museum", plan).amount == 30


def test_estimates_are_in_the_budgets_currency() -> None:
    plan = _plan(budget={"amount": 3000, "currency": "JPY"})

    assert estimate(_place("Museum"), "museum", plan).currency == "JPY"


def test_misfits_say_why() -> None:
    limits = [{"category": "food", "max_per_person": {"amount": 50, "currency": "USD"}}]
    plan = _plan(limits=limits)
    pricey = _place("Kaiseki", rating=3.8, price_level=4)

    assert misses(pricey, "food", estimate(pricey, "food", plan), plan) == [
        "rated 3.8",
        "about 120.00 USD a person, over the food limit of 50.00 USD",
    ]


def test_what_the_budget_has_left() -> None:
    stay = lodging("2027-05-02T15:00", "2027-05-04T11:00").model_copy(
        update={"cost": Cost(amount=Decimal(900))}
    )
    plan = snapshot(
        [stay],
        destination=TOKYO,
        budget={"amount": 1000, "currency": "USD"},
        preferences=PlanPreferences(activities=ActivityPreferences(interests=["food"])),
    )
    dinner = _place("Sushi", price_level=4)

    assert budget_left(plan) == Money(amount=Decimal(100))
    assert misses(dinner, "food", estimate(dinner, "food", plan), plan) == [
        "about 240.00 USD, more than the budget has left"
    ]
    assert budget_left(_plan()) is None


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_interests_take_turns_and_misfits_go_last() -> None:
    museums = [_place("Small Museum", 4.2), _place("Big Museum", 4.8), _place("Dull", 3.1)]
    food = [_place("Ramen", 4.6), _place("Big Museum", 4.9)]  # the museum's café

    ranked = rank(_plan(), ["museum", "food"], [museums, food])

    assert [(found.option.name, found.category) for found in ranked] == [
        ("Big Museum", "museum"),
        ("Ramen", "food"),
        ("Small Museum", "museum"),
        ("Dull", "museum"),
    ]
    assert ranked[-1].warnings == ["rated 3.1"]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def test_candidates_carry_their_window_and_the_add_activity_change() -> None:
    places = FakePlaceProvider()

    first, *_ = await activities(_ctx(places), _plan(), ACTION)

    assert first.title == "Example Art Museum"
    assert first.subtitle == "Art museum · rated 4.7 (250 reviews)"
    assert first.details[:2] == [
        "Sat May 1, 10:00-12:00",
        "About 30.00 USD for 2 people (estimate)",
    ]
    assert first.price is None
    (change,) = first.changes
    assert change.name == "add_activity"
    assert change.arguments["start"] == "2027-05-01T10:00:00"
    assert change.arguments["timezone"] == "Asia/Tokyo"
    assert change.arguments["category"] == "museum"
    assert change.arguments["cost"] == {"amount": "30", "currency": "USD", "basis": "estimate"}
    assert at(change.arguments, "place", "city") == "Tokyo"
    assert change.arguments["source_url"] == first.source_url


async def test_tours_need_booking() -> None:
    (tour, *_) = await activities(_ctx(FakePlaceProvider()), _plan(["tour"]), ACTION)

    assert tour.changes[0].arguments["booking_required"] is True


async def test_no_preferences_no_search() -> None:
    plan = snapshot(stage=PlanStage.ACTIVITIES, destination=TOKYO)

    with pytest.raises(SearchFailedError, match="what you like to do"):
        await activities(_ctx(FakePlaceProvider()), plan, ACTION)


async def test_every_search_failing_fails_the_search() -> None:
    places = FakePlaceProvider([PlaceSearchError("Place search timed out.")] * 2)

    with pytest.raises(SearchFailedError, match="timed out"):
        await activities(_ctx(places), _plan(), ACTION)


async def test_places_never_open_while_the_trip_is_free_are_left_out() -> None:
    # Open on Mondays from 7 to 8 only: before any of the day's windows.
    early = [OpeningHours(weekday=0, opens=time(7), closes=time(8))]
    places = FakePlaceProvider([[_place("Dawn Market", hours=early)]])

    with pytest.raises(SearchFailedError, match="no free time"):
        await activities(_ctx(places), _plan(["museum"]), ACTION)
