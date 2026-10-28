"""Provider-neutral web search types: results, the `SearchProvider` protocol, errors."""

from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel

from shared.domain import Place

# What the agent is looking for; drives query shaping (see `query.py`).
SearchKind = Literal["flights", "hotels", "activities", "transport", "general"]

DEFAULT_MAX_RESULTS = 5


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""


class SearchError(Exception):
    """A search that couldn't be completed. The message is safe to show the user/model."""


class SearchProvider(Protocol):
    async def search(
        self, query: str, kind: SearchKind, *, max_results: int = DEFAULT_MAX_RESULTS
    ) -> list[SearchResult]:
        """Return up to `max_results` results; raise `SearchError` on failure."""
        ...

    async def aclose(self) -> None: ...


class SearchContext(BaseModel):
    """Trip facts used to make a short query specific (filled from the current plan)."""

    origin: Place | None = None
    destination: Place | None = None
    travel_date: date | None = None
