"""One HTTP GET with the house rules applied.

Every external call in this project goes through get_json: token-bucket
acquire first (retries spend rate budget like any other request), explicit
timeout (set on the client, never omitted), and retry with FULL jitter —
each delay is uniform in [0, min(cap, base * 2^attempt)]. The uniform draw
is the point: clients that failed together and back off on the same fixed
schedule retry together, re-creating the very spike that failed them.

Retryable: transport errors (timeouts, resets, DNS) plus 429 and 5xx.
Any other 4xx is a bug in our request and raises immediately — retrying a
400 five times just spends rate budget hiding the bug.

rng and sleep are injectable for tests.
"""

import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from api.ingest.ratelimit import TokenBucket

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class RetriesExhausted(Exception):
    """Every attempt failed with a retryable error; the message has the last."""


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any],
    bucket: TokenBucket,
    max_attempts: int = 6,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    rng: Callable[[], float] = random.random,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    last_failure = ""
    for attempt in range(max_attempts):
        bucket.acquire()
        try:
            resp = client.get(url, params=params)
        except httpx.TransportError as exc:
            last_failure = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code in RETRYABLE_STATUSES:
                last_failure = f"HTTP {resp.status_code}"
            else:
                resp.raise_for_status()  # non-retryable 4xx: our bug, fail now
                data: dict[str, Any] = resp.json()
                return data
        if attempt < max_attempts - 1:
            sleep(rng() * min(max_delay, base_delay * 2**attempt))
    raise RetriesExhausted(f"GET {url}: {max_attempts} attempts, last failure: {last_failure}")
