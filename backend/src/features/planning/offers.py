"""Offers: the persisted candidate lists the user picks from.

Persisting them means a reload shows the same choices without searching again. A plan has
at most one open offer; opening a new one dismisses the old.
"""

import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.itinerary.schemas import Gap
from features.planning.models import PlanningOffer
from features.planning.schemas import Candidate, NightRange, Offer, OfferStatus
from features.plans.schemas import PlanStage, SlotName
from shared.db import utc_now

logger = logging.getLogger(__name__)


class OfferNotFoundError(Exception):
    pass


class OfferClosedError(Exception):
    """The offer was already picked from or dismissed."""


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def current_offer(session: AsyncSession, plan_id: UUID) -> Offer | None:
    """The plan's open offer, if any."""
    result = await session.execute(
        select(PlanningOffer)
        .where(PlanningOffer.plan_id == plan_id, PlanningOffer.status == "open")
        .order_by(PlanningOffer.created_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return Offer.model_validate(row) if row is not None else None


async def _get_row(session: AsyncSession, plan_id: UUID, offer_id: UUID) -> PlanningOffer:
    row = await session.get(PlanningOffer, offer_id)
    if row is None or row.plan_id != plan_id:
        raise OfferNotFoundError(offer_id)
    return row


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def _close_open(session: AsyncSession, plan_id: UUID) -> None:
    result = await session.execute(
        select(PlanningOffer).where(
            PlanningOffer.plan_id == plan_id, PlanningOffer.status == "open"
        )
    )
    for row in result.scalars():
        row.status = "dismissed"
        row.decided_at = utc_now()


async def open_offer(
    session: AsyncSession,
    *,
    plan_id: UUID,
    slot: SlotName,
    stage: PlanStage,
    plan_version: int,
    candidates: Sequence[Candidate],
    query_hash: str,
    gap: Gap | None = None,
    nights: NightRange | None = None,
) -> Offer:
    """Store a new open offer, dismissing any other open one for the plan."""
    await _close_open(session, plan_id)
    row = PlanningOffer(
        plan_id=plan_id,
        slot=slot.value,
        stage=stage.value,
        plan_version=plan_version,
        candidates=[candidate.model_dump(mode="json") for candidate in candidates],
        status="open",
        query_hash=query_hash,
        gap=gap.model_dump(mode="json") if gap is not None else None,
        nights=nights.model_dump(mode="json") if nights is not None else None,
    )
    session.add(row)
    await session.commit()
    logger.info("Opened %s offer %s on plan %s (%d)", slot, row.id, plan_id, len(candidates))
    return Offer.model_validate(row)


async def close_offer(
    session: AsyncSession, plan_id: UUID, offer_id: UUID, status: OfferStatus
) -> Offer:
    """Mark an open offer `picked` or `dismissed`; `OfferClosedError` if it isn't open."""
    row = await _get_row(session, plan_id, offer_id)
    if row.status != "open":
        raise OfferClosedError(f"This offer was already {row.status}")
    row.status = status
    row.decided_at = utc_now()
    await session.commit()
    return Offer.model_validate(row)
