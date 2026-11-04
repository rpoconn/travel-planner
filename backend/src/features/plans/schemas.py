import datetime as dt
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
    computed_field,
    model_validator,
)

from features.itinerary.schemas import (
    ActivityCategory,
    Cabin,
    Gap,
    ItineraryItem,
    LodgingKind,
    SpendingLimit,
)
from shared.domain import Money, Place, Travelers

# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------

# `status` is the user's own label for the trip; `stage` (below) is where the planning
# workflow is. Nothing reads `status` to gate anything.
PlanStatus = Literal["draft", "planned", "booked"]


class PlanStage(StrEnum):
    """The planning workflow, in order. A StrEnum rather than a Literal so the OpenAPI spec
    names it and the generated SDK exports it as a constant the UI can iterate."""

    BASICS = "basics"
    FLIGHTS = "flights"
    LODGING = "lodging"
    ACTIVITIES = "activities"
    GROUND = "ground"
    REVIEW = "review"


STAGES: tuple[PlanStage, ...] = tuple(PlanStage)
StageAction = Literal["next", "back", "skip_flights", "go_to"]
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
    Otherwise every cost would break it and the agent would push the user to raise it."""
    return None if budget is not None and budget.amount == 0 else budget


# Normalised on the way in (a 0 budget is stored as none) and on the way out (plans saved
# before that still read as having no budget).
Budget = Annotated[Money | None, AfterValidator(active_budget)]


def check_date_range(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError("end_date must be on or after start_date")


# ---------------------------------------------------------------------------
# Issues: what stands between the plan and a finished trip
# ---------------------------------------------------------------------------


class IssueCode(StrEnum):
    """Why a stage isn't finished (blocking) or what looks wrong (warning). The second
    group matches the timeline's `WarningKind`s one to one."""

    # Stage checklists (blocking).
    MISSING_DATES = "missing_dates"
    MISSING_TRAVELERS = "missing_travelers"
    MISSING_ORIGIN = "missing_origin"
    MISSING_DESTINATION = "missing_destination"
    MISSING_OUTBOUND = "missing_outbound"
    MISSING_RETURN = "missing_return"
    UNCOVERED_NIGHT = "uncovered_night"
    NO_ACTIVITIES = "no_activities"
    MISSING_TRANSFER = "missing_transfer"
    STAGE_INCOMPLETE = "stage_incomplete"  # review: an earlier stage isn't done
    # Timeline warnings.
    OVERLAP = "overlap"
    OUTSIDE_TRIP = "outside_trip"
    MISSING_COST = "missing_cost"
    OVER_LIMIT = "over_limit"
    OVER_BUDGET = "over_budget"
    WRONG_TIMEZONE = "wrong_timezone"
    WRONG_DEPARTURE = "wrong_departure"


IssueSeverity = Literal["blocking", "warning"]


class Issue(BaseModel):
    """One problem with the plan, tied to the stage that fixes it."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    code: IssueCode
    stage: PlanStage
    severity: IssueSeverity
    message: str  # for people, e.g. "a place to stay the night of May 2"
    # `dt.date`: a field named `date` would shadow the type in later annotations.
    date: dt.date | None = None
    item_ids: list[UUID] = Field(default_factory=list)
    gap: Gap | None = None  # missing_transfer: the change of place to bridge


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


class FlightPreferencesAnswer(BaseModel):
    """What the user tells the flights stage before it searches."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    cabin: Cabin = "economy"
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


class StageChange(BaseModel):
    """Move the plan through the workflow: `next` (only when the current stage is ready),
    `back`, `skip_flights` (from the flights stage, for a trip without flying) or `go_to`
    an earlier `stage`."""

    action: StageAction
    stage: PlanStage | None = None

    @model_validator(mode="after")
    def _stage_only_for_go_to(self) -> Self:
        if self.action == "go_to" and self.stage is None:
            raise ValueError("go_to needs a stage")
        if self.action != "go_to" and self.stage is not None:
            raise ValueError(f"{self.action} takes no stage")
        return self


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class StageStatus(BaseModel):
    """A stage's checklist: what still blocks it (`issues`), plus two views derived from
    them for callers that only need a yes/no or the messages."""

    stage: PlanStage
    issues: list[Issue]

    # mypy doesn't accept a decorator on top of @property; this is Pydantic's documented
    # way to put a derived value into the response.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def ready(self) -> bool:
        return not self.issues

    @computed_field  # type: ignore[prop-decorator]
    @property
    def missing(self) -> list[str]:
        return [issue.message for issue in self.issues]


class PlanSummary(BaseModel):
    """Lightweight row for the plans sidebar."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    status: PlanStatus
    stage: PlanStage
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
    stage: PlanStage
    flights_skipped: bool
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
    """Plan details with the itinerary and the stage checklists; built by
    `itinerary.service.snapshot`, which owns the items. The API returns
    `planning.schemas.TravelPlan`, which adds the planning engine's assessment."""

    items: list[ItineraryItem]  # sorted by start
    # Derived on every read (`plans.stages.plan_progress`), one per stage in order.
    stages: list[StageStatus]
    # Unbridged changes of place, home to home; the ground stage is done when it's empty.
    gaps: list[Gap]
