"""Proposals: changes the planner suggests on its own, stored until the user decides.

A proposal holds commands that already passed a dry run (`commands.dry_run`), with the
plan's version at the time. Approving checks them again and writes them all or nothing
(`commands.check_and_apply`); rejecting writes nothing. Commands point at items by their
number in the list, so a proposal is only good for the plan it was made for: once the
plan's version moves on it is stale, is no longer shown, and can't be approved.

A plan has at most one pending proposal; opening a new one supersedes the old.
"""

import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.chat.tools import ToolContext
from features.planning.commands import check_and_apply
from features.planning.models import PlanningProposal
from features.planning.schemas import (
    AddCandidate,
    ChangesRecord,
    Proposal,
    ProposalChange,
    ProposalStatus,
)
from features.plans.schemas import PlanSnapshot
from shared.db import utc_now

logger = logging.getLogger(__name__)


class ProposalNotFoundError(Exception):
    pass


class ProposalClosedError(Exception):
    """The proposal was already decided or replaced. The message is shown to the user."""


class ProposalStaleError(ProposalClosedError):
    """The plan changed since the proposal was made."""


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def current_proposal(session: AsyncSession, plan_id: UUID) -> Proposal | None:
    """The plan's pending proposal, if any (it may be stale: see `is_fresh`)."""
    result = await session.execute(
        select(PlanningProposal)
        .where(PlanningProposal.plan_id == plan_id, PlanningProposal.status == "pending")
        .order_by(PlanningProposal.created_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return Proposal.model_validate(row) if row is not None else None


def is_fresh(proposal: Proposal, plan: PlanSnapshot) -> bool:
    """Whether `proposal` still waits for a decision on the plan as it stands."""
    return proposal.status == "pending" and proposal.plan_version == plan.version


async def _get_row(session: AsyncSession, plan_id: UUID, proposal_id: UUID) -> PlanningProposal:
    row = await session.get(PlanningProposal, proposal_id)
    if row is None or row.plan_id != plan_id:
        raise ProposalNotFoundError(proposal_id)
    return row


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def _close(session: AsyncSession, row: PlanningProposal, status: ProposalStatus) -> Proposal:
    row.status = status
    row.decided_at = utc_now()
    await session.commit()
    logger.info("Proposal %s on plan %s is now %s", row.id, row.plan_id, status)
    return Proposal.model_validate(row)


async def open_proposal(
    session: AsyncSession,
    *,
    plan_id: UUID,
    plan_version: int,
    reason: str,
    changes: Sequence[ProposalChange],
) -> Proposal:
    """Store a new pending proposal of dry-run changes, superseding any other pending one
    for the plan."""
    result = await session.execute(
        select(PlanningProposal).where(
            PlanningProposal.plan_id == plan_id, PlanningProposal.status == "pending"
        )
    )
    for old in result.scalars():
        old.status = "superseded"
        old.decided_at = utc_now()
    row = PlanningProposal(
        plan_id=plan_id,
        status="pending",
        reason=reason,
        changes=[change.model_dump(mode="json") for change in changes],
        plan_version=plan_version,
    )
    session.add(row)
    await session.commit()
    logger.info("Opened proposal %s on plan %s (%d changes)", row.id, plan_id, len(changes))
    return Proposal.model_validate(row)


async def _pending_row(session: AsyncSession, plan_id: UUID, proposal_id: UUID) -> PlanningProposal:
    row = await _get_row(session, plan_id, proposal_id)
    if row.status != "pending":
        raise ProposalClosedError(f"This suggestion was already {row.status}")
    return row


async def reject_proposal(session: AsyncSession, plan_id: UUID, proposal_id: UUID) -> Proposal:
    """Mark a pending proposal rejected (stale or not: nothing is written either way);
    `ProposalClosedError` if it isn't pending."""
    return await _close(session, await _pending_row(session, plan_id, proposal_id), "rejected")


async def approve_proposal(
    ctx: ToolContext, plan: PlanSnapshot, proposal_id: UUID
) -> tuple[Proposal, ChangesRecord]:
    """Check a pending proposal's changes against `plan` again and write them all or
    nothing. The proposal ends `approved`, or `failed` when a change no longer fits (the
    record says which rule). `ProposalStaleError` when the plan changed since it was made;
    `ProposalClosedError` when it isn't pending."""
    row = await _pending_row(ctx.session, plan.id, proposal_id)
    proposal = Proposal.model_validate(row)
    if not is_fresh(proposal, plan):
        await _close(ctx.session, row, "superseded")
        raise ProposalStaleError("The trip changed since this was suggested")
    candidates = {
        change.command.candidate: change.candidate
        for change in proposal.changes
        if isinstance(change.command, AddCandidate) and change.candidate is not None
    }
    commands = [change.command for change in proposal.changes]
    record = await check_and_apply(ctx, plan, commands, candidates)
    # A write that stopped part-way (the plan changed between the check and the write)
    # leaves `applied` and `rejected` both set: not what was approved.
    status: ProposalStatus = "failed" if record.rejected else "approved"
    return await _close(ctx.session, row, status), record
