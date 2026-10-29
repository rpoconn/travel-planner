import pytest

from shared.rate_limit import RateLimiter, create_rate_limiters


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def test_allows_up_to_the_limit_then_reports_the_wait(clock: Clock) -> None:
    limiter = RateLimiter(2, window_seconds=60, clock=clock)

    assert limiter.hit("a") is None
    clock.now += 10
    assert limiter.hit("a") is None
    clock.now += 5

    # The oldest hit (t=1000) leaves the window at t=1060; it's now t=1015.
    assert limiter.hit("a") == pytest.approx(45)


def test_window_slides(clock: Clock) -> None:
    limiter = RateLimiter(1, window_seconds=60, clock=clock)
    limiter.hit("a")

    clock.now += 60

    assert limiter.hit("a") is None


def test_rejected_hits_do_not_extend_the_wait(clock: Clock) -> None:
    limiter = RateLimiter(1, window_seconds=60, clock=clock)
    limiter.hit("a")
    for _ in range(5):
        clock.now += 10
        assert limiter.hit("a") is not None

    clock.now += 10  # 60s after the only accepted hit

    assert limiter.hit("a") is None


def test_keys_are_independent(clock: Clock) -> None:
    limiter = RateLimiter(1, clock=clock)

    assert limiter.hit("a") is None
    assert limiter.hit("b") is None
    assert limiter.hit("a") is not None


def test_idle_keys_are_swept(clock: Clock) -> None:
    limiter = RateLimiter(5, window_seconds=60, clock=clock)
    for index in range(999):
        limiter.hit(f"client-{index}")
    clock.now += 61

    limiter.hit("fresh")  # the 1000th call triggers a sweep

    assert set(limiter._hits) == {"fresh"}


def test_zero_limit_disables_the_bucket() -> None:
    limiters = create_rate_limiters({"chat": 5, "auth": 0})

    assert set(limiters) == {"chat"}
    assert limiters["chat"].limit == 5
