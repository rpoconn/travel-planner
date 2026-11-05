from datetime import datetime, timedelta
from decimal import Decimal

from features.itinerary.schemas import FlightItemCreate
from features.planning.flight_choice import (
    flight_arguments,
    format_minutes,
    local_time,
    rank_flights,
    stops_label,
)
from shared.domain import Money
from shared.flights.types import FlightCabin
from tests.json_path import at
from tests.shared.flights.builders import flight_option

# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_flights_rank_by_travel_time_then_price() -> None:
    slow_cheap = flight_option("PHL", "HND", "2027-05-01T08:00", 20 * 60, 700)
    fast_pricey = flight_option("PHL", "HND", "2027-05-01T09:00", 14 * 60, 1500)
    fast_cheap = flight_option("PHL", "HND", "2027-05-01T10:00", 14 * 60, 1100)

    ranked = rank_flights([slow_cheap, fast_pricey, fast_cheap])

    assert ranked == [fast_cheap, fast_pricey, slow_cheap]


def test_an_unpriced_flight_ranks_after_priced_ones_of_the_same_length() -> None:
    unpriced = flight_option("PHL", "HND", "2027-05-01T08:00", 14 * 60, None)
    priced = flight_option("PHL", "HND", "2027-05-01T09:00", 14 * 60, 2000)
    shorter_unpriced = flight_option("PHL", "HND", "2027-05-01T10:00", 13 * 60, None)

    assert rank_flights([unpriced, priced, shorter_unpriced]) == [
        shorter_unpriced,
        priced,
        unpriced,
    ]


# ---------------------------------------------------------------------------
# add_flight arguments
# ---------------------------------------------------------------------------


def test_flight_arguments_keep_each_airports_local_time_and_zone() -> None:
    option = flight_option("PHL", "HND", "2027-05-01T15:00", 14 * 60 + 30, 1200)

    arguments = flight_arguments(option, FlightCabin.ECONOMY, option.price)

    assert arguments["start"] == "2027-05-01T15:00:00-04:00"
    assert arguments["timezone"] == "America/New_York"
    assert arguments["end_timezone"] == "Asia/Tokyo"
    # The reported bug: the time change must not change how long the flight takes.
    flight = FlightItemCreate.model_validate(
        {**arguments, "type": "flight", "title": "PHL to HND", "direction": "outbound"}
    )
    assert flight.end - flight.start == timedelta(hours=14, minutes=30)
    assert flight.end == datetime.fromisoformat("2027-05-02T18:30:00+09:00")
    assert arguments["cost"] == {"amount": "1200", "currency": "USD", "basis": "exact"}


def test_a_connection_is_one_flight_with_its_layovers() -> None:
    option = flight_option("PHL", "HND", "2027-05-01T08:00", 18 * 60, 900, via="ORD")

    arguments = flight_arguments(option, FlightCabin.BUSINESS, None)

    assert (at(arguments, "from_place", "iata"), at(arguments, "to_place", "iata")) == (
        "PHL",
        "HND",
    )
    assert arguments["layovers"] == [{"name": "ORD Airport", "iata": "ORD"}]
    assert arguments["flight_number"] == "NH 100, NH 101"
    assert arguments["cabin"] == "business"
    assert arguments["notes"] == "Flight time 18h, 1 stop (ORD 0m)"
    assert "cost" not in arguments


# ---------------------------------------------------------------------------
# Describing
# ---------------------------------------------------------------------------


def test_local_times_and_stops() -> None:
    option = flight_option("PHL", "HND", "2027-05-01T15:00", 14 * 60 + 30, 1234)
    connection = flight_option("PHL", "HND", "2027-05-01T15:00", 16 * 60, 900, via="ORD")

    assert local_time(option.departure) == "PHL Sat May 1 15:00"
    assert local_time(option.arrival) == "HND Sun May 2 18:30"
    assert stops_label(option) == "nonstop"
    assert stops_label(connection) == "1 stop (ORD 0m)"


def test_format_minutes() -> None:
    assert [format_minutes(m) for m in (45, 60, 125)] == ["45m", "1h", "2h 5m"]


def test_flight_arguments_cost_uses_the_given_price() -> None:
    option = flight_option("PHL", "HND", "2027-05-01T15:00", 14 * 60, 1200)

    arguments = flight_arguments(option, FlightCabin.ECONOMY, Money(amount=Decimal(0)))

    assert at(arguments, "cost", "amount") == "0"
