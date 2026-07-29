"""Token bucket timing, proven against a fake clock — no real sleeping."""

import pytest

from api.ingest.ratelimit import TokenBucket


class FakeClock:
    """sleep() advances the clock, so refill math is exercised for real."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def test_burst_up_to_capacity_is_free() -> None:
    fc = FakeClock()
    bucket = TokenBucket(rate=1.0, capacity=3.0, clock=fc.clock, sleep=fc.sleep)
    for _ in range(3):
        bucket.acquire()
    assert fc.sleeps == []


def test_blocks_for_the_refill_time() -> None:
    fc = FakeClock()
    bucket = TokenBucket(rate=2.0, capacity=1.0, clock=fc.clock, sleep=fc.sleep)
    bucket.acquire()  # empties the bucket
    bucket.acquire()  # needs 1 token at 2 tokens/s -> 0.5s (+ anti-spin margin)
    assert fc.sleeps == [pytest.approx(0.5, abs=1e-4)]


def test_idle_refill_caps_at_capacity() -> None:
    fc = FakeClock()
    bucket = TokenBucket(rate=10.0, capacity=2.0, clock=fc.clock, sleep=fc.sleep)
    fc.t += 100.0  # a long idle must not bank 1000 tokens
    bucket.acquire()
    bucket.acquire()
    assert fc.sleeps == []
    bucket.acquire()
    assert fc.sleeps == [pytest.approx(0.1, abs=1e-4)]


def test_sustained_rate_is_enforced() -> None:
    fc = FakeClock()
    bucket = TokenBucket(rate=5.0, capacity=1.0, clock=fc.clock, sleep=fc.sleep)
    for _ in range(11):
        bucket.acquire()
    # 11 requests through a 1-deep bucket at 5/s: 10 waits of 0.2s.
    assert fc.t == pytest.approx(2.0, abs=1e-3)


def test_acquire_beyond_capacity_is_rejected() -> None:
    fc = FakeClock()
    bucket = TokenBucket(rate=1.0, capacity=1.0, clock=fc.clock, sleep=fc.sleep)
    with pytest.raises(ValueError, match="capacity"):
        bucket.acquire(2.0)


def test_nonpositive_parameters_rejected() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate=0.0, capacity=1.0)
    with pytest.raises(ValueError):
        TokenBucket(rate=1.0, capacity=0.0)
