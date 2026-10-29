"""In-memory sliding-window rate limiting.

Limits are per process, which fits this single-process local app; a multi-worker deployment
would need a shared store (e.g. Redis) behind the same `RateLimiter` interface.
"""

import logging
import math
import time
from collections import deque
from collections.abc import Callable

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

# Buckets the app configures in `create_app` (see `Settings.*_rate_limit_per_minute`).
CHAT_BUCKET = "chat"
AUTH_BUCKET = "auth"

# Drop keys with no recent hits every this many calls, so one-off clients don't pile up.
_SWEEP_EVERY = 1000


# ---------------------------------------------------------------------------
# Limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Allows `limit` hits per key in any rolling `window_seconds`."""

    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._calls = 0

    def hit(self, key: str) -> float | None:
        """Record a hit for `key`. Returns None if allowed, else seconds until it would be."""
        now = self._clock()
        self._calls += 1
        if self._calls % _SWEEP_EVERY == 0:
            self._sweep(now)

        hits = self._hits.setdefault(key, deque())
        self._expire(hits, now)
        if len(hits) >= self.limit:
            # Rejected hits aren't recorded, so a client that keeps retrying isn't locked
            # out for longer than the window.
            return max(hits[0] + self.window_seconds - now, 0.0)
        hits.append(now)
        return None

    def _expire(self, hits: deque[float], now: float) -> None:
        while hits and hits[0] <= now - self.window_seconds:
            hits.popleft()

    def _sweep(self, now: float) -> None:
        for key in list(self._hits):
            self._expire(self._hits[key], now)
            if not self._hits[key]:
                del self._hits[key]


# ---------------------------------------------------------------------------
# FastAPI helpers
# ---------------------------------------------------------------------------


def enforce_rate_limit(request: Request, bucket: str, key: str, detail: str) -> None:
    """Raise 429 (with `Retry-After`) if `key` is over the app's limit for `bucket`.

    Buckets with no limiter on `app.state.rate_limiters` (limit set to 0) are unlimited.
    """
    limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
    limiter = limiters.get(bucket)
    if limiter is None:
        return
    retry_after = limiter.hit(key)
    if retry_after is None:
        return
    seconds = max(math.ceil(retry_after), 1)
    logger.warning("Rate limit hit (bucket=%s, key=%s)", bucket, key)
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"{detail} Try again in {seconds} second{'s' if seconds != 1 else ''}.",
        headers={"Retry-After": str(seconds)},
    )


def client_ip(request: Request) -> str:
    # The app is served directly (no proxy), so the socket peer is the client. Behind a
    # proxy, run uvicorn with --proxy-headers so this reflects X-Forwarded-For.
    return request.client.host if request.client else "unknown"


def create_rate_limiters(limits: dict[str, int]) -> dict[str, RateLimiter]:
    """One per-minute limiter per bucket; a limit of 0 disables that bucket."""
    return {bucket: RateLimiter(limit) for bucket, limit in limits.items() if limit > 0}
