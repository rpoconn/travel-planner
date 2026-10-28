"""Search provider construction and the `SearchDep` dependency."""

import logging
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request

from shared.config import Settings, SettingsDep
from shared.db import SessionDep
from shared.search.cache import CachedProvider
from shared.search.fake import FakeSearchProvider
from shared.search.tavily import TavilyProvider
from shared.search.types import (
    DEFAULT_MAX_RESULTS,
    SearchError,
    SearchKind,
    SearchProvider,
    SearchResult,
)

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "Web search is not configured (set TAVILY_API_KEY to enable it)."


class UnavailableProvider:
    """Stands in when no search backend is configured, so the agent learns why it can't search."""

    async def search(
        self, query: str, kind: SearchKind, *, max_results: int = DEFAULT_MAX_RESULTS
    ) -> list[SearchResult]:
        raise SearchError(NOT_CONFIGURED)

    async def aclose(self) -> None:
        return None


def create_search_provider(settings: Settings) -> SearchProvider:
    """Build the app-wide provider for `settings.search_provider`; stored on app.state."""
    if settings.search_provider == "fake":
        return FakeSearchProvider()
    if not settings.tavily_api_key:
        logger.warning("TAVILY_API_KEY is not set; the web_search tool will report it")
        return UnavailableProvider()
    return TavilyProvider(
        api_key=settings.tavily_api_key,
        timeout_seconds=settings.search_timeout_seconds,
    )


def get_search(request: Request, session: SessionDep, settings: SettingsDep) -> SearchProvider:
    """The app's provider behind a cache that uses this request's DB session.

    Tests swap `app.state.search` for a scripted `FakeSearchProvider`.
    """
    provider: SearchProvider = request.app.state.search
    return CachedProvider(provider, session, ttl=timedelta(hours=settings.search_cache_ttl_hours))


SearchDep = Annotated[SearchProvider, Depends(get_search)]
