import uuid
from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, UTCDateTime, utc_now


class PlanningOffer(Base):
    """Candidates offered for a pick slot (`schemas.Offer`). A plan has at most one open
    offer; opening another dismisses it (`offers.open_offer`)."""

    __tablename__ = "planning_offers"
    __table_args__ = (Index("ix_planning_offers_plan_status", "plan_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # String FK keeps this feature from importing the plans models directly.
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("travel_plans.id", ondelete="CASCADE"))
    slot: Mapped[str] = mapped_column(String(40))
    stage: Mapped[str] = mapped_column(String(20))
    plan_version: Mapped[int]
    # `Candidate` list, in the order shown.
    candidates: Mapped[list[dict[str, JsonValue]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="open")
    # Hash of the search that produced the candidates, so a repeat search can reuse them.
    query_hash: Mapped[str] = mapped_column(String(64), index=True)
    # What the offer is for, when the slot repeats: a transfer's gap, a stay's nights.
    gap: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, default=None)
    nights: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
