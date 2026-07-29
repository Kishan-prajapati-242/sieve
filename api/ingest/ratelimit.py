"""Per-source token bucket.

Every external API gets its own bucket sized to its documented limit, so a
burst against one source can never spend another source's budget (arXiv at
1 request per 3 seconds must not be starved by OpenAlex traffic). Tokens
refill continuously at `rate` per second; a full bucket allows a burst of
`capacity` requests, after which acquire() blocks for exactly the refill
time of the deficit.

clock and sleep are injectable so tests assert timing without real sleeping.
Single-threaded by design: the Phase 1 ingest script is synchronous. Phase 3
workers are separate processes, and a cross-process budget (arXiv's global
limit) will need coordination through Postgres — revisit there, not here.

Alternative rejected: a fixed sleep between requests. It serializes at the
worst-case interval and cannot use the burst headroom the APIs allow.
"""

import time
from collections.abc import Callable


class TokenBucket:
    def __init__(
        self,
        rate: float,
        capacity: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be positive")
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last = clock()
        self._clock = clock
        self._sleep = sleep

    def acquire(self, tokens: float = 1.0) -> None:
        """Block until `tokens` are available, then consume them."""
        if tokens > self._capacity:
            raise ValueError(f"cannot acquire {tokens} from a bucket of capacity {self._capacity}")
        while True:
            now = self._clock()
            self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return
            # The microsecond margin guarantees the clock moves past the
            # refill point: sleeping the exact deficit can round to less than
            # the clock's float resolution and spin this loop forever.
            self._sleep((tokens - self._tokens) / self._rate + 1e-6)
