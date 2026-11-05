"""Ways across a gap, for the ground stage: what kind of gap it is, the ways offered, how long
and how much, and when the ride happens. Rules only, so no providers at all."""

from datetime import date, timedelta
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from features.itinerary.schemas import Gap
from features.planning.schemas import RunSearch
from features.planning.search_types import SearchContext, SearchFailedError
from features.planning.transfer_search import (
    distance_km,
    duration,
    fit,
    gap_kind,
    modes,
    price,
    transfers,
)
from features.plans.schemas import PlanStage, SlotName
from shared.domain import Money, Place
from shared.flights.fake import FakeFlightProvider
from shared.hotels.fake import FakeHotelProvider
from shared.places.fake import FakePlaceProvider
from tests.features.itinerary.factories import (
    NRT,
    OKURA,
    TOKYO_TZ,
    activity,
    lodging,
)
from tests.features.plans.builders import outbound_flight, return_flight, snapshot
from tests.json_path import at

SHIBUYA = Place(name="Shibuya Crossing", city="Tokyo", lat=35.6595, lng=139.7005)
HARAJUKU = Place(name="Meiji Shrine", city="Tokyo", lat=35.6764, lng=139.6993)
KYOTO_INN = Place(name="Kyoto Inn", city="Kyoto")


def _ctx() -> SearchContext:
    return SearchContext(
        session=cast(AsyncSession, None),
        today=date(2026, 10, 15),
        flights=FakeFlightProvider(),
        hotels=FakeHotelProvider(),
        places=FakePlaceProvider(),
    )


def _action(gap: Gap | None) -> RunSearch:
    return RunSearch(
        slot=SlotName.TRANSFER,
        stage=PlanStage.GROUND,
        label="Find a way",
        gap=gap,
    )


# ---------------------------------------------------------------------------
# Kinds of gap and the ways offered
# ---------------------------------------------------------------------------


def test_gap_kinds() -> None:
    stay = lodging("2027-05-02T17:00", "2027-05-04T09:00")
    plan = snapshot([outbound_flight(), stay, return_flight()])
    home_out, landing, leaving, home_back = plan.gaps

    assert gap_kind(plan, home_out) == "home_airport"
    assert gap_kind(plan, landing) == "airport"
    assert gap_kind(plan, leaving) == "airport"
    assert gap_kind(plan, home_back) == "home_airport"


def test_stays_in_two_cities_and_around_town() -> None:
    tokyo = lodging("2027-05-01T15:00", "2027-05-02T10:00")
    kyoto = lodging("2027-05-02T15:00", "2027-05-04T10:00", place=KYOTO_INN, title="Kyoto Inn")
    temple = activity("2027-05-03T10:00", "2027-05-03T12:00")
    plan = snapshot([tokyo, kyoto, temple], origin=None)
    to_kyoto, to_temple, back = plan.gaps

    assert gap_kind(plan, to_kyoto) == "between_cities"
    assert gap_kind(plan, to_temple) == "around_town"
    assert gap_kind(plan, back) == "around_town"


def test_walking_leads_when_its_close_and_goes_when_its_far() -> None:
    assert modes("around_town", 1.9) == ["walk", "taxi", "rideshare", "metro"]
    assert modes("around_town", 8.0) == ["taxi", "rideshare", "metro"]
    assert modes("around_town", None) == ["taxi", "rideshare", "metro", "walk"]
    assert modes("airport", None)[-1] == "car_rental"


def test_distance() -> None:
    km = distance_km(SHIBUYA, HARAJUKU)

    assert km is not None and 1.8 < km < 2.0
    assert distance_km(SHIBUYA, OKURA) is None


# ---------------------------------------------------------------------------
# Time and price
# ---------------------------------------------------------------------------


def test_durations() -> None:
    assert duration("taxi", "airport", None) == timedelta(minutes=35)
    # 1.9 km on foot at 4.5 km/h is 25 minutes, rounded up to five.
    assert duration("walk", "around_town", 1.9) == timedelta(minutes=30)
    assert duration("taxi", "around_town", 1.0) == timedelta(minutes=10)


def test_prices_per_vehicle_and_per_person() -> None:
    plan = snapshot()  # two adults, no budget
    when = outbound_flight().start  # May 1

    assert price("taxi", "airport", None, plan, when) == Money(amount=Decimal(40))
    assert price("rideshare", "around_town", 10.0, plan, when).amount == 17  # (4 + 15) x 0.9
    assert price("train", "airport", None, plan, when).amount == 20  # 10 a person
    assert price("walk", "around_town", 1.0, plan, when).amount == 0
    # A rental car is for the rest of the trip: May 1-4 is four days.
    assert price("car_rental", "airport", None, plan, when).amount == 240


def test_prices_are_in_the_budgets_currency() -> None:
    plan = snapshot(budget={"amount": 200000, "currency": "JPY"})

    assert price("taxi", "airport", None, plan, outbound_flight().start).currency == "JPY"


# ---------------------------------------------------------------------------
# Fitting the ride into the gap
# ---------------------------------------------------------------------------


def test_after_landing_the_ride_leaves_at_once() -> None:
    stay = lodging("2027-05-02T17:00", "2027-05-04T09:00")
    plan = snapshot([outbound_flight(), stay])
    landing = plan.gaps[1]
    assert landing.leave_after is not None

    ride = fit(plan, landing, timedelta(minutes=35))

    assert ride.start == landing.leave_after
    assert ride.end == landing.leave_after + timedelta(minutes=35)
    assert ride.warning is None


def test_from_a_stay_the_ride_arrives_just_in_time() -> None:
    stay = lodging("2027-05-02T17:00", "2027-05-04T09:00")
    temple = activity("2027-05-03T10:00", "2027-05-03T12:00")
    plan = snapshot([stay, temple], origin=None)
    to_temple = plan.gaps[0]
    assert to_temple.before_item_id == temple.id

    ride = fit(plan, to_temple, timedelta(minutes=20))

    assert (ride.start, ride.end) == (temple.start - timedelta(minutes=20), temple.start)


def test_for_a_flight_the_ride_arrives_two_hours_early() -> None:
    plan = snapshot([outbound_flight()])
    home_out = plan.gaps[0]
    flight_start = outbound_flight().start

    ride = fit(plan, home_out, timedelta(minutes=45))

    assert ride.end == flight_start - timedelta(hours=2)
    assert ride.start == ride.end - timedelta(minutes=45)


def test_a_window_too_short_is_used_whole_with_a_warning() -> None:
    first = activity("2027-05-03T10:00", "2027-05-03T12:00")
    second = activity("2027-05-03T12:10", "2027-05-03T13:00", place=NRT, title="Narita tour")
    plan = snapshot([first, second], origin=None)
    (between,) = plan.gaps

    ride = fit(plan, between, timedelta(minutes=35))

    assert (ride.start, ride.end) == (first.end, second.start)
    assert ride.warning == "only 10 min to get there"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def test_candidates_carry_the_add_transfer_change() -> None:
    stay = lodging("2027-05-02T17:00", "2027-05-04T09:00")
    plan = snapshot([outbound_flight(), stay])
    landing = plan.gaps[1]

    taxi, *rest = await transfers(_ctx(), plan, _action(landing))

    assert [c.title for c in [taxi, *rest]] == [
        "Taxi to Hotel Okura",
        "Rideshare to Hotel Okura",
        "Airport shuttle to Hotel Okura",
        "Train to Hotel Okura",
        "Rental car to Hotel Okura",
    ]
    assert taxi.subtitle == "From Narita International Airport"
    assert taxi.details == ["May 2, 14:30-15:05", "About 35 min", "About 40.00 USD (estimate)"]
    (change,) = taxi.changes
    assert change.name == "add_transfer"
    assert change.arguments["mode"] == "taxi"
    assert change.arguments["timezone"] == TOKYO_TZ
    assert (change.arguments["start"], change.arguments["end"]) == (
        "2027-05-02T14:30:00",
        "2027-05-02T15:05:00",
    )
    assert at(change.arguments, "from_place", "iata") == "NRT"
    assert at(change.arguments, "cost", "basis") == "estimate"
    rental = rest[-1].changes[0].arguments
    assert at(rental, "pickup_location", "iata") == "NRT"


async def test_home_is_named_as_home() -> None:
    plan = snapshot([outbound_flight()])

    (taxi, *_) = await transfers(_ctx(), plan, _action(plan.gaps[0]))

    assert taxi.subtitle == "From Home (San Francisco)"
    assert taxi.title == "Taxi to San Francisco International Airport"


async def test_no_gap_no_search() -> None:
    with pytest.raises(SearchFailedError, match="no gap"):
        await transfers(_ctx(), snapshot(), _action(None))


def test_a_walk_around_town_is_free() -> None:
    temple = activity("2027-05-03T10:00", "2027-05-03T12:00", place=SHIBUYA, title="Shibuya")
    shrine = activity("2027-05-03T14:00", "2027-05-03T15:00", place=HARAJUKU, title="Shrine")
    plan = snapshot([temple, shrine], origin=None)

    (between,) = plan.gaps
    assert between.to_place == HARAJUKU
    assert modes(gap_kind(plan, between), distance_km(SHIBUYA, HARAJUKU))[0] == "walk"
    assert price("walk", "around_town", 1.9, plan, temple.end).amount == 0
