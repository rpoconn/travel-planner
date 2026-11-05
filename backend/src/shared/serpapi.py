"""The HTTP side of SerpApi (https://serpapi.com), shared by every engine we search with
(Google Flights, Google Hotels): the request with retries, and the errors as sentences
people can read.

The API key travels in the URL, so nothing here ever logs a URL or an httpx message: only
exception types and status codes.
"""

import logging

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from shared.logging import redact_http_secrets

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    # Not 429: SerpApi sends it when the month's searches run out, which a retry won't fix.
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


def _api_error(response: httpx.Response) -> str | None:
    try:
        body: object = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, str) else None


def describe_failure(exc: BaseException, service: str) -> str:
    """`exc` as a sentence for people, e.g. "Hotel search timed out."; `service` names the
    search ("Flight search")."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return f"{service} is misconfigured (the SerpApi key was rejected)."
        if status == 429:
            return (
                f"{service} is unavailable: the SerpApi search allowance is used up or "
                "rate limited."
            )
        error = _api_error(exc.response)
        if status < 500 and error:
            return f"{service} failed: {error}"
        return f"{service} failed (HTTP {status})."
    if isinstance(exc, httpx.TimeoutException):
        return f"{service} timed out."
    if isinstance(exc, httpx.TransportError):
        return f"Could not reach the {service.lower()} service."
    return f"{service} returned an unexpected response."


def loggable(exc: BaseException | None) -> str:
    # httpx's exception messages include the request URL, and with it the API key, so
    # only the type and status are ever logged.
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{type(exc).__name__} (HTTP {exc.response.status_code})"
    return type(exc).__name__


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class SerpApiClient:
    """GETs SerpApi's search endpoint, retrying transport failures and 5xx responses."""

    def __init__(
        self,
        *,
        api_key: str,
        name: str,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
        retry_wait_seconds: float = 0.5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # The key goes in the URL, which httpx logs for every request.
        redact_http_secrets()
        self._api_key = api_key
        self._name = name  # for the retry log, e.g. "flight search"
        self._max_attempts = max_attempts
        self._retry_wait_seconds = retry_wait_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    def _log_retry(self, state: RetryCallState) -> None:
        exc = state.outcome.exception() if state.outcome is not None else None
        logger.warning(
            "SerpApi %s attempt %d failed, retrying: %s",
            self._name,
            state.attempt_number,
            loggable(exc),
        )

    async def _get_once(self, params: dict[str, str]) -> httpx.Response:
        response = await self._client.get(SERPAPI_URL, params={**params, "api_key": self._api_key})
        response.raise_for_status()
        return response

    async def get(self, params: dict[str, str]) -> bytes:
        """The response body for `params`; raises `httpx.HTTPError` once retries run out."""
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=self._retry_wait_seconds, max=8),
            retry=retry_if_exception(is_transient),
            before_sleep=self._log_retry,
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                response = await self._get_once(params)
        return response.content

    async def aclose(self) -> None:
        await self._client.aclose()
