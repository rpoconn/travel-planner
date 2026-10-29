"""Itinerary item schemas: a discriminated union on `type` (flight / lodging / activity /
transfer), plus the per-type create and patch inputs and the timeline view model.

Times: `start`/`end` are always returned as aware datetimes in the item's own timezone
(`end` in `end_timezone` when set, e.g. a flight's arrival airport). Inputs may be naive, in
which case they are read as wall-clock time in that timezone.
"""

import datetime as dt
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from shared.domain import Money, Place

# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------

ItemType = Literal["flight", "lodging", "activity", "transfer"]
ItemStatus = Literal["proposed", "confirmed", "cancelled"]
ItemCreator = Literal["user", "assistant"]
ItemTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ItemNotes = Annotated[str, Field(max_length=5000)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
SourceUrl = Annotated[str, Field(max_length=2000)]

Cabin = Literal["economy", "premium", "business", "first"]
FlightDirection = Literal["outbound", "return", "internal"]
LodgingKind = Literal["hotel", "hostel", "rental", "resort", "other"]
ActivityCategory = Literal[
    "sightseeing", "food", "tour", "museum", "outdoors", "nightlife", "shopping", "relax", "other"
]
TransferMode = Literal[
    "car_rental", "taxi", "rideshare", "train", "bus", "ferry", "shuttle", "walk", "metro"
]


def _check_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown IANA timezone: {name!r}") from exc
    return name


TimezoneName = Annotated[str, Field(max_length=64), AfterValidator(_check_timezone)]


def to_zone(value: datetime, timezone: str) -> datetime:
    """Naive → wall-clock time in `timezone`; aware → the same instant shown in `timezone`."""
    zone = ZoneInfo(timezone)
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


# ---------------------------------------------------------------------------
# Create inputs (also the base of the stored item shapes)
# ---------------------------------------------------------------------------


class ItemFields(BaseModel):
    """Fields shared by every item type."""

    # Responses always include defaulted fields, so mark them required in the output
    # schema; the generated TS types then don't make them optional.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    title: ItemTitle
    start: datetime
    end: datetime
    timezone: TimezoneName  # IANA timezone of `start`, e.g. "Asia/Tokyo"
    end_timezone: TimezoneName | None = None  # timezone of `end` when it differs
    status: ItemStatus = "proposed"
    cost: Money | None = None
    booking_ref: ShortText | None = None
    source_url: SourceUrl | None = None  # where the assistant found it
    notes: ItemNotes = ""

    @model_validator(mode="after")
    def _normalize_times(self) -> Self:
        # Assigning inside an "after" validator doesn't re-trigger validation (no
        # validate_assignment), so this is safe and keeps one canonical representation.
        self.start = to_zone(self.start, self.timezone)
        self.end = to_zone(self.end, self.end_timezone or self.timezone)
        if self.end < self.start:
            raise ValueError(_end_before_start_message(self.start, self.end))
        return self


def _end_before_start_message(start: datetime, end: datetime) -> str:
    # Across timezones a bare "end must be after start" is hard to act on: a model will
    # keep nudging the arrival hour on the same date (PHL 10:25 → LIS 13:20 looks fine but
    # is 2h before departure). Show both instants in the end's zone and suggest the fix.
    message = "end must be at or after start"
    if start.utcoffset() == end.utcoffset():
        return message
    zone = end.tzname()
    return (
        f"{message}: start {start:%Y-%m-%d %H:%M} {start.tzname()} is "
        f"{start.astimezone(end.tzinfo):%Y-%m-%d %H:%M} {zone}, but end is "
        f"{end:%Y-%m-%d %H:%M} {zone}. Times are local to each timezone; an overnight or "
        "long-haul arrival usually needs the next day's date"
    )


class FlightItemCreate(ItemFields):
    type: Literal["flight"]
    carrier: ShortText
    flight_number: ShortText | None = None
    from_place: Place
    to_place: Place
    cabin: Cabin = "economy"
    direction: FlightDirection
    layovers: list[Place] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _airports_have_codes(self) -> Self:
        if self.from_place.iata is None or self.to_place.iata is None:
            raise ValueError("flight from_place and to_place need an IATA airport code")
        return self


class LodgingItemCreate(ItemFields):
    """`start` is check-in, `end` is check-out."""

    type: Literal["lodging"]
    place: Place
    room_type: ShortText | None = None
    nightly_rate: Money | None = None
    kind: LodgingKind = "hotel"


class ActivityItemCreate(ItemFields):
    type: Literal["activity"]
    place: Place
    category: ActivityCategory
    booking_required: bool = False


class TransferItemCreate(ItemFields):
    type: Literal["transfer"]
    mode: TransferMode
    from_place: Place
    to_place: Place
    provider: ShortText | None = None  # "Hertz", "JR East"
    pickup_location: Place | None = None  # car rental only
    dropoff_location: Place | None = None


ItineraryItemCreate = Annotated[
    FlightItemCreate | LodgingItemCreate | ActivityItemCreate | TransferItemCreate,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Stored items (responses)
# ---------------------------------------------------------------------------


class ItemIdentity(BaseModel):
    id: UUID
    plan_id: UUID
    created_by: ItemCreator


class FlightItem(FlightItemCreate, ItemIdentity):
    pass


class LodgingItem(LodgingItemCreate, ItemIdentity):
    pass


class ActivityItem(ActivityItemCreate, ItemIdentity):
    pass


class TransferItem(TransferItemCreate, ItemIdentity):
    pass


ItineraryItem = Annotated[
    FlightItem | LodgingItem | ActivityItem | TransferItem,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Patches: every field optional except `type`, which must match the stored item.
# The service merges the patch onto the item and re-validates the whole thing, so
# nulling a required field or breaking start <= end is rejected there.
# ---------------------------------------------------------------------------


class ItemPatchFields(BaseModel):
    title: ItemTitle | None = None
    start: datetime | None = None
    end: datetime | None = None
    timezone: TimezoneName | None = None
    end_timezone: TimezoneName | None = None
    status: ItemStatus | None = None
    cost: Money | None = None
    booking_ref: ShortText | None = None
    source_url: SourceUrl | None = None
    notes: ItemNotes | None = None


class FlightItemPatch(ItemPatchFields):
    type: Literal["flight"]
    carrier: ShortText | None = None
    flight_number: ShortText | None = None
    from_place: Place | None = None
    to_place: Place | None = None
    cabin: Cabin | None = None
    direction: FlightDirection | None = None
    layovers: list[Place] | None = Field(default=None, max_length=5)


class LodgingItemPatch(ItemPatchFields):
    type: Literal["lodging"]
    place: Place | None = None
    room_type: ShortText | None = None
    nightly_rate: Money | None = None
    kind: LodgingKind | None = None


class ActivityItemPatch(ItemPatchFields):
    type: Literal["activity"]
    place: Place | None = None
    category: ActivityCategory | None = None
    booking_required: bool | None = None


class TransferItemPatch(ItemPatchFields):
    type: Literal["transfer"]
    mode: TransferMode | None = None
    from_place: Place | None = None
    to_place: Place | None = None
    provider: ShortText | None = None
    pickup_location: Place | None = None
    dropoff_location: Place | None = None


ItineraryItemPatch = Annotated[
    FlightItemPatch | LodgingItemPatch | ActivityItemPatch | TransferItemPatch,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Timeline view model (GET /plans/{id}/timeline)
# ---------------------------------------------------------------------------

TimelineCardItem = Annotated[FlightItem | ActivityItem | TransferItem, Field(discriminator="type")]
WarningKind = Literal["no_lodging", "overlap", "missing_transfer", "outside_trip"]


class TimelineEntry(BaseModel):
    """A card on one day. Items spanning midnight appear on every day they touch."""

    item: TimelineCardItem
    continues_from_previous_day: bool
    continues_to_next_day: bool


class TimelineDay(BaseModel):
    index: int
    date: dt.date
    entries: list[TimelineEntry]  # sorted by start


class LodgingSpan(BaseModel):
    """One continuous bar from the check-in day to the check-out day (inclusive indices)."""

    item: LodgingItem
    start_day_index: int
    end_day_index: int


class TimelineWarning(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    kind: WarningKind
    message: str
    date: dt.date | None = None
    item_ids: list[UUID] = Field(default_factory=list)


class Timeline(BaseModel):
    plan_id: UUID
    version: int
    # Covers the plan's dates, widened to include any items outside them (or derived from
    # the items alone when the plan has no dates). Empty when there is nothing to place.
    days: list[TimelineDay]
    lodging: list[LodgingSpan]
    warnings: list[TimelineWarning]
