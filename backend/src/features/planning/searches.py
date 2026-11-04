"""Searches that turn a `RunSearch` into an offer's candidates.

Each search kind has a runner: flights and hotels from their providers, activities from
the place provider, and transfers from rules alone. A kind without a runner fails like a
provider outage would, so the UI shows Retry and the rest of the flow is unaffected.
"""

import hashlib
import json

from features.planning import activity_search, flight_search, lodging_search, transfer_search
from features.planning.schemas import Candidate, RunSearch, SearchKind
from features.planning.search_types import (
    NoMoreOptionsError,
    Runner,
    SearchContext,
    SearchFailedError,
)
from features.plans.schemas import PlanSnapshot

__all__ = [
    "NoMoreOptionsError",
    "SearchContext",
    "SearchFailedError",
    "query_hash",
    "run_search",
]

_RUNNERS: dict[SearchKind, Runner] = {
    "flights_outbound": flight_search.outbound_flights,
    "flights_return": flight_search.return_flights,
    "lodging": lodging_search.stays,
    "activities": activity_search.activities,
    "transfers": transfer_search.transfers,
}


def query_hash(plan: PlanSnapshot, action: RunSearch) -> str:
    """Identifies a search by what it asks for, so a repeat within a day can reuse an offer."""
    key = {"plan": str(plan.id), "action": action.model_dump(mode="json")}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


async def run_search(ctx: SearchContext, plan: PlanSnapshot, action: RunSearch) -> list[Candidate]:
    """The candidates for `action`, best first, leaving out `ctx.exclude`;
    `NoMoreOptionsError` when only excluded ones were found, `SearchFailedError` when none."""
    runner = _RUNNERS.get(action.kind)
    if runner is None:
        raise SearchFailedError(f"Searching isn't available here yet ({action.label.lower()}).")
    candidates = await runner(ctx, plan, action)
    if not candidates and ctx.exclude:
        raise NoMoreOptionsError(
            "Those are all the options I found. Pick one of these, or go back and change the trip."
        )
    if not candidates:
        raise SearchFailedError("The search found nothing that fits. Try again in a moment.")
    return candidates
