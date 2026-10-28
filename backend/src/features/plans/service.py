"""Plan CRUD. Every function is scoped to the owning user: other users' plans are "not found"."""

import logging
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.plans.models import Plan
from features.plans.schemas import PlanCreate, PlanUpdate, check_date_range
from shared.db import utc_now

logger = logging.getLogger(__name__)


class PlanNotFoundError(Exception):
    pass


class InvalidPlanError(Exception):
    pass


def _column_value(value: Any) -> Any:
    # Value objects go into JSON columns; dates/strings/ints map to native columns as-is.
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def list_plans(session: AsyncSession, user_id: UUID) -> list[Plan]:
    result = await session.execute(
        select(Plan).where(Plan.user_id == user_id).order_by(Plan.updated_at.desc())
    )
    return list(result.scalars())


async def get_plan(session: AsyncSession, user_id: UUID, plan_id: UUID) -> Plan:
    plan = await session.get(Plan, plan_id)
    if plan is None or plan.user_id != user_id:
        raise PlanNotFoundError(plan_id)
    return plan


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def create_plan(session: AsyncSession, user_id: UUID, data: PlanCreate) -> Plan:
    plan = Plan(
        user_id=user_id,
        **{name: _column_value(getattr(data, name)) for name in PlanCreate.model_fields},
    )
    session.add(plan)
    await session.commit()
    logger.info("Created plan %s for user %s", plan.id, user_id)
    return plan


async def update_plan(
    session: AsyncSession, user_id: UUID, plan_id: UUID, patch: PlanUpdate
) -> Plan:
    plan = await get_plan(session, user_id, plan_id)

    start = patch.start_date if "start_date" in patch.model_fields_set else plan.start_date
    end = patch.end_date if "end_date" in patch.model_fields_set else plan.end_date
    try:
        check_date_range(start, end)
    except ValueError as exc:
        raise InvalidPlanError(str(exc)) from exc

    for name in patch.model_fields_set:
        setattr(plan, name, _column_value(getattr(patch, name)))
    plan.version += 1
    plan.updated_at = utc_now()
    await session.commit()
    return plan


async def delete_plan(session: AsyncSession, user_id: UUID, plan_id: UUID) -> None:
    plan = await get_plan(session, user_id, plan_id)
    await session.delete(plan)
    await session.commit()
    logger.info("Deleted plan %s", plan_id)
