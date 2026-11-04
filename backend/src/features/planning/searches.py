"""Searches that turn a `RunSearch` into an offer's candidates.

Each search kind has a runner; Phase 3 onwards adds them (flights, then lodging, activities
and transfers). Until a kind has one, running it fails like a provider outage would, so the
UI shows Retry and the rest of the flow is unaffected.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from features.planning.schemas import Candidate, RunSearch, SearchKind
from features.plans.schemas import PlanSnapshot


class SearchFailedError(Exception):
    """The search couldn't produce candidates. The message is shown to the user."""


@dataclass(frozen=True)
class SearchContext:
    session: AsyncSession
    today: date


Runner = Callable[[SearchContext, PlanSnapshot, RunSearch], Awaitable[list[Candidate]]]

_RUNNERS: dict[SearchKind, Runner] = {}


def query_hash(plan: PlanSnapshot, action: RunSearch) -> str:
    """Identifies a search by what it asks for, so a repeat within a day can reuse an offer."""
    key = {"plan": str(plan.id), "action": action.model_dump(mode="json")}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


async def run_search(ctx: SearchContext, plan: PlanSnapshot, action: RunSearch) -> list[Candidate]:
    """The candidates for `action`, best first; `SearchFailedError` when there are none."""
    runner = _RUNNERS.get(action.kind)
    if runner is None:
        raise SearchFailedError(f"Searching isn't available here yet ({action.label.lower()}).")
    candidates = await runner(ctx, plan, action)
    if not candidates:
        raise SearchFailedError("The search found nothing that fits. Try again in a moment.")
    return candidates
