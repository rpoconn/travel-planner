"""Builders for validated itinerary items, for timeline and schema tests.

Times are ISO strings without offsets, i.e. wall-clock time in the item's timezone(s).
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from features.itinerary.schemas import (
    ActivityItem,
    FlightItem,
    ItemStatus,
    LodgingItem,
    TransferItem,
)
from shared.domain import Place

PLAN_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")

SFO = Place(name="San Francisco International Airport", iata="SFO", city="San Francisco")
LAX = Place(name="Los Angeles International Airport", iata="LAX", city="Los Angeles")
JFK = Place(name="John F. Kennedy International Airport", iata="JFK", city="New York")
NRT = Place(name="Narita International Airport", iata="NRT", city="Tokyo")
SYD = Place(name="Sydney Airport", iata="SYD", city="Sydney")
OKURA = Place(name="Hotel Okura", city="Tokyo")
SENSOJI = Place(name="Senso-ji", city="Tokyo")
TSUKIJI = Place(name="Tsukiji Outer Market", city="Tokyo")

TOKYO_TZ = "Asia/Tokyo"
LA_TZ = "America/Los_Angeles"


def _common(title: str, start: str, end: str, status: ItemStatus) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "plan_id": PLAN_ID,
        "created_by": "user",
        "title": title,
        "start": datetime.fromisoformat(start),
        "end": datetime.fromisoformat(end),
        "status": status,
    }


def flight(
    start: str,
    end: str,
    *,
    from_place: Place = SFO,
    to_place: Place = NRT,
    timezone: str = LA_TZ,
    end_timezone: str | None = TOKYO_TZ,
    title: str | None = None,
    status: ItemStatus = "proposed",
) -> FlightItem:
    return FlightItem.model_validate(
        {
            **_common(title or f"{from_place.iata} → {to_place.iata}", start, end, status),
            "type": "flight",
            "timezone": timezone,
            "end_timezone": end_timezone,
            "carrier": "ANA",
            "from_place": from_place,
            "to_place": to_place,
            "direction": "outbound",
        }
    )


def lodging(
    start: str,
    end: str,
    *,
    place: Place = OKURA,
    title: str = "Hotel Okura",
    status: ItemStatus = "proposed",
) -> LodgingItem:
    return LodgingItem.model_validate(
        {
            **_common(title, start, end, status),
            "type": "lodging",
            "timezone": TOKYO_TZ,
            "place": place,
        }
    )


def activity(
    start: str,
    end: str,
    *,
    place: Place = SENSOJI,
    title: str | None = None,
    status: ItemStatus = "proposed",
) -> ActivityItem:
    return ActivityItem.model_validate(
        {
            **_common(title or place.name, start, end, status),
            "type": "activity",
            "timezone": TOKYO_TZ,
            "place": place,
            "category": "sightseeing",
        }
    )


def transfer(
    start: str,
    end: str,
    *,
    from_place: Place = NRT,
    to_place: Place = OKURA,
    title: str = "Narita Express",
    status: ItemStatus = "proposed",
) -> TransferItem:
    return TransferItem.model_validate(
        {
            **_common(title, start, end, status),
            "type": "transfer",
            "timezone": TOKYO_TZ,
            "mode": "train",
            "from_place": from_place,
            "to_place": to_place,
        }
    )
