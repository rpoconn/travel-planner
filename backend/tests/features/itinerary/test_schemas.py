from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from features.itinerary.schemas import (
    ActivityItemCreate,
    FlightItem,
    FlightItemCreate,
    ItineraryItemCreate,
    LodgingItemCreate,
    TransferItemCreate,
)

_CREATE = TypeAdapter[ItineraryItemCreate](ItineraryItemCreate)

FLIGHT: dict[str, Any] = {
    "type": "flight",
    "title": "SFO → NRT",
    "start": "2027-05-01T11:00:00",
    "end": "2027-05-02T14:30:00",
    "timezone": "America/Los_Angeles",
    "end_timezone": "Asia/Tokyo",
    "carrier": "ANA",
    "flight_number": "NH7",
    "from_place": {"name": "San Francisco International Airport", "iata": "SFO"},
    "to_place": {"name": "Narita International Airport", "iata": "NRT"},
    "direction": "outbound",
}


# ---------------------------------------------------------------------------
# Times
# ---------------------------------------------------------------------------


def test_naive_times_are_wall_clock_in_their_timezones() -> None:
    item = _CREATE.validate_python(FLIGHT)

    assert item.start.utcoffset() == timedelta(hours=-7)  # PDT
    assert item.end.utcoffset() == timedelta(hours=9)  # JST
    assert item.start.hour == 11
    assert item.end.hour == 14
    assert item.end - item.start == timedelta(hours=11, minutes=30)


def test_aware_times_are_shown_in_the_item_timezone() -> None:
    item = _CREATE.validate_python(
        {**FLIGHT, "start": "2027-05-01T18:00:00Z", "end": "2027-05-02T05:30:00+00:00"}
    )

    assert item.start == datetime(2027, 5, 1, 18, 0, tzinfo=UTC)
    assert item.start.hour == 11  # same instant, displayed as Los Angeles time
    assert item.end.hour == 14


def test_end_timezone_defaults_to_timezone() -> None:
    item = ActivityItemCreate.model_validate(
        {
            "type": "activity",
            "title": "Senso-ji",
            "start": "2027-05-03T09:00:00",
            "end": "2027-05-03T11:00:00",
            "timezone": "Asia/Tokyo",
            "place": {"name": "Senso-ji"},
            "category": "sightseeing",
        }
    )

    assert item.end.utcoffset() == timedelta(hours=9)


def test_end_before_start_is_rejected() -> None:
    with pytest.raises(ValidationError, match="end must be at or after start"):
        _CREATE.validate_python({**FLIGHT, "end_timezone": None, "end": "2027-05-01T10:00:00"})


def test_cross_timezone_end_before_start_explains_the_comparison() -> None:
    # PHL 10:25 EST is 15:25 in Lisbon, so a same-day 13:20 arrival is before departure.
    flight = {
        **FLIGHT,
        "start": "2026-12-18T10:25:00",
        "timezone": "America/New_York",
        "end": "2026-12-18T13:20:00",
        "end_timezone": "Europe/Lisbon",
    }

    with pytest.raises(ValidationError) as excinfo:
        _CREATE.validate_python(flight)

    message = str(excinfo.value)
    assert "start 2026-12-18 10:25 EST is 2026-12-18 15:25 WET" in message
    assert "end is 2026-12-18 13:20 WET" in message
    assert "next day" in message


def test_flight_with_no_duration_is_rejected() -> None:
    # 10:00 EST and 00:00 JST next day are the same instant.
    flight = {
        **FLIGHT,
        "start": "2026-12-15T10:00:00",
        "timezone": "America/New_York",
        "end": "2026-12-16T00:00:00",
    }

    with pytest.raises(ValidationError) as excinfo:
        _CREATE.validate_python(flight)

    message = str(excinfo.value)
    assert "a flight must take at least 15 minute(s)" in message
    assert "departs 2026-12-15 10:00 EST (2026-12-16 00:00 JST)" in message
    assert "0 minute(s) later" in message


def test_transfer_with_no_duration_is_rejected() -> None:
    transfer = {
        "type": "transfer",
        "title": "Narita Express",
        "start": "2027-05-02T15:00:00",
        "end": "2027-05-02T15:00:00",
        "timezone": "Asia/Tokyo",
        "mode": "train",
        "from_place": {"name": "Narita", "iata": "NRT"},
        "to_place": {"name": "Tokyo Station"},
    }

    with pytest.raises(ValidationError, match="a transfer must take at least 1 minute"):
        _CREATE.validate_python(transfer)


@pytest.mark.parametrize("field", ["from_place", "to_place"])
def test_flight_rejects_a_city_code(field: str) -> None:
    with pytest.raises(ValidationError, match=r"TYO is a city code.*HND, NRT"):
        _CREATE.validate_python({**FLIGHT, field: {"name": "Tokyo", "iata": "TYO"}})


def test_flight_rejects_a_code_that_names_another_airport() -> None:
    haneda = {"name": "Tokyo Haneda Airport", "iata": "NRT"}

    message = r"'Tokyo Haneda Airport' is HND, not NRT \(NRT is Narita\)"

    with pytest.raises(ValidationError, match=message):
        _CREATE.validate_python({**FLIGHT, "to_place": haneda})


@pytest.mark.parametrize(
    "place",
    [
        {"name": "Tokyo Haneda Airport", "iata": "HND"},
        {"name": "Tokyo Airport", "iata": "NRT"},  # no airport-specific name to check
        {"name": "Narita International Airport (NRT)", "iata": "NRT"},
        {"name": "Paris Orly", "iata": "ORY"},
    ],
)
def test_flight_accepts_a_code_that_matches_its_name(place: dict[str, str]) -> None:
    assert _CREATE.validate_python({**FLIGHT, "to_place": place})


def test_airport_names_match_whole_words_only() -> None:
    # "orly" is inside "poorly" but this is not Orly airport.
    assert _CREATE.validate_python(
        {**FLIGHT, "to_place": {"name": "Poorly Named Field", "iata": "NRT"}}
    )


def test_transfer_rejects_a_code_that_names_another_airport() -> None:
    transfer = {
        "type": "transfer",
        "title": "Car from Haneda",
        "start": "2027-05-01T09:00:00",
        "end": "2027-05-01T11:00:00",
        "timezone": "Asia/Tokyo",
        "mode": "taxi",
        "from_place": {"name": "Tokyo Haneda Airport", "iata": "NRT"},
        "to_place": {"name": "Hotel Livemax Kayabacho"},
    }

    with pytest.raises(ValidationError, match="is HND, not NRT"):
        _CREATE.validate_python(transfer)


def test_transfer_accepts_a_car_service() -> None:
    transfer = {
        "type": "transfer",
        "title": "Car from Haneda",
        "start": "2027-05-01T09:00:00",
        "end": "2027-05-01T11:00:00",
        "timezone": "Asia/Tokyo",
        "mode": "car_service",
        "from_place": {"name": "Tokyo Haneda Airport", "iata": "HND"},
        "to_place": {"name": "Hotel Livemax Kayabacho"},
    }

    assert TransferItemCreate.model_validate(transfer).mode == "car_service"


def test_stored_flight_from_before_the_rules_still_loads() -> None:
    # Rows saved before these input rules existed must keep loading (and stay deletable).
    stored = {
        **FLIGHT,
        "id": "b48086f9-97b4-422d-9367-b59c4a3e2e06",
        "plan_id": "de2f666f-0e25-4f2d-91d2-7930c4561308",
        "created_by": "assistant",
        "end": "2027-05-02T03:00:00",
        "to_place": {"name": "TYO", "iata": "TYO"},
    }

    item = FlightItem.model_validate(stored)

    assert item.end == item.start


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Unknown IANA timezone"):
        _CREATE.validate_python({**FLIGHT, "timezone": "Mars/Olympus_Mons"})


# ---------------------------------------------------------------------------
# Union / type-specific rules
# ---------------------------------------------------------------------------


def test_discriminator_selects_the_item_type() -> None:
    lodging = _CREATE.validate_python(
        {
            "type": "lodging",
            "title": "Hotel Okura",
            "start": "2027-05-02T15:00:00",
            "end": "2027-05-06T11:00:00",
            "timezone": "Asia/Tokyo",
            "place": {"name": "Hotel Okura"},
        }
    )

    assert isinstance(_CREATE.validate_python(FLIGHT), FlightItemCreate)
    assert isinstance(lodging, LodgingItemCreate)
    assert lodging.kind == "hotel"
    assert lodging.status == "proposed"


def test_unknown_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _CREATE.validate_python({**FLIGHT, "type": "spaceship"})


def test_flights_need_airport_codes() -> None:
    with pytest.raises(ValidationError, match="IATA"):
        _CREATE.validate_python({**FLIGHT, "to_place": {"name": "Tokyo"}})
