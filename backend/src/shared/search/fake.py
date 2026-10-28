"""Deterministic `SearchProvider` for tests and `SEARCH_PROVIDER=fake` (offline dev / E2E).

Without scripted results it makes up obviously-fake but well-formed ones from the query,
so the agent loop can be exercised end to end without network access or an API key.
"""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import quote_plus

from shared.search.types import DEFAULT_MAX_RESULTS, SearchError, SearchKind, SearchResult

FakeSearchReply = list[SearchResult] | SearchError


@dataclass(frozen=True)
class FakeSearch:
    query: str
    kind: SearchKind
    max_results: int


def canned_results(query: str, kind: SearchKind, max_results: int) -> list[SearchResult]:
    slug = quote_plus(query)
    return [
        SearchResult(
            title=f"Example {kind} result {n} for {query}",
            url=f"https://example.com/{kind}/{n}?q={slug}",
            snippet=f"Offline placeholder result {n}. Prices and details are not real.",
        )
        for n in range(1, min(max_results, 3) + 1)
    ]


class FakeSearchProvider:
    def __init__(self, replies: Iterable[FakeSearchReply] = ()) -> None:
        self._replies: deque[FakeSearchReply] = deque(replies)
        self.searches: list[FakeSearch] = []

    def queue(self, *replies: FakeSearchReply) -> None:
        self._replies.extend(replies)

    async def search(
        self, query: str, kind: SearchKind, *, max_results: int = DEFAULT_MAX_RESULTS
    ) -> list[SearchResult]:
        self.searches.append(FakeSearch(query, kind, max_results))
        if not self._replies:
            return canned_results(query, kind, max_results)
        reply = self._replies.popleft()
        if isinstance(reply, SearchError):
            raise reply
        return reply[:max_results]

    async def aclose(self) -> None:
        return None
