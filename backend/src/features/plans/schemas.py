from datetime import date, datetime
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

from features.itinerary.schemas import Gap, ItineraryItem, SpendingLimit
from shared.domain import Money, Place, Travelers

# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------

# `status` is the user's own label for the trip; `stage` (below) is where the planning
# workflow is. Nothing reads `status` to gate anything.
PlanStatus = Literal["draft", "planned", "booked"]

# The planning workflow, in order. The chat agent only gets the current stage's tools, and
# only the user moves between stages (POST /plans/{id}/stage).
PlanStage = Literal["basics", "flights", "lodging", "activities", "ground", "review"]
STAGES: tuple[PlanStage, ...] = ("basics", "flights", "lodging", "activities", "ground", "review")
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
    """Whether a stage's checklist is complete, and what is still missing if not."""

    stage: PlanStage
    ready: bool
    missing: list[str]


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

    model_config = ConfigDict(from_attributes=True)

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
    notes: str
    version: int
    created_at: datetime
    updated_at: datetime


class TravelPlan(PlanDetails):
    """Full plan snapshot including its itinerary; built by `itinerary.service.snapshot`,
    which owns the items."""

    items: list[ItineraryItem]  # sorted by start
    # Derived on every read (`plans.stages.plan_progress`), one per stage in order.
    stages: list[StageStatus]
    # Unbridged changes of place, home to home; the ground stage is done when it's empty.
    gaps: list[Gap]
