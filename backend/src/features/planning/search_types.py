"""What every search runner takes and raises. Kept apart from `planning.searches` (which
dispatches to the runners) so each runner module can import these without a cycle."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from features.planning.schemas import Candidate, RunSearch
from features.plans.schemas import PlanSnapshot
from shared.flights.types import FlightProvider


class SearchFailedError(Exception):
    """The search couldn't produce candidates. The message is shown to the user."""


@dataclass(frozen=True)
class SearchContext:
    session: AsyncSession
    today: date
    flights: FlightProvider


# A runner returns the candidates for one `RunSearch`, best first (empty when none fit).
Runner = Callable[[SearchContext, PlanSnapshot, RunSearch], Awaitable[list[Candidate]]]
