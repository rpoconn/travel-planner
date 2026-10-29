"""Chat message and proposal persistence. Ownership of the plan is checked by callers
through `plans.service.get_plan` (the router does it before a stream starts)."""

import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.chat.models import Message, Proposal
from features.chat.schemas import (
    MessageKind,
    MessageRole,
    ProposalStatus,
    ProposedChange,
    ToolTrace,
)
from features.plans import service as plans_service
from shared.db import utc_now

logger = logging.getLogger(__name__)


class ProposalNotFoundError(Exception):
    pass


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


async def get_proposal(session: AsyncSession, plan_id: UUID, proposal_id: UUID) -> Proposal:
    """A proposal in `plan_id` (whose ownership the caller has checked)."""
    proposal = await session.get(Proposal, proposal_id)
    if proposal is None or proposal.plan_id != plan_id:
        raise ProposalNotFoundError(proposal_id)
    return proposal


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
        # Set explicitly: reading an unloaded relationship would need a lazy load.
        proposal=None,
    )
    session.add(message)
    await session.commit()
    logger.debug("Stored %s %s message %s in plan %s", kind, role, message.id, plan_id)
    return message


async def set_kind(session: AsyncSession, message: Message, kind: MessageKind) -> None:
    message.kind = kind
    await session.commit()


async def add_proposal(
    session: AsyncSession,
    message: Message,
    changes: Sequence[ProposedChange],
    plan_version: int,
) -> Proposal:
    """Attach the changes `message` staged. Only the newest proposal in a plan stays open:
    any other pending one is marked superseded in the same commit."""
    await session.execute(
        update(Proposal)
        .where(Proposal.plan_id == message.plan_id, Proposal.status == "pending")
        .values(status="superseded", decided_at=utc_now())
    )
    proposal = Proposal(
        plan_id=message.plan_id,
        status="pending",
        changes=[change.model_dump(mode="json") for change in changes],
        plan_version=plan_version,
    )
    message.proposal = proposal
    await session.commit()
    logger.info(
        "Stored proposal %s (%d changes) on message %s", proposal.id, len(changes), message.id
    )
    return proposal


async def set_proposal_status(
    session: AsyncSession, proposal: Proposal, status: ProposalStatus
) -> None:
    proposal.status = status
    proposal.decided_at = utc_now()
    await session.commit()
    logger.info("Proposal %s is now %s", proposal.id, status)
