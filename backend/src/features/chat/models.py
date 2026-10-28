import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, UTCDateTime, utc_now


class Message(Base):
    """One chat message in a plan's conversation (a plan has exactly one conversation)."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_plan_created", "plan_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # String FK keeps this feature from importing the plans models directly.
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("travel_plans.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20), default="message")
    content: Mapped[str] = mapped_column(Text, default="")
    # Trace of the tools the assistant ran while producing this message (`ToolTrace` list).
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
