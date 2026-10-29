from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db import create_engine, create_sessionmaker
from shared.migrations import run_migrations
from shared.search.cache import CachedProvider, cache_key
from shared.search.fake import FakeSearchProvider
from shared.search.models import SearchCacheEntry
from shared.search.types import SearchError, SearchResult

RESULTS = [SearchResult(title="Hotel Okura", url="https://example.com/okura", snippet="$400")]
START = datetime(2027, 1, 15, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await run_migrations(engine)
    async with create_sessionmaker(engine)() as db:
        yield db
    await engine.dispose()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def inner() -> FakeSearchProvider:
    return FakeSearchProvider()


@pytest.fixture
def cached(inner: FakeSearchProvider, session: AsyncSession, clock: Clock) -> CachedProvider:
    return CachedProvider(inner, session, ttl=timedelta(hours=24), clock=clock)


# ---------------------------------------------------------------------------
# Hits and misses
# ---------------------------------------------------------------------------


async def test_repeat_query_is_served_from_the_cache(
    cached: CachedProvider, inner: FakeSearchProvider, session: AsyncSession
) -> None:
    inner.queue(RESULTS)

    first = await cached.search("Hotels in Tokyo", "hotels")
    # Different case/spacing is the same query.
    second = await cached.search("  hotels   in tokyo ", "hotels")

    assert first == second == RESULTS
    assert len(inner.searches) == 1
    entry = await session.get(SearchCacheEntry, cache_key("hotels in tokyo", "hotels", 5))
    assert entry is not None and entry.kind == "hotels" and entry.created_at == START


async def test_cache_survives_a_new_provider_instance(
    cached: CachedProvider, inner: FakeSearchProvider, session: AsyncSession, clock: Clock
) -> None:
    # Like a later request: a fresh wrapper over the same database.
    inner.queue(RESULTS)
    await cached.search("Hotels in Tokyo", "hotels")

    later = CachedProvider(inner, session, clock=clock)

    assert await later.search("Hotels in Tokyo", "hotels") == RESULTS
    assert len(inner.searches) == 1


async def test_kind_and_max_results_are_part_of_the_key(
    cached: CachedProvider, inner: FakeSearchProvider
) -> None:
    await cached.search("Tokyo", "hotels")
    await cached.search("Tokyo", "activities")
    await cached.search("Tokyo", "hotels", max_results=2)

    assert len(inner.searches) == 3


async def test_expired_entries_are_refreshed(
    cached: CachedProvider, inner: FakeSearchProvider, session: AsyncSession, clock: Clock
) -> None:
    newer = [SearchResult(title="Park Hyatt", url="https://example.com/ph")]
    inner.queue(RESULTS, newer)
    await cached.search("hotels in Tokyo", "hotels")

    clock.now = START + timedelta(hours=23)
    assert await cached.search("hotels in Tokyo", "hotels") == RESULTS

    clock.now = START + timedelta(hours=24)
    assert await cached.search("hotels in Tokyo", "hotels") == newer
    assert len(inner.searches) == 2
    entry = await session.get(SearchCacheEntry, cache_key("hotels in Tokyo", "hotels", 5))
    assert entry is not None and entry.created_at == clock.now
    assert entry.results == [result.model_dump() for result in newer]


# ---------------------------------------------------------------------------
# Nothing is cached for failures or empty answers
# ---------------------------------------------------------------------------


async def test_errors_are_not_cached(cached: CachedProvider, inner: FakeSearchProvider) -> None:
    inner.queue(SearchError("Web search timed out."), RESULTS)

    with pytest.raises(SearchError):
        await cached.search("hotels in Tokyo", "hotels")
    assert await cached.search("hotels in Tokyo", "hotels") == RESULTS
    assert len(inner.searches) == 2


async def test_empty_results_are_not_cached(
    cached: CachedProvider, inner: FakeSearchProvider
) -> None:
    inner.queue([], RESULTS)

    assert await cached.search("hotels in Tokyo", "hotels") == []
    assert await cached.search("hotels in Tokyo", "hotels") == RESULTS
