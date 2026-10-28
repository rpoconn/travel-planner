"""Chat message persistence. Ownership of the plan is checked by callers through
`plans.service.get_plan` (the router does it before a stream starts)."""

import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.chat.models import Message
from features.chat.schemas import MessageKind, MessageRole, ToolTrace
from features.plans import service as plans_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def list_messages(session: AsyncSession, user_id: UUID, plan_id: UUID) -> list[Message]:
    """The plan's whole conversation, oldest first."""
    plan = await plans_service.get_plan(session, user_id, plan_id)
    result = await session.execute(
        select(Message).where(Message.plan_id == plan.id).order_by(Message.created_at)
    )
    return list(result.scalars())


async def recent_messages(session: AsyncSession, plan_id: UUID, limit: int) -> list[Message]:
    """The last `limit` messages, oldest first."""
    result = await session.execute(
        select(Message)
        .where(Message.plan_id == plan_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def add_message(
    session: AsyncSession,
    plan_id: UUID,
    role: MessageRole,
    content: str,
    *,
    kind: MessageKind = "message",
    tool_calls: Sequence[ToolTrace] = (),
) -> Message:
    message = Message(
        plan_id=plan_id,
        role=role,
        kind=kind,
        content=content,
        tool_calls=[trace.model_dump(mode="json") for trace in tool_calls],
    )
    session.add(message)
    await session.commit()
    logger.debug("Stored %s %s message %s in plan %s", kind, role, message.id, plan_id)
    return message


async def set_kind(session: AsyncSession, message: Message, kind: MessageKind) -> None:
    message.kind = kind
    await session.commit()
