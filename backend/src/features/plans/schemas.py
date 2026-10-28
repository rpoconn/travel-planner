from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from features.itinerary.schemas import ItineraryItem
from shared.domain import Money, Place, Travelers

# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------

PlanStatus = Literal["draft", "planned", "booked"]
PlanTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
PlanNotes = Annotated[str, Field(max_length=5000)]


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
    travelers: Travelers = Field(default_factory=Travelers)
    budget: Money | None = None
    notes: PlanNotes = ""

    @model_validator(mode="after")
    def _dates_in_order(self) -> Self:
        check_date_range(self.start_date, self.end_date)
        return self


# Fields that exist on every plan; a PATCH may omit them but may not null them out.
_NON_NULLABLE_FIELDS = frozenset({"title", "status", "travelers", "notes"})


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
    budget: Money | None = None
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

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    title: str
    status: PlanStatus
    origin: Place | None
    destination: Place | None
    start_date: date | None
    end_date: date | None
    travelers: Travelers
    budget: Money | None
    notes: str
    version: int
    created_at: datetime
    updated_at: datetime


class TravelPlan(PlanDetails):
    """Full plan snapshot including its itinerary; built by `itinerary.service.snapshot`,
    which owns the items."""

    items: list[ItineraryItem]  # sorted by start
