"""Retry behavior of the shared HTTP helper, against a mock transport.

The transport is mocked because these tests are about OUR retry policy, not
the network; the database tests elsewhere stay mock-free per the working
agreement.
"""

import httpx
import pytest

from api.ingest.http import QuotaExhausted, RequestMeter, RetriesExhausted, get_json
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


def test_huge_retry_after_raises_quota_exhausted_without_retrying() -> None:
    """A Retry-After measured in hours means the daily budget is gone; six
    blind retries against it are pure waste and hide the reason."""
    client, calls = client_returning(
        [
            httpx.Response(
                429,
                headers={"Retry-After": "17000"},
                text='{"error":"Rate limit exceeded","message":"Insufficient budget"}',
            )
        ]
    )
    with pytest.raises(QuotaExhausted, match="Insufficient budget") as exc_info:
        get_json(client, "/w", params={}, bucket=free_bucket(), sleep=lambda s: None)
    assert exc_info.value.retry_after_s == 17000
    assert len(calls) == 1


def test_short_retry_after_is_honored_exactly() -> None:
    """Below the ceiling, the server's own cooldown wins over our jitter."""
    client, calls = client_returning(
        [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={"ok": 1})]
    )
    sleeps: list[float] = []
    out = get_json(
        client, "/w", params={}, bucket=free_bucket(), rng=lambda: 1.0, sleep=sleeps.append
    )
    assert out == {"ok": 1}
    assert sleeps == [7.0]
    assert len(calls) == 2


def test_failure_messages_keep_the_response_body() -> None:
    """The body is where the server explains itself; discarding it once
    turned a billing problem into a fake rate-limit mystery."""
    client, _ = client_returning([httpx.Response(500, text="disk melted, sorry")] * 2)
    with pytest.raises(RetriesExhausted, match="disk melted"):
        get_json(
            client,
            "/w",
            params={},
            bucket=free_bucket(),
            max_attempts=2,
            rng=lambda: 0.0,
            sleep=lambda s: None,
        )


def test_request_meter_counts_retries_and_captures_budget_headers() -> None:
    responses = [
        httpx.Response(500),
        httpx.Response(200, json={}, headers={"x-ratelimit-remaining": "874"}),
    ]
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return responses.pop(0)

    meter = RequestMeter()
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://t",
        event_hooks={"request": [meter.on_request], "response": [meter.on_response]},
    )
    get_json(client, "/w", params={}, bucket=free_bucket(), rng=lambda: 0.0, sleep=lambda s: None)
    assert meter.requests == 2  # the retry is attempted spend too
    assert meter.credits_spent == 2  # no filter param -> list class, 1 credit each
    assert meter.remaining == "874"


def test_request_meter_prices_by_billing_class() -> None:
    """Measured 2026-07-29: a search-filter page bills 10 credits, a plain
    list page 1, /rate-limit itself 0. Request counts hide the 10x."""
    meter = RequestMeter()
    for url in (
        "https://t/works?filter=concepts.id:C204321447,publication_year:2024",
        'https://t/works?filter=title_and_abstract.search:"clinical NLP",has_abstract:true',
        "https://t/rate-limit",
    ):
        meter.on_request(httpx.Request("GET", url))
    assert meter.credits_spent == 1 + 10 + 0
    assert meter.requests == 3


def test_request_meter_costs_can_be_overridden_by_server_table() -> None:
    meter = RequestMeter()
    meter.credit_costs.update({"search": 25})
    meter.on_request(httpx.Request("GET", "https://t/works?filter=abstract.search:x"))
    assert meter.credits_spent == 25
