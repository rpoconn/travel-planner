import uuid
from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, UTCDateTime, utc_now


class Message(Base):
    """One message in a plan's transcript (a plan has exactly one transcript)."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_plan_created", "plan_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # String FK keeps this feature from importing the plans models directly.
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("travel_plans.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20), default="message")
    content: Mapped[str] = mapped_column(Text, default="")
    # Structured content for planning messages (the form or choices a message showed, or
    # the answer it recorded), so the transcript re-renders them after a reload.
    payload: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
