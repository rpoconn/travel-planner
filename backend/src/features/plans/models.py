import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, UTCDateTime, utc_now


class Plan(Base):
    """A user's trip. Value objects (Place/Travelers/Money) are stored as JSON and validated
    through `shared.domain` models on the way in and out."""

    __tablename__ = "travel_plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # String FK keeps this feature from importing the auth models directly.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # Workflow position (see `schemas.PlanStage`); only `service.set_stage` changes it.
    stage: Mapped[str] = mapped_column(String(20), default="basics")
    # The user chose to travel without flying; the flights stage then counts as done.
    flights_skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    origin: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    destination: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    start_date: Mapped[date | None] = mapped_column(default=None)
    end_date: Mapped[date | None] = mapped_column(default=None)
    travelers: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    budget: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    # SpendingLimit objects, at most one per activity category.
    limits: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # `PlanPreferences`: search preferences and declined optional inputs.
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Bumped on every mutation (plan details and itinerary items) so the UI
    # can tell when its cached copy is stale.
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
