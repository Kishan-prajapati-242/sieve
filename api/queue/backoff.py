"""Retry delay for a failed job: exponential, with FULL jitter.

Same rule as the HTTP client (api/ingest/http.py) and for the same reason,
one level up: workers that fail together — a source goes down, the database
gets slow — must not come back together. Full jitter draws the delay
uniformly from [0, cap] rather than jittering around the cap, so a thundering
herd is spread across the whole window instead of clustered at its end.

The delay is computed in Python and written to run_after rather than
computed in SQL, so a test can inject a deterministic rng and assert the
exact timestamp a job will next be claimable at.
"""

import random
from collections.abc import Callable

BASE_DELAY_S = 2.0
MAX_DELAY_S = 600.0


def retry_delay_s(
    attempts: int,
    *,
    base: float = BASE_DELAY_S,
    cap: float = MAX_DELAY_S,
    rng: Callable[[], float] = random.random,
) -> float:
    """Seconds to wait before attempt number `attempts` + 1.

    attempts is the count of attempts ALREADY made, so the first retry
    (attempts=1) draws from [0, base], the second from [0, 2*base], and so
    on until the cap. A job that has never run has no delay to compute.
    """
    if attempts < 1:
        raise ValueError(f"retry_delay_s needs at least one failed attempt, got {attempts}")
    ceiling: float = min(cap, base * float(2 ** (attempts - 1)))
    delay: float = rng() * ceiling
    return delay
