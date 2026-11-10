"""Transcript persistence. Ownership of the plan is checked by callers through
`plans.service.get_plan` (the router does it before a stream starts)."""

import logging
from collections.abc import Collection
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.chat.models import Message
from features.chat.schemas import MessageKind, MessageRole
from features.plans import service as plans_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def list_messages(session: AsyncSession, user_id: UUID, plan_id: UUID) -> list[Message]:
    """The plan's whole transcript, oldest first."""
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


async def latest_message(
    session: AsyncSession, plan_id: UUID, *, skipping: Collection[MessageKind] = ()
) -> Message | None:
    """The plan's last message that isn't one of the `skipping` kinds, if any."""
    result = await session.execute(
        select(Message)
        .where(Message.plan_id == plan_id, Message.kind.not_in(list(skipping)))
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def add_message(
    session: AsyncSession,
    plan_id: UUID,
    role: MessageRole,
    content: str,
    *,
    kind: MessageKind = MessageKind.MESSAGE,
    payload: BaseModel | None = None,
) -> Message:
    """Store one message. `payload` is a planning message's structured content (the form,
    choices or answer it showed), kept as JSON so the transcript re-renders it."""
    message = Message(
        plan_id=plan_id,
        role=role,
        kind=kind,
        content=content,
        payload=payload.model_dump(mode="json") if payload is not None else None,
    )
    session.add(message)
    await session.commit()
    logger.debug("Stored %s %s message %s in plan %s", kind, role, message.id, plan_id)
    return message
