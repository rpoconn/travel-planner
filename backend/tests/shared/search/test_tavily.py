"""TavilyProvider against a mocked Tavily HTTP API (httpx.MockTransport)."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from shared.search.tavily import SNIPPET_MAX_CHARS, TAVILY_URL, TavilyProvider
from shared.search.types import SearchError

Handler = Callable[[httpx.Request], httpx.Response]


def _provider(handler: Handler, *, max_attempts: int = 3) -> TavilyProvider:
    return TavilyProvider(
        api_key="tvly-test",
        max_attempts=max_attempts,
        retry_wait_seconds=0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _body(*results: dict[str, Any]) -> dict[str, Any]:
    return {"query": "q", "results": list(results), "response_time": 0.4}


HOTEL = {
    "title": "Hotel Okura Tokyo",
    "url": "https://example.com/okura",
    "content": "Rooms from $400 per night.",
    "score": 0.9,
}


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


async def test_search_sends_request_and_maps_results() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_body(HOTEL))

    results = await _provider(handler).search("hotels in Tokyo", "hotels", max_results=4)

    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("Hotel Okura Tokyo", "https://example.com/okura", "Rooms from $400 per night.")
    ]
    (request,) = seen
    assert str(request.url) == TAVILY_URL
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer tvly-test"
    body = json.loads(request.content)
    assert body["query"] == "hotels in Tokyo"
    assert body["max_results"] == 4


async def test_long_snippets_are_trimmed_and_missing_titles_use_the_url() -> None:
    long = {"url": "https://example.com/long", "content": "word  \n " * 400}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(long))

    (result,) = await _provider(handler).search("q", "general")

    assert result.title == "https://example.com/long"
    assert len(result.snippet) == SNIPPET_MAX_CHARS
    assert result.snippet.endswith("…")
    assert "\n" not in result.snippet and "  " not in result.snippet


async def test_results_are_capped_at_max_results() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(HOTEL, HOTEL, HOTEL))

    assert len(await _provider(handler).search("q", "general", max_results=2)) == 2


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


async def test_transient_errors_are_retried() -> None:
    responses = iter([httpx.Response(503), httpx.Response(429), httpx.Response(200, json=_body())])

    def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    assert await _provider(handler).search("q", "general") == []


async def test_connection_errors_are_retried_then_reported() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(SearchError, match="Could not reach"):
        await _provider(handler, max_attempts=2).search("q", "general")
    assert calls == 2


async def test_bad_api_key_is_not_retried() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"detail": {"error": "Unauthorized"}})

    with pytest.raises(SearchError, match="API key was rejected"):
        await _provider(handler).search("q", "general")
    assert calls == 1


async def test_unexpected_response_body_is_reported() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(SearchError, match="unexpected response"):
        await _provider(handler).search("q", "general")
