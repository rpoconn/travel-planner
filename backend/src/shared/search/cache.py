"""SQLite-backed result cache in front of any `SearchProvider`.

Agents tend to repeat the same searches (across rounds, turns and plans), and search APIs
are slow and metered, so identical queries within the TTL are answered from the database.
"""

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db import utc_now
from shared.search.models import SearchCacheEntry
from shared.search.types import DEFAULT_MAX_RESULTS, SearchKind, SearchProvider, SearchResult

logger = logging.getLogger(__name__)

DEFAULT_TTL = timedelta(hours=24)

_RESULTS: TypeAdapter[list[SearchResult]] = TypeAdapter(list[SearchResult])


def cache_key(query: str, kind: SearchKind, max_results: int) -> str:
    # Case and spacing differences don't change what a search engine returns.
    normalized = " ".join(query.lower().split())
    return hashlib.sha256(f"{kind}\n{max_results}\n{normalized}".encode()).hexdigest()


class CachedProvider:
    """Wraps a provider with a read-through cache stored through `session`.

    Built per request (it's cheap) so the cache shares the request's session and
    transaction instead of opening a second SQLite connection that could hit a write lock.
    """

    def __init__(
        self,
        inner: SearchProvider,
        session: AsyncSession,
        *,
        ttl: timedelta = DEFAULT_TTL,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._inner = inner
        self._session = session
        self._ttl = ttl
        self._clock = clock

    async def search(
        self, query: str, kind: SearchKind, *, max_results: int = DEFAULT_MAX_RESULTS
    ) -> list[SearchResult]:
        key = cache_key(query, kind, max_results)
        now = self._clock()
        entry = await self._session.get(SearchCacheEntry, key)
        if entry is not None and now - entry.created_at < self._ttl:
            logger.info("Search cache hit for %r (%s)", query, kind)
            return _RESULTS.validate_python(entry.results)

        # Errors propagate and nothing is stored, so a failed search is retried next time.
        results = await self._inner.search(query, kind, max_results=max_results)
        if not results:
            # Could be a transient hiccup on the provider's side; don't pin it for a day.
            return results

        dumped = [result.model_dump() for result in results]
        if entry is None:
            self._session.add(
                SearchCacheEntry(key=key, kind=kind, query=query, results=dumped, created_at=now)
            )
        else:  # expired: refresh in place
            entry.query, entry.results, entry.created_at = query, dumped, now
        try:
            await self._session.commit()
        except IntegrityError:
            # A concurrent request cached the same query first; its copy is just as good.
            await self._session.rollback()
            logger.info("Search cache entry for %r was written concurrently", query)
        return results

    async def aclose(self) -> None:
        # The inner provider is app-wide and closed with the app, not per request.
        return None
