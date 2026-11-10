from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from features.itinerary.schemas import (
    ActivityCategory,
    Gap,
    ItineraryItem,
    LodgingKind,
    SpendingLimit,
)
from shared.domain import Money, Place, Travelers
from shared.flights.types import FlightCabin

# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------

# `status` is the user's own label for the trip. Nothing reads it to gate anything.
PlanStatus = Literal["draft", "planned", "booked"]


PlanTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
PlanNotes = Annotated[str, Field(max_length=5000)]


def _one_per_category(limits: list[SpendingLimit]) -> list[SpendingLimit]:
    categories = [limit.category for limit in limits]
    repeated = sorted({category for category in categories if categories.count(category) > 1})
    if repeated:
        raise ValueError(f"Only one limit per category: {', '.join(repeated)}")
    return limits


SpendingLimits = Annotated[
    list[SpendingLimit], Field(max_length=10), AfterValidator(_one_per_category)
]


def active_budget(budget: Money | None) -> Money | None:
    """`budget`, or None when it is 0: a $0 budget means "no budget", not "spend nothing".
    Otherwise every cost would break it and nothing could be added."""
    return None if budget is not None and budget.amount == 0 else budget


# Normalised on the way in (a 0 budget is stored as none) and on the way out (plans saved
# before that still read as having no budget).
Budget = Annotated[Money | None, AfterValidator(active_budget)]


def check_date_range(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError("end_date must be on or after start_date")


# ---------------------------------------------------------------------------
# Preferences: answers that steer searches but aren't itinerary items
# ---------------------------------------------------------------------------


class SlotName(StrEnum):
    """Every input the planning flow asks for (`planning.slots` holds the registry). It
    lives here because a plan stores which optional slots the user declined."""

    DESTINATION = "destination"
    DATES = "dates"
    TRAVELERS = "travelers"
    ORIGIN = "origin"
    BUDGET = "budget"
    FLIGHT_PREFERENCES = "flight_preferences"
    FLIGHT_OUTBOUND = "flight_outbound"
    FLIGHT_RETURN = "flight_return"
    LODGING_PREFERENCES = "lodging_preferences"
    LODGING_STAY = "lodging_stay"
    ACTIVITY_PREFERENCES = "activity_preferences"
    ACTIVITIES = "activities"
    TRANSFER = "transfer"


DepartWindow = Literal["any", "morning", "afternoon", "evening"]
HotelClass = Literal["budget", "midrange", "upscale", "luxury"]
ShortAnswer = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


# How each `max_stops` choice reads ("not nonstop", "Economy cabin, up to 1 stop").
STOP_LABELS: dict[int | None, str] = {
    0: "nonstop",
    1: "up to 1 stop",
    2: "up to 2 stops",
    None: "any number of stops",
}


class FlightPreferencesAnswer(BaseModel):
    """What the user tells the flight search before it runs."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    cabin: FlightCabin = FlightCabin.ECONOMY
    max_stops: Literal[0, 1, 2] | None = None  # None: any number of stops
    depart_window: DepartWindow = "any"


class FlightPreferences(FlightPreferencesAnswer):
    # The picked outbound flight's search token, which the return search needs. Set when
    # the outbound flight is picked, never by the user. (No class docstring: it would
    # replace the parent's in the spec.)
    outbound_token: str | None = None


class LodgingPreferences(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    hotel_class: HotelClass
    kind: LodgingKind = "hotel"
    nightly_budget: Money | None = None
    neighborhood: ShortAnswer | None = None


class ActivityPreferences(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    interests: list[ActivityCategory] = Field(min_length=1, max_length=5)
    dietary: ShortAnswer = ""


class PlanPreferences(BaseModel):
    """Stored on the plan (`travel_plans.preferences`); a plan saved before this existed
    reads as all None."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    flights: FlightPreferences | None = None
    lodging: LodgingPreferences | None = None
    activities: ActivityPreferences | None = None
    # Optional slots the user skipped, so they aren't asked again.
    declined: list[SlotName] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class PlanCreate(BaseModel):
    title: PlanTitle = "Untitled trip"
    status: PlanStatus = "draft"
    origin: Place | None = None
    destination: Place | None = None
    start_date: date | None = None
    end_date: date | None = None
    travelers: Travelers | None = None
    budget: Budget = None
    limits: SpendingLimits = Field(default_factory=list)
    notes: PlanNotes = ""

    @model_validator(mode="after")
    def _dates_in_order(self) -> Self:
        check_date_range(self.start_date, self.end_date)
        return self


# Fields that exist on every plan; a PATCH may omit them but may not null them out.
_NON_NULLABLE_FIELDS = frozenset({"title", "status", "notes", "limits"})


class PlanUpdate(BaseModel):
    """Partial update: only fields present in the request body are applied.

    The date range is re-checked by the service against the merged plan, since a patch may
    carry only one of the two dates.
    """

    title: PlanTitle | None = None
    status: PlanStatus | None = None
    origin: Place | None = None
    destination: Place | None = None
    start_date: date | None = None
    end_date: date | None = None
    travelers: Travelers | None = None
    budget: Budget = None
    limits: SpendingLimits | None = None  # replaces the whole list; [] clears it
    notes: PlanNotes | None = None

    @model_validator(mode="after")
    def _required_fields_not_nulled(self) -> Self:
        present = self.model_fields_set & _NON_NULLABLE_FIELDS
        nulled = sorted(name for name in present if getattr(self, name) is None)
        if nulled:
            raise ValueError(f"These fields cannot be null: {', '.join(nulled)}")
        return self


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class PlanSummary(BaseModel):
    """Lightweight row for the plans sidebar."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    status: PlanStatus
    destination: Place | None
    start_date: date | None
    end_date: date | None
    version: int
    updated_at: datetime


class PlanDetails(BaseModel):
    """Plan-level fields, read straight off the ORM row."""

    # Defaulted fields are still always in responses, so the generated TS keeps them required.
    model_config = ConfigDict(
        from_attributes=True, json_schema_serialization_defaults_required=True
    )

    id: UUID
    user_id: UUID
    title: str
    status: PlanStatus
    # The trip is made without flying (driving, a train), so it needs no flight out or home.
    no_flights: bool
    origin: Place | None
    destination: Place | None
    start_date: date | None
    end_date: date | None
    travelers: Travelers | None
    budget: Budget
    limits: list[SpendingLimit]
    preferences: PlanPreferences = Field(default_factory=PlanPreferences)
    notes: str
    version: int
    created_at: datetime
    updated_at: datetime


class PlanSnapshot(PlanDetails):
    """Plan details with the itinerary; built by `itinerary.service.snapshot`, which owns
    the items. The API returns `planning.schemas.TravelPlan`, which adds the plan's
    problems and what it is waiting on the user for."""

    items: list[ItineraryItem]  # sorted by start
    # Unbridged changes of place, home to home; derived on every read
    # (`itinerary.gaps.find_gaps`).
    gaps: list[Gap]
