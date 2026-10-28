from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, status

from features.auth.dependencies import CurrentUser
from features.itinerary import service
from features.itinerary.schemas import (
    ItineraryItem,
    ItineraryItemCreate,
    ItineraryItemPatch,
    Timeline,
)
from features.plans.service import PlanNotFoundError
from shared.db import SessionDep
from shared.errors import error_responses

router = APIRouter(
    prefix="/plans/{plan_id}",
    tags=["itinerary"],
    responses=error_responses(status.HTTP_401_UNAUTHORIZED, status.HTTP_404_NOT_FOUND),
)

_INVALID = error_responses(status.HTTP_422_UNPROCESSABLE_CONTENT)


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


@router.get("/items", operation_id="listItems")
async def list_items(plan_id: UUID, user: CurrentUser, session: SessionDep) -> list[ItineraryItem]:
    """The plan's itinerary items, sorted by start time."""
    try:
        return await service.list_items(session, user.id, plan_id)
    except PlanNotFoundError as exc:
        raise _not_found("Plan not found") from exc


@router.post("/items", operation_id="addItem", status_code=status.HTTP_201_CREATED)
async def add_item(
    plan_id: UUID,
    body: Annotated[ItineraryItemCreate, Body()],
    user: CurrentUser,
    session: SessionDep,
) -> ItineraryItem:
    """Add an item; bumps the plan `version`."""
    try:
        return await service.add_item(session, user.id, plan_id, body)
    except PlanNotFoundError as exc:
        raise _not_found("Plan not found") from exc


@router.patch("/items/{item_id}", operation_id="updateItem", responses=_INVALID)
async def update_item(
    plan_id: UUID,
    item_id: UUID,
    body: Annotated[ItineraryItemPatch, Body()],
    user: CurrentUser,
    session: SessionDep,
) -> ItineraryItem:
    """Apply a partial update (`type` must match the item); bumps the plan `version`."""
    try:
        return await service.update_item(session, user.id, plan_id, item_id, body)
    except PlanNotFoundError as exc:
        raise _not_found("Plan not found") from exc
    except service.ItemNotFoundError as exc:
        raise _not_found("Item not found") from exc
    except service.InvalidItemError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.delete(
    "/items/{item_id}", operation_id="removeItem", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_item(plan_id: UUID, item_id: UUID, user: CurrentUser, session: SessionDep) -> None:
    """Delete an item; bumps the plan `version`."""
    try:
        await service.remove_item(session, user.id, plan_id, item_id)
    except PlanNotFoundError as exc:
        raise _not_found("Plan not found") from exc
    except service.ItemNotFoundError as exc:
        raise _not_found("Item not found") from exc


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


@router.get("/timeline", operation_id="getTimeline")
async def get_timeline(plan_id: UUID, user: CurrentUser, session: SessionDep) -> Timeline:
    """Day columns, lodging bars and warnings derived from the plan's items."""
    try:
        return await service.get_timeline(session, user.id, plan_id)
    except PlanNotFoundError as exc:
        raise _not_found("Plan not found") from exc
