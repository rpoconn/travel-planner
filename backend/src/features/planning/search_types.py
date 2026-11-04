"""What every search runner takes and raises. Kept apart from `planning.searches` (which
dispatches to the runners) so each runner module can import these without a cycle."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from features.planning.schemas import Candidate, RunSearch
from features.plans.schemas import PlanSnapshot
from shared.flights.types import FlightProvider
from shared.hotels.types import HotelProvider
from shared.places.types import PlaceProvider


class SearchFailedError(Exception):
    """The search couldn't produce candidates. The message is shown to the user."""


class NoMoreOptionsError(SearchFailedError):
    """Asked for more options, the search found only ones already offered."""


@dataclass(frozen=True)
class SearchContext:
    session: AsyncSession
    today: date
    flights: FlightProvider
    hotels: HotelProvider
    places: PlaceProvider
    # Titles already offered for this search ("More options"), keyed by `title_key`: the
    # runners leave them out, so the next best ones are listed instead.
    exclude: frozenset[str] = frozenset()


def title_key(title: str) -> str:
    """How offered titles are compared: case and surrounding space don't count."""
    return title.strip().casefold()


def unseen(ctx: SearchContext, title: str) -> bool:
    """Whether a candidate with this title hasn't been offered for the search yet."""
    return title_key(title) not in ctx.exclude


# A runner returns the candidates for one `RunSearch`, best first (empty when none fit).
Runner = Callable[[SearchContext, PlanSnapshot, RunSearch], Awaitable[list[Candidate]]]
