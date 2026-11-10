"""The plan as the API returns it: the itinerary snapshot plus the validation engine's
problems and what the plan is waiting on the user for.

Routers call this rather than `itinerary.service.snapshot`, which stays free of planning
imports so `itinerary` and `plans` never depend on `planning`.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from features.chat import service as chat_service
from features.chat.schemas import MessageKind
from features.itinerary import service as itinerary_service
from features.itinerary.timeline import build_timeline
from features.planning import offers, proposals
from features.planning.pending import AskedForm, ask_input, pending_interaction
from features.planning.schemas import AskInput, Problem, TravelPlan
from features.planning.validation import is_complete, validate
from features.plans import service as plans_service
from features.plans.models import Plan
from features.plans.schemas import PlanSnapshot


def opening_form(plan: PlanSnapshot, problems: Sequence[Problem]) -> AskedForm | None:
    """What a plan nobody has said anything in yet waits on: the form of the first thing it
    is missing that only the user can give (where to, when). A new trip opens on a question
    without a model call; the planner takes over from the first answer."""
    slot = next(
        (
            problem.fix.slot
            for problem in problems
            if problem.severity == "blocking" and problem.fix.how == "ask"
        ),
        None,
    )
    if slot is None:
        return None
    return AskedForm(form=ask_input(slot, plan), at=plan.created_at)


async def _asked_form(
    session: AsyncSession, plan: PlanSnapshot, problems: Sequence[Problem]
) -> AskedForm | None:
    """The form the transcript ends on, if it ends on one. A stored failure notice after it
    doesn't answer it, so the form still stands. An empty transcript opens on a form of its
    own (`opening_form`)."""
    last = await chat_service.latest_message(session, plan.id, skipping=(MessageKind.ERROR,))
    if last is None:
        return opening_form(plan, problems)
    if last.kind != MessageKind.EXPECTED_INPUT or last.payload is None:
        return None
    return AskedForm(form=AskInput.model_validate(last.payload), at=last.created_at)


async def snapshot(session: AsyncSession, plan: Plan) -> TravelPlan:
    """Full `TravelPlan` for a plan the caller has already resolved."""
    base = await itinerary_service.snapshot(session, plan)
    timeline = build_timeline(
        plan_id=base.id,
        version=base.version,
        start_date=base.start_date,
        end_date=base.end_date,
        items=base.items,
        budget=base.budget,
        limits=base.limits,
        travelers=base.travelers,
        origin=base.origin,
    )
    offer = await offers.current_offer(session, plan.id)
    problems = validate(base, timeline.warnings)
    pending = pending_interaction(
        base,
        asked=await _asked_form(session, base, problems),
        offer=offer,
        proposal=await proposals.current_proposal(session, plan.id),
    )
    return TravelPlan.model_validate(
        {
            **dict(base),
            "problems": problems,
            "complete": is_complete(problems),
            "pending": pending,
        }
    )


async def get_plan_snapshot(session: AsyncSession, user_id: UUID, plan_id: UUID) -> TravelPlan:
    plan = await plans_service.get_plan(session, user_id, plan_id)
    return await snapshot(session, plan)
