from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from features.itinerary.schemas import (
    ActivityItemCreate,
    FlightItemCreate,
    ItineraryItemCreate,
    LodgingItemCreate,
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
