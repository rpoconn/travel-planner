import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, UTCDateTime, utc_now


class Item(Base):
    """One itinerary item. Columns hold the fields every type shares; the type-specific fields
    (carrier, place, mode, ...) live in `details` and are validated through the
    `ItineraryItem` union in `schemas.py` on the way in and out."""

    __tablename__ = "itinerary_items"
    __table_args__ = (Index("ix_itinerary_items_plan_start", "plan_id", "start"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # String FK keeps this feature from importing the plans models directly.
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("travel_plans.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    # Stored as UTC instants (sortable across timezones); converted back to `timezone` /
    # `end_timezone` when read.
    start: Mapped[datetime] = mapped_column(UTCDateTime)
    end: Mapped[datetime] = mapped_column(UTCDateTime)
    timezone: Mapped[str] = mapped_column(String(64))
    end_timezone: Mapped[str | None] = mapped_column(String(64), default=None)
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    cost: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    booking_ref: Mapped[str | None] = mapped_column(String(200), default=None)
    source_url: Mapped[str | None] = mapped_column(String(2000), default=None)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(20))
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
