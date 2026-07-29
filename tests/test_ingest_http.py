"""Retry behavior of the shared HTTP helper, against a mock transport.

The transport is mocked because these tests are about OUR retry policy, not
the network; the database tests elsewhere stay mock-free per the working
agreement.
"""

import httpx
import pytest

from api.ingest.http import RetriesExhausted, get_json
from api.ingest.ratelimit import TokenBucket


def free_bucket() -> TokenBucket:
    # Effectively unlimited: these tests assert retry logic, not rate math.
    return TokenBucket(rate=1e9, capacity=1e9)


def client_returning(responses: list[httpx.Response | Exception]) -> tuple[httpx.Client, list[int]]:
    """A client whose transport pops canned responses; also counts calls."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://t"), calls


def test_transient_500s_are_retried_then_succeed() -> None:
    client, calls = client_returning(
        [httpx.Response(500), httpx.Response(503), httpx.Response(200, json={"ok": True})]
    )
    sleeps: list[float] = []
    out = get_json(
        client, "/w", params={}, bucket=free_bucket(), rng=lambda: 1.0, sleep=sleeps.append
    )
    assert out == {"ok": True}
    assert len(calls) == 3
    # rng pinned to 1.0 exposes the raw exponential schedule: base 1s, then 2s.
    assert sleeps == [pytest.approx(1.0), pytest.approx(2.0)]


def test_timeouts_are_retryable() -> None:
    client, calls = client_returning(
        [httpx.ConnectTimeout("boom"), httpx.Response(200, json={"n": 1})]
    )
    out = get_json(
        client, "/w", params={}, bucket=free_bucket(), rng=lambda: 0.0, sleep=lambda s: None
    )
    assert out == {"n": 1}
    assert len(calls) == 2


def test_full_jitter_can_draw_zero_delay() -> None:
    client, _ = client_returning([httpx.Response(429), httpx.Response(200, json={})])
    sleeps: list[float] = []
    get_json(client, "/w", params={}, bucket=free_bucket(), rng=lambda: 0.0, sleep=sleeps.append)
    assert sleeps == [0.0]


def test_delays_are_capped() -> None:
    client, calls = client_returning([httpx.Response(500)] * 6)
    sleeps: list[float] = []
    with pytest.raises(RetriesExhausted, match="HTTP 500"):
        get_json(
            client,
            "/w",
            params={},
            bucket=free_bucket(),
            max_attempts=6,
            base_delay=1.0,
            max_delay=4.0,
            rng=lambda: 1.0,
            sleep=sleeps.append,
        )
    assert len(calls) == 6
    # 1, 2, 4, then pinned at the 4s cap; no sleep after the final attempt.
    assert sleeps == [pytest.approx(d) for d in (1.0, 2.0, 4.0, 4.0, 4.0)]


def test_client_errors_raise_immediately_without_retry() -> None:
    client, calls = client_returning([httpx.Response(404)])
    with pytest.raises(httpx.HTTPStatusError):
        get_json(
            client, "/w", params={}, bucket=free_bucket(), rng=lambda: 0.0, sleep=lambda s: None
        )
    assert len(calls) == 1
