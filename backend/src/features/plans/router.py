from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from features.auth.dependencies import CurrentUser
from features.itinerary import service as itinerary_service
from features.plans import service, stages
from features.plans.schemas import (
    PlanCreate,
    PlanSummary,
    PlanUpdate,
    StageChange,
    TravelPlan,
)
from shared.db import SessionDep
from shared.domain import Place
from shared.errors import error_responses

router = APIRouter(
    prefix="/plans",
    tags=["plans"],
    responses=error_responses(status.HTTP_401_UNAUTHORIZED),
)

_NOT_FOUND = error_responses(status.HTTP_404_NOT_FOUND)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")


@router.get("", operation_id="listPlans")
async def list_plans(user: CurrentUser, session: SessionDep) -> list[PlanSummary]:
    """The current user's plans, most recently updated first."""
    plans = await service.list_plans(session, user.id)
    return [PlanSummary.model_validate(plan) for plan in plans]


@router.post("", operation_id="createPlan", status_code=status.HTTP_201_CREATED)
async def create_plan(body: PlanCreate, user: CurrentUser, session: SessionDep) -> TravelPlan:
    """Create a plan. Without an `origin` it starts from the user's home, if they set one."""
    home = Place.model_validate(user.home) if user.home is not None else None
    plan = await service.create_plan(session, user.id, body, home=home)
    return await itinerary_service.snapshot(session, plan)


@router.get("/{plan_id}", operation_id="getPlan", responses=_NOT_FOUND)
async def get_plan(plan_id: UUID, user: CurrentUser, session: SessionDep) -> TravelPlan:
    """The plan with its itinerary items (sorted by start)."""
    try:
        return await itinerary_service.get_plan_snapshot(session, user.id, plan_id)
    except service.PlanNotFoundError as exc:
        raise _not_found() from exc


@router.patch(
    "/{plan_id}",
    operation_id="updatePlan",
    responses=_NOT_FOUND | error_responses(status.HTTP_422_UNPROCESSABLE_CONTENT),
)
async def update_plan(
    plan_id: UUID, body: PlanUpdate, user: CurrentUser, session: SessionDep
) -> TravelPlan:
    """Apply a partial update; bumps the plan `version`."""
    try:
        plan = await service.update_plan(session, user.id, plan_id, body)
    except service.PlanNotFoundError as exc:
        raise _not_found() from exc
    except service.InvalidPlanError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return await itinerary_service.snapshot(session, plan)


@router.post(
    "/{plan_id}/stage",
    operation_id="changeStage",
    responses=_NOT_FOUND | error_responses(status.HTTP_409_CONFLICT),
)
async def change_stage(
    plan_id: UUID, body: StageChange, user: CurrentUser, session: SessionDep
) -> TravelPlan:
    """Move through the planning stages. `next` is refused (409) until the current stage's
    checklist is complete; going back is always allowed."""
    try:
        current = await itinerary_service.get_plan_snapshot(session, user.id, plan_id)
        stage, flights_skipped = stages.apply_stage_change(current, body)
        plan = await service.set_stage(session, user.id, plan_id, stage, flights_skipped)
    except service.PlanNotFoundError as exc:
        raise _not_found() from exc
    except stages.StageError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await itinerary_service.snapshot(session, plan)


@router.delete(
    "/{plan_id}",
    operation_id="deletePlan",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND,
)
async def delete_plan(plan_id: UUID, user: CurrentUser, session: SessionDep) -> None:
    try:
        await service.delete_plan(session, user.id, plan_id)
    except service.PlanNotFoundError as exc:
        raise _not_found() from exc
