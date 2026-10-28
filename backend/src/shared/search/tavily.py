"""`SearchProvider` backed by the Tavily search API (https://docs.tavily.com)."""

import logging

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from shared.search.types import DEFAULT_MAX_RESULTS, SearchError, SearchKind, SearchResult

logger = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"

# Page extracts can be long; the model only needs enough to judge relevance and quote prices.
SNIPPET_MAX_CHARS = 500


# ---------------------------------------------------------------------------
# Response schema (only the fields we use)
# ---------------------------------------------------------------------------


class _TavilyResult(BaseModel):
    title: str = ""
    url: str
    content: str = ""


class _TavilyResponse(BaseModel):
    results: list[_TavilyResult]


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


def _describe(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return "Web search is misconfigured (the Tavily API key was rejected)."
        if status == 429:
            return "Web search is rate limited right now. Try again in a minute."
        return f"Web search failed (HTTP {status})."
    if isinstance(exc, httpx.TimeoutException):
        return "Web search timed out."
    if isinstance(exc, httpx.TransportError):
        return "Could not reach the web search service."
    return "Web search returned an unexpected response."


def _snippet(content: str) -> str:
    content = " ".join(content.split())
    if len(content) <= SNIPPET_MAX_CHARS:
        return content
    return content[: SNIPPET_MAX_CHARS - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class TavilyProvider:
    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 15.0,
        max_attempts: int = 3,
        retry_wait_seconds: float = 0.5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._max_attempts = max_attempts
        self._retry_wait_seconds = retry_wait_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    def _retrying(self) -> AsyncRetrying:
        return AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=self._retry_wait_seconds, max=8),
            retry=retry_if_exception(_is_transient),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )

    async def _post(self, query: str, max_results: int) -> httpx.Response:
        response = await self._client.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "query": query,
                "topic": "general",
                "search_depth": "basic",
                "max_results": max_results,
            },
        )
        response.raise_for_status()
        return response

    async def search(
        self, query: str, kind: SearchKind, *, max_results: int = DEFAULT_MAX_RESULTS
    ) -> list[SearchResult]:
        try:
            async for attempt in self._retrying():
                with attempt:
                    response = await self._post(query, max_results)
            parsed = _TavilyResponse.model_validate_json(response.content)
        except (httpx.HTTPError, ValidationError) as exc:
            logger.warning("Tavily search failed for %r (%s): %s", query, kind, exc)
            raise SearchError(_describe(exc)) from exc

        return [
            SearchResult(
                title=result.title or result.url,
                url=result.url,
                snippet=_snippet(result.content),
            )
            for result in parsed.results[:max_results]
        ]

    async def aclose(self) -> None:
        await self._client.aclose()
