"""Ways across a gap, for the ground stage: what kind of gap it is, the ways offered, how long
and how much, and when the ride happens. Rules only, so no providers at all."""

from datetime import date, timedelta
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from features.itinerary.schemas import Gap, ItineraryItem, TransferItem, TransferMode
from features.planning.schemas import RunSearch
from features.planning.search_types import SearchContext, SearchFailedError
from features.planning.transfer_search import (
    car_in_hand,
    distance_km,
    duration,
    fit,
    gap_kind,
    modes,
    price,
    transfers,
    ways,
)
from features.plans.schemas import PlanStage, SlotName
from shared.domain import Money, Place
from shared.flights.fake import FakeFlightProvider
from shared.hotels.fake import FakeHotelProvider
from shared.places.fake import FakePlaceProvider
from tests.features.itinerary.factories import (
    HOME,
    NRT,
    OKURA,
    SENSOJI,
    TOKYO_TZ,
    activity,
    lodging,
    transfer,
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


# ---------------------------------------------------------------------------
# Cars: the traveller's own, or a rental picked up earlier
# ---------------------------------------------------------------------------


def _car_ride(mode: TransferMode, start: str, end: str, from_place: Place = NRT) -> TransferItem:
    ride = transfer(start, end, from_place=from_place)
    return ride.model_copy(update={"mode": mode, "title": "Car"})


def _driving_trip() -> list[ItineraryItem]:
    """No flights: drive from home in the own car, stay, see a temple."""
    return [
        _car_ride("own_car", "2027-05-02T12:00", "2027-05-02T16:00", from_place=HOME),
        lodging("2027-05-02T17:00", "2027-05-04T09:00"),
        activity("2027-05-03T10:00", "2027-05-03T12:00", place=SENSOJI, title="Senso-ji"),
    ]


def _rental_trip() -> list[ItineraryItem]:
    """Fly in, pick up a rental at the airport, stay, fly home."""
    return [
        outbound_flight(),
        _car_ride("car_rental", "2027-05-02T14:30", "2027-05-02T15:30"),
        lodging("2027-05-02T17:00", "2027-05-04T09:00"),
        return_flight(),
    ]


def test_leaving_home_without_flights_the_own_car_leads() -> None:
    plan = snapshot([lodging("2027-05-02T17:00", "2027-05-04T09:00")], flights_skipped=True)
    leaving = plan.gaps[0]

    assert ways(plan, leaving, gap_kind(plan, leaving), None) == [
        "own_car",
        "train",
        "bus",
        "car_rental",
        "taxi",
    ]


def test_flying_the_own_car_is_never_offered() -> None:
    stay = lodging("2027-05-02T17:00", "2027-05-04T09:00")
    plan = snapshot([outbound_flight(), stay, return_flight()])

    for gap in plan.gaps:
        assert "own_car" not in ways(plan, gap, gap_kind(plan, gap), None)


def test_the_own_car_leads_once_it_is_on_the_trip() -> None:
    plan = snapshot(_driving_trip(), flights_skipped=True)
    to_temple, back_to_stay, home_back = plan.gaps

    for gap in (to_temple, back_to_stay, home_back):
        assert car_in_hand(plan, gap) == "own_car"
        offered = ways(plan, gap, gap_kind(plan, gap), None)
        assert offered[0] == "own_car"
        assert "car_rental" not in offered  # one car at a time


async def test_driving_the_own_car_costs_fuel_and_parking() -> None:
    plan = snapshot(_driving_trip(), flights_skipped=True)
    to_temple = plan.gaps[0]

    own, *_ = await transfers(_ctx(), plan, _action(to_temple))

    assert own.title == "Your car to Senso-ji"
    assert own.details[1:] == ["About 15 min", "About 8.00 USD (estimate)"]
    assert own.changes[0].arguments["mode"] == "own_car"
    assert "pickup_location" not in own.changes[0].arguments


async def test_a_rental_car_picked_up_after_landing_is_kept_until_the_flight_home() -> None:
    plan = snapshot(_rental_trip())
    home_out, to_airport, home_back = plan.gaps

    assert car_in_hand(plan, home_out) is None
    assert car_in_hand(plan, to_airport) == "car_rental"
    assert car_in_hand(plan, home_back) is None  # left behind at the airport
    offered = ways(plan, to_airport, gap_kind(plan, to_airport), None)
    assert offered == ["car_rental", "taxi", "rideshare", "shuttle", "train"]

    rental, *_ = await transfers(_ctx(), plan, _action(to_airport))

    assert rental.title == "Rental car to Narita International Airport"
    # Fuel and parking, not another four days of rental; no pickup time either.
    assert rental.details[1:] == [
        "About 35 min",
        "About 10.00 USD (estimate)",
        "The rental car you already have, returned here",
    ]
    arguments = rental.changes[0].arguments
    assert "pickup_location" not in arguments
    assert at(arguments, "dropoff_location", "iata") == "NRT"


async def test_a_new_rental_is_picked_up_and_kept() -> None:
    stay = lodging("2027-05-02T17:00", "2027-05-04T09:00")
    plan = snapshot([outbound_flight(), stay])

    *_, rental = await transfers(_ctx(), plan, _action(plan.gaps[1]))

    assert rental.details[-1] == "Picked up here, kept for the rest of the trip"
    assert "dropoff_location" not in rental.changes[0].arguments


def test_driving_a_car_in_hand_skips_the_rental_pickup() -> None:
    plan = snapshot()
    when = outbound_flight().start

    assert duration("car_rental", "airport", 20.0) == timedelta(minutes=55)  # 24 + 30 pickup
    assert duration("car_rental", "airport", 20.0, driving=True) == timedelta(minutes=35)
    assert duration("own_car", "airport", 20.0) == timedelta(minutes=35)
    assert price("car_rental", "airport", 20.0, plan, when, driving=True).amount == 7  # 2 + 5
    assert price("own_car", "between_cities", None, plan, when).amount == 40
