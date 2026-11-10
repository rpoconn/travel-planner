"""The plan as the API returns it: the itinerary snapshot plus the validation engine's
problems, what the plan is waiting on the user for, and the planning engine's assessment.

Routers call this rather than `itinerary.service.snapshot`, which stays free of planning
imports so `itinerary` and `plans` never depend on `planning`.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from features.chat import service as chat_service
from features.chat.schemas import MessageKind
from features.itinerary import service as itinerary_service
from features.itinerary.timeline import build_timeline
from features.planning import offers, proposals
from features.planning.engine import evaluate
from features.planning.pending import AskedForm, pending_interaction
from features.planning.schemas import AskInput, TravelPlan
from features.planning.validation import is_complete, validate
from features.plans import service as plans_service
from features.plans.models import Plan


async def _asked_form(session: AsyncSession, plan_id: UUID) -> AskedForm | None:
    """The form the transcript ends on, if it ends on one. A stored failure notice after it
    doesn't answer it, so the form still stands."""
    last = await chat_service.latest_message(session, plan_id, skipping=(MessageKind.ERROR,))
    if last is None or last.kind != MessageKind.EXPECTED_INPUT or last.payload is None:
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
    assessment = evaluate(base, offer, timeline.warnings)
    problems = validate(base, timeline.warnings)
    pending = pending_interaction(
        base,
        asked=await _asked_form(session, plan.id),
        offer=offer,
        proposal=await proposals.current_proposal(session, plan.id),
    )
    return TravelPlan.model_validate(
        {
            **dict(base),
            "assessment": assessment,
            "problems": problems,
            "complete": is_complete(problems),
            "pending": pending,
        }
    )


async def get_plan_snapshot(session: AsyncSession, user_id: UUID, plan_id: UUID) -> TravelPlan:
    plan = await plans_service.get_plan(session, user_id, plan_id)
    return await snapshot(session, plan)
