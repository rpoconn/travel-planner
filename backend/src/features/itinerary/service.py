"""Itinerary item CRUD, plus the composed plan snapshot and timeline.

Every function resolves the plan through `plans.service.get_plan` first, so ownership is
checked in one place (other users' plans and items are "not found"). Every item mutation
bumps the plan's `version`.
"""

import logging
from typing import Any
from uuid import UUID

from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.itinerary.models import Item
from features.itinerary.schemas import (
    ItemCreator,
    ItemFields,
    ItemIdentity,
    ItineraryItem,
    ItineraryItemCreate,
    ItineraryItemPatch,
    Timeline,
)
from features.itinerary.timeline import build_timeline
from features.plans import service as plans_service
from features.plans.models import Plan
from features.plans.schemas import PlanDetails, TravelPlan
from shared.db import utc_now
from shared.validation import summarize_validation_error

logger = logging.getLogger(__name__)

_ITEM = TypeAdapter[ItineraryItem](ItineraryItem)
_ITEM_CREATE = TypeAdapter[ItineraryItemCreate](ItineraryItemCreate)

# Shared fields live in their own columns; everything else type-specific goes in `details`.
_COLUMN_FIELDS = frozenset(ItemFields.model_fields)
_IDENTITY_FIELDS = frozenset(ItemIdentity.model_fields)


class ItemNotFoundError(Exception):
    pass


class InvalidItemError(Exception):
    pass


# ---------------------------------------------------------------------------
# Row <-> schema conversion
# ---------------------------------------------------------------------------


def _to_schema(row: Item) -> ItineraryItem:
    data: dict[str, Any] = {
        **row.details,
        **{name: getattr(row, name) for name in _COLUMN_FIELDS},
        "type": row.type,
        "id": row.id,
        "plan_id": row.plan_id,
        "created_by": row.created_by,
    }
    # Re-validating converts the stored UTC instants back to the item's local timezone.
    return _ITEM.validate_python(data)


def _write(row: Item, data: ItineraryItemCreate) -> None:
    for name in _COLUMN_FIELDS:
        value = getattr(data, name)
        setattr(row, name, value.model_dump(mode="json") if isinstance(value, BaseModel) else value)
    row.type = data.type
    row.details = data.model_dump(mode="json", exclude=set(_COLUMN_FIELDS | {"type"}))


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def _items_for(session: AsyncSession, plan_id: UUID) -> list[ItineraryItem]:
    result = await session.execute(
        select(Item).where(Item.plan_id == plan_id).order_by(Item.start, Item.created_at)
    )
    return [_to_schema(row) for row in result.scalars()]


async def _get_row(session: AsyncSession, plan_id: UUID, item_id: UUID) -> Item:
    row = await session.get(Item, item_id)
    if row is None or row.plan_id != plan_id:
        raise ItemNotFoundError(item_id)
    return row


async def list_items(session: AsyncSession, user_id: UUID, plan_id: UUID) -> list[ItineraryItem]:
    plan = await plans_service.get_plan(session, user_id, plan_id)
    return await _items_for(session, plan.id)


async def get_item(
    session: AsyncSession, user_id: UUID, plan_id: UUID, item_id: UUID
) -> ItineraryItem:
    plan = await plans_service.get_plan(session, user_id, plan_id)
    return _to_schema(await _get_row(session, plan.id, item_id))


async def snapshot(session: AsyncSession, plan: Plan) -> TravelPlan:
    """Full `TravelPlan` (details + items) for a plan the caller has already resolved."""
    details = PlanDetails.model_validate(plan)
    return TravelPlan.model_validate({**dict(details), "items": await _items_for(session, plan.id)})


async def get_plan_snapshot(session: AsyncSession, user_id: UUID, plan_id: UUID) -> TravelPlan:
    plan = await plans_service.get_plan(session, user_id, plan_id)
    return await snapshot(session, plan)


async def get_timeline(session: AsyncSession, user_id: UUID, plan_id: UUID) -> Timeline:
    plan = await plans_service.get_plan(session, user_id, plan_id)
    return build_timeline(
        plan_id=plan.id,
        version=plan.version,
        start_date=plan.start_date,
        end_date=plan.end_date,
        items=await _items_for(session, plan.id),
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def add_item(
    session: AsyncSession,
    user_id: UUID,
    plan_id: UUID,
    data: ItineraryItemCreate,
    created_by: ItemCreator = "user",
) -> ItineraryItem:
    plan = await plans_service.get_plan(session, user_id, plan_id)
    row = Item(plan_id=plan.id, created_by=created_by)
    _write(row, data)
    session.add(row)
    plans_service.mark_changed(plan)
    await session.commit()
    logger.info("Added %s item %s to plan %s (%s)", row.type, row.id, plan.id, created_by)
    return _to_schema(row)


async def update_item(
    session: AsyncSession,
    user_id: UUID,
    plan_id: UUID,
    item_id: UUID,
    patch: ItineraryItemPatch,
) -> ItineraryItem:
    """Merge `patch` onto the stored item and re-validate the result as a whole."""
    plan = await plans_service.get_plan(session, user_id, plan_id)
    row = await _get_row(session, plan.id, item_id)
    current = _to_schema(row)
    if patch.type != current.type:
        raise InvalidItemError(f"Item is a {current.type}, not a {patch.type}")

    merged = {
        **current.model_dump(exclude=set(_IDENTITY_FIELDS)),
        **patch.model_dump(exclude_unset=True),
    }
    try:
        validated = _ITEM_CREATE.validate_python(merged)
    except ValidationError as exc:
        raise InvalidItemError(summarize_validation_error(exc)) from exc

    _write(row, validated)
    row.updated_at = utc_now()
    plans_service.mark_changed(plan)
    await session.commit()
    return _to_schema(row)


async def remove_item(session: AsyncSession, user_id: UUID, plan_id: UUID, item_id: UUID) -> None:
    plan = await plans_service.get_plan(session, user_id, plan_id)
    row = await _get_row(session, plan.id, item_id)
    await session.delete(row)
    plans_service.mark_changed(plan)
    await session.commit()
    logger.info("Removed item %s from plan %s", item_id, plan.id)
