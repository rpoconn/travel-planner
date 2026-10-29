"""Itinerary item schemas: a discriminated union on `type` (flight / lodging / activity /
transfer), plus the per-type create and patch inputs and the timeline view model.

Times: `start`/`end` are always returned as aware datetimes in the item's own timezone
(`end` in `end_timezone` when set, e.g. a flight's arrival airport). Inputs may be naive, in
which case they are read as wall-clock time in that timezone.

Some rules apply only to new input (`*ItemCreate`, which updates are re-validated as),
not to stored items: a flight or transfer must take time, a flight must use real
airports rather than city codes, and an airport's code must match its name. Rows saved
before a rule existed still load.
"""

import datetime as dt
from datetime import datetime, timedelta
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

from features.itinerary.airports import metro_code_error, name_code_error
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
    "car_rental",
    "car_service",
    "taxi",
    "rideshare",
    "train",
    "bus",
    "ferry",
    "shuttle",
    "walk",
    "metro",
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

    # The descriptions double as the tool schema the model fills in, so they spell out the
    # exact shapes it most often gets wrong (local times, the end of a flight, money).
    title: ItemTitle = Field(description="Short name, e.g. 'Flight PHL to HND'")
    start: datetime = Field(
        description="Local wall-clock start, YYYY-MM-DDTHH:MM:SS (departure / check-in)"
    )
    end: datetime = Field(
        description=(
            "Local wall-clock end, YYYY-MM-DDTHH:MM:SS: arrival for flights and transfers, "
            "check-out for lodging, finish time for activities"
        )
    )
    timezone: TimezoneName = Field(description="IANA timezone of `start`, e.g. Asia/Tokyo")
    end_timezone: TimezoneName | None = Field(
        default=None, description="IANA timezone of `end` when it differs (arrival airport)"
    )
    status: ItemStatus = "proposed"
    cost: Money | None = Field(
        default=None,
        description=(
            'Total price for all travelers (not per person) as {"amount": 1380, '
            '"currency": "USD"}; amount is a plain number'
        ),
    )
    booking_ref: ShortText | None = None
    source_url: SourceUrl | None = Field(
        default=None, description="URL of the web_search result this came from"
    )
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


def _format_instant(value: datetime) -> str:
    return f"{value:%Y-%m-%d %H:%M} {value.tzname()}"


def _too_short_message(item: ItemFields, kind: str, minimum: timedelta) -> str | None:
    """Why `item` is too short, or None. A model that was told its arrival was before
    departure will sometimes "fix" it to the exact same instant (10:00 EST → 00:00 JST
    next day), which passes a plain end >= start check and shows up as a 0m flight."""
    duration = item.end - item.start
    if duration >= minimum:
        return None
    departs = _format_instant(item.start)
    if item.start.utcoffset() != item.end.utcoffset():
        departs += f" ({_format_instant(item.start.astimezone(item.end.tzinfo))})"
    minutes = int(duration.total_seconds() // 60)
    message = (
        f"a {kind} must take at least {int(minimum.total_seconds() // 60)} minute(s), but it "
        f"departs {departs} and arrives {_format_instant(item.end)}, {minutes} minute(s) "
        "later. Set end to the scheduled local arrival time where it arrives"
    )
    if kind == "flight":
        message += "; a long-haul flight takes many hours and often lands on a later date"
    return message


def _airport_code_error(*places: Place) -> str | None:
    """The first airport whose code is a city code or names a different airport."""
    for place in places:
        if place.iata is None:
            continue
        error = metro_code_error(place.iata) or name_code_error(place.name, place.iata)
        if error is not None:
            return error
    return None


# Shortest realistic scheduled flight; anything below this is a time-entry mistake.
_MIN_FLIGHT = timedelta(minutes=15)
_MIN_TRANSFER = timedelta(minutes=1)


class FlightFields(ItemFields):
    """Fields of a flight, shared by the create input and the stored item."""

    type: Literal["flight"]
    carrier: ShortText = Field(description="Airline name")
    flight_number: ShortText | None = Field(default=None, description="e.g. AA 167")
    from_place: Place = Field(description="Departure airport; `iata` is required")
    to_place: Place = Field(description="Arrival airport; `iata` is required")
    cabin: Cabin = "economy"
    direction: FlightDirection = Field(
        description="outbound = leaving home, return = going home, internal = within the trip"
    )
    layovers: list[Place] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _airports_have_codes(self) -> Self:
        if self.from_place.iata is None or self.to_place.iata is None:
            raise ValueError("flight from_place and to_place need an IATA airport code")
        return self


class FlightItemCreate(FlightFields):
    # Input-only rules (see the module docstring). No class docstring: Pydantic would put
    # it into the OpenAPI spec and the tool schema.
    @model_validator(mode="after")
    def _is_a_real_flight(self) -> Self:
        error = _airport_code_error(self.from_place, self.to_place, *self.layovers)
        if error is not None:
            raise ValueError(error)
        error = _too_short_message(self, "flight", _MIN_FLIGHT)
        if error is not None:
            raise ValueError(error)
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


class TransferFields(ItemFields):
    """Fields of a transfer, shared by the create input and the stored item."""

    type: Literal["transfer"]
    mode: TransferMode
    from_place: Place
    to_place: Place
    provider: ShortText | None = None  # "Hertz", "JR East"
    pickup_location: Place | None = None  # car rental only
    dropoff_location: Place | None = None


class TransferItemCreate(TransferFields):
    @model_validator(mode="after")
    def _is_a_real_transfer(self) -> Self:
        # Airport transfers carry the airport's code too, and get it wrong the same way.
        error = _airport_code_error(self.from_place, self.to_place)
        if error is not None:
            raise ValueError(error)
        error = _too_short_message(self, "transfer", _MIN_TRANSFER)
        if error is not None:
            raise ValueError(error)
        return self


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


class FlightItem(FlightFields, ItemIdentity):
    pass


class LodgingItem(LodgingItemCreate, ItemIdentity):
    pass


class ActivityItem(ActivityItemCreate, ItemIdentity):
    pass


class TransferItem(TransferFields, ItemIdentity):
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
