import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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

    # Async sessions can't lazy-load on attribute access, so the proposal is always loaded
    # with the message ("selectin"). New messages must have it assigned (even to None)
    # before they're read, see `service.add_message`.
    proposal: Mapped["Proposal | None"] = relationship(
        back_populates="message", uselist=False, lazy="selectin", cascade="all, delete-orphan"
    )


class Proposal(Base):
    """Plan changes an assistant message staged for the user's approval (`ChatProposal`)."""

    __tablename__ = "chat_proposals"
    __table_args__ = (Index("ix_chat_proposals_plan_status", "plan_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("travel_plans.id", ondelete="CASCADE"))
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # `ProposedChange` list, applied in order.
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    plan_version: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    message: Mapped[Message] = relationship(back_populates="proposal", lazy="raise")
