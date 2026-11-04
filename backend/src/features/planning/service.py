"""The plan as the API returns it: the itinerary snapshot plus the engine's assessment.

Routers call this rather than `itinerary.service.snapshot`, which stays free of planning
imports so `itinerary` and `plans` never depend on `planning`.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from features.itinerary import service as itinerary_service
from features.itinerary.timeline import build_timeline
from features.planning import offers
from features.planning.engine import evaluate
from features.planning.schemas import TravelPlan
from features.plans import service as plans_service
from features.plans.models import Plan


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
    return TravelPlan.model_validate({**dict(base), "assessment": assessment})


async def get_plan_snapshot(session: AsyncSession, user_id: UUID, plan_id: UUID) -> TravelPlan:
    plan = await plans_service.get_plan(session, user_id, plan_id)
    return await snapshot(session, plan)
