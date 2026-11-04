"""Picks: choosing candidates from the open offer. Picking is the approval, so a pick that
fits is written straight away.

Every candidate carries ready-made plan changes, run through the same validation as any
other change (`chat.tools.check_change`: route, overnight stay, timezone, budget). They are
all checked before any is written, so a pick that doesn't fit changes nothing. No model
calls here; free text reaches this module only as candidate ids the extractor read.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from features.chat import tools
from features.chat.schemas import ProposedChange
from features.planning import offers
from features.planning.schemas import Candidate, OfferChoice
from features.plans import service as plans_service
from features.plans.schemas import PlanSnapshot, SlotName

logger = logging.getLogger(__name__)


class PickError(ValueError):
    """The pick can't be made. The message is shown to the user."""


@dataclass(frozen=True)
class PickContext:
    session: AsyncSession
    user_id: UUID
    plan_id: UUID
    today: date


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------


def _count(count: int) -> str:
    return f"{count} option{'' if count == 1 else 's'}"


def picked_candidates(action: OfferChoice, candidate_ids: Sequence[str]) -> list[Candidate]:
    """The offer's candidates for `candidate_ids`, in the order given, or `PickError` when
    an id isn't the offer's, one repeats, or the count is outside what the slot takes."""
    by_id = {candidate.id: candidate for candidate in action.candidates}
    unknown = [cid for cid in candidate_ids if cid not in by_id]
    if unknown:
        raise PickError(f"There's no option {unknown[0]} in this list")
    if len(set(candidate_ids)) != len(candidate_ids):
        raise PickError("Pick each option only once")
    count = len(candidate_ids)
    if action.min_picks == action.max_picks and count != action.min_picks:
        raise PickError(f"Pick {_count(action.min_picks)}")
    if count < action.min_picks:
        raise PickError(f"Pick at least {_count(action.min_picks)}")
    if count > action.max_picks:
        raise PickError(f"Pick at most {_count(action.max_picks)}")
    return [by_id[cid] for cid in candidate_ids]


def describe_pick(candidates: Sequence[Candidate]) -> str:
    """The pick as one line of the transcript."""
    return "; ".join(candidate.title for candidate in candidates)


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def _changes(candidates: Sequence[Candidate]) -> list[ProposedChange]:
    return [
        ProposedChange(
            call_id=f"pick-{candidate.id}-{index}",
            name=change.name,
            arguments=change.arguments,
            summary=change.summary,
        )
        for candidate in candidates
        for index, change in enumerate(candidate.changes)
    ]


async def apply_pick(
    ctx: PickContext, plan: PlanSnapshot, action: OfferChoice, candidates: Sequence[Candidate]
) -> None:
    """Write the picked candidates' changes, close the offer, and keep what the next search
    needs (a round trip's flight-out token). `PickError` when a change doesn't fit."""
    tool_ctx = tools.ToolContext(
        session=ctx.session,
        user_id=ctx.user_id,
        plan_id=ctx.plan_id,
        today=ctx.today,
    )
    changes = _changes(candidates)
    for index, change in enumerate(changes):
        # Checked with the pick's earlier changes counted (a round trip's fare moved onto
        # the flight out, several activities sharing the budget), as the user sees them.
        outcome = await tools.check_change(replace(tool_ctx, staged=changes[:index]), change)
        if not outcome.ok:
            raise PickError(outcome.summary)
    for change in changes:
        # Writes commit one by one; a failure here (the plan changed in between) leaves
        # the earlier ones applied, and the plan's new version retires the offer.
        outcome = await tools.apply_change(tool_ctx, change)
        if not outcome.ok:
            logger.warning(
                "Pick on plan %s stopped at %s: %s", plan.id, change.name, outcome.summary
            )
            raise PickError(outcome.summary)
    await offers.close_offer(ctx.session, ctx.plan_id, action.offer_id, "picked")
    logger.info("Plan %s picked %s from offer %s", plan.id, action.slot, action.offer_id)

    flights = plan.preferences.flights
    if action.slot == SlotName.FLIGHT_OUTBOUND and flights is not None:
        # The flights home are searched for this flight out; without a token they are
        # searched one way.
        token = candidates[0].follow_up_token
        preferences = plan.preferences.model_copy(
            update={"flights": flights.model_copy(update={"outbound_token": token})}
        )
        await plans_service.update_preferences(ctx.session, ctx.user_id, plan.id, preferences)
