"""One HTTP GET with the house rules applied.

Every external call in this project goes through get_json: token-bucket
acquire first (retries spend rate budget like any other request), explicit
timeout (set on the client, never omitted), and retry with FULL jitter —
each delay is uniform in [0, min(cap, base * 2^attempt)]. The uniform draw
is the point: clients that failed together and back off on the same fixed
schedule retry together, re-creating the very spike that failed them.

429 is special-cased because it carries a Retry-After header that we honor
(the server knows its own cooldown better than our jitter does). A
Retry-After beyond `retry_after_ceiling` means the quota is gone for hours
— OpenAlex's daily budget resets at midnight UTC — so we raise
QuotaExhausted immediately instead of burning six blind retries against a
dead budget. Failure messages keep a slice of the response body: the body
is where OpenAlex explains itself ("Insufficient budget..."), and
discarding it once turned a billing problem into a fake rate mystery.

Other 5xx and transport errors keep the jitter schedule; any other 4xx is
a bug in our request and raises immediately.

get_response holds the retry loop; get_json and get_text are one-line
wrappers over it. PubMed's efetch returns XML and nothing else, and a
second retry loop written beside this one would drift from it — arXiv's
client already skips retries entirely because it predates this helper
(its pages are idempotent GETs, so a failure costs a rerun, not data).

rng and sleep are injectable for tests.
"""

import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from api.ingest.ratelimit import TokenBucket

RETRYABLE_STATUSES = frozenset({500, 502, 503, 504})


class RetriesExhausted(Exception):
    """Every attempt failed with a retryable error; the message has the last."""


class QuotaExhausted(Exception):
    """The server's Retry-After exceeds our ceiling: the daily budget is gone.

    retry_after_s is the server-reported wait; callers should stop the run
    cleanly and tell the operator when to come back, not keep retrying.
    """

    def __init__(self, message: str, retry_after_s: float) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


# Measured 2026-07-29 by bracketing one request of each shape with free
# /rate-limit reads; matches the server's own credit_costs table. Overridden
# at runtime by whatever /rate-limit currently declares.
DEFAULT_CREDIT_COSTS = {
    "singleton": 0,
    "list": 1,
    "search": 10,
    "content": 100,
    "semantic": 10,
    "text": 100,
}


class RequestMeter:
    """httpx event hooks: tracks CREDITS, not just request count.

    OpenAlex bills per request by endpoint class — a search-filter page
    costs 10 credits where a plain list page costs 1 (measured 2026-07-29,
    page size irrelevant) — so raw request counts hide a 10x spend
    difference. Requests are classified the way OpenAlex bills them and
    priced from the /rate-limit credit_costs table. The meter records
    attempted spend (a 429'd request is counted even though the server
    could not bill it); the x-ratelimit-remaining header captured from
    responses is the server's truth to reconcile against.
    """

    def __init__(self, credit_costs: dict[str, int] | None = None) -> None:
        self.credit_costs = dict(credit_costs or DEFAULT_CREDIT_COSTS)
        self.requests = 0
        self.credits_spent = 0
        self.remaining: str | None = None
        self.reset_seconds: str | None = None

    @staticmethod
    def billing_class(request: httpx.Request) -> str:
        if request.url.path == "/rate-limit":
            return "singleton"
        # Search-class is any request that searches, not just works filters:
        # a bare `search=` param (entity search, works full-text) bills 10x
        # too — measured 2026-07-29, the meter predicted 8 credits for a
        # /topics?search= discovery run the server billed 80 for.
        if "search" in request.url.params or ".search:" in request.url.params.get("filter", ""):
            return "search"
        return "list"

    def on_request(self, request: httpx.Request) -> None:
        self.requests += 1
        self.credits_spent += self.credit_costs.get(self.billing_class(request), 1)

    def on_response(self, response: httpx.Response) -> None:
        self.remaining = response.headers.get("x-ratelimit-remaining", self.remaining)
        self.reset_seconds = response.headers.get("x-ratelimit-reset", self.reset_seconds)


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers["retry-after"])
    except (KeyError, ValueError):
        return None


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any],
    bucket: TokenBucket,
    max_attempts: int = 6,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_after_ceiling: float = 300.0,
    rng: Callable[[], float] = random.random,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    data: dict[str, Any] = get_response(
        client,
        url,
        params=params,
        bucket=bucket,
        max_attempts=max_attempts,
        base_delay=base_delay,
        max_delay=max_delay,
        retry_after_ceiling=retry_after_ceiling,
        rng=rng,
        sleep=sleep,
    ).json()
    return data


def get_text(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any],
    bucket: TokenBucket,
    max_attempts: int = 6,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_after_ceiling: float = 300.0,
    rng: Callable[[], float] = random.random,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Same rules, XML body. PubMed's efetch speaks XML and nothing else,
    and a second retry loop written next to this one would drift from it."""
    return get_response(
        client,
        url,
        params=params,
        bucket=bucket,
        max_attempts=max_attempts,
        base_delay=base_delay,
        max_delay=max_delay,
        retry_after_ceiling=retry_after_ceiling,
        rng=rng,
        sleep=sleep,
    ).text


def get_response(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any],
    bucket: TokenBucket,
    max_attempts: int = 6,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_after_ceiling: float = 300.0,
    rng: Callable[[], float] = random.random,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    last_failure = ""
    for attempt in range(max_attempts):
        bucket.acquire()
        delay: float | None = None
        try:
            resp = client.get(url, params=params)
        except httpx.TransportError as exc:
            last_failure = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code == 429:
                last_failure = f"HTTP 429: {resp.text[:200]}"
                retry_after = _retry_after_seconds(resp)
                if retry_after is not None:
                    if retry_after > retry_after_ceiling:
                        raise QuotaExhausted(
                            f"{last_failure} (server asks for {retry_after:.0f}s,"
                            f" ceiling is {retry_after_ceiling:.0f}s)",
                            retry_after_s=retry_after,
                        )
                    delay = retry_after
            elif resp.status_code in RETRYABLE_STATUSES:
                last_failure = f"HTTP {resp.status_code}: {resp.text[:200]}"
            else:
                resp.raise_for_status()  # non-retryable 4xx: our bug, fail now
                return resp
        if attempt < max_attempts - 1:
            if delay is None:
                delay = rng() * min(max_delay, base_delay * 2**attempt)
            sleep(delay)
    raise RetriesExhausted(f"GET {url}: {max_attempts} attempts, last failure: {last_failure}")
