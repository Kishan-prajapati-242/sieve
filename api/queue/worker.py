"""The worker loop: claim a job, run it, commit the work and the completion
together.

The transaction boundary is the whole design. One transaction covers:

    claim -> handler writes -> complete

so there is no window where the work is durable and the job is not, or the
job is done and the work is not. A worker killed at any instant leaves the
job exactly as it was before the claim... except for one committed fact: the
claim itself. That is deliberate — the claim commits so that other workers
can see the row is taken, which is what `reap_stale` then has to undo. The
alternative (holding the row lock for the job's whole duration) makes a slow
job block the reaper and turns a crashed worker into a permanently locked
row that nothing can observe.

Failure handling runs in its OWN transaction, after the work transaction has
rolled back. Recording "this failed" inside the aborted transaction would
roll back with it, and the job would look untouched: attempts never
increments, backoff never applies, and a poison job spins forever.

Handlers are registered by job_type and receive (conn, payload). They must
write through the connection they are given — a handler that opens its own
connection escapes the transaction and breaks the guarantee above.

Shutdown is cooperative: SIGTERM/SIGINT set a flag, the loop finishes the
job in flight and exits. `docker compose stop` and Kubernetes both send
SIGTERM and then wait, so a graceful worker loses nothing on a normal
deploy; only SIGKILL needs the reaper.
"""

import logging
import os
import signal
import time
from collections.abc import Callable, Mapping
from types import FrameType
from typing import Any

import psycopg

from api.queue.claim import claim, complete, fail

logger = logging.getLogger(__name__)

Handler = Callable[[psycopg.Connection, dict[str, Any]], None]

POLL_INTERVAL_S = 1.0


class Worker:
    """One process's worth of queue consumption.

    `name` lands in ingest_jobs.locked_by, so a stuck job names the process
    holding it. Default is host:pid, which is unique across a compose scale-up.
    """

    def __init__(
        self,
        conninfo: str,
        handlers: Mapping[str, Handler],
        *,
        name: str | None = None,
        poll_interval_s: float = POLL_INTERVAL_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.conninfo = conninfo
        self.handlers = handlers
        self.name = name or f"{os.uname().nodename}:{os.getpid()}"
        self.poll_interval_s = poll_interval_s
        self.sleep = sleep
        self.stopping = False
        self.processed = 0
        self.failed = 0

    def install_signal_handlers(self) -> None:
        def stop(signum: int, _frame: FrameType | None) -> None:
            logger.info("worker stopping", extra={"worker": self.name, "signal": signum})
            self.stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

    def run_once(self, conn: psycopg.Connection) -> bool:
        """Claim and run at most one job. Returns whether one was found."""
        jobs = claim(conn, worker=self.name, limit=1)
        if not jobs:
            return False
        job = jobs[0]
        handler = self.handlers.get(job["job_type"])
        if handler is None:
            # Not retryable: no amount of waiting adds a handler. Burn the
            # attempts so it dead-letters immediately rather than after five
            # pointless rounds of backoff.
            with conn.transaction():
                conn.execute(
                    "UPDATE ingest_jobs SET attempts = max_attempts WHERE id = %s", (job["id"],)
                )
                fail(
                    conn,
                    job["id"],
                    f"no handler for job_type {job['job_type']!r}",
                    attempts=job["max_attempts"],
                )
            self.failed += 1
            return True

        try:
            with conn.transaction():
                handler(conn, job["payload"])
                if not complete(conn, job["id"]):
                    # Reaped mid-flight: someone else owns this job now, so
                    # this transaction's writes must not land.
                    raise StaleClaim(job["id"])
        except StaleClaim:
            logger.warning("claim went stale", extra={"job_id": job["id"], "worker": self.name})
            self.failed += 1
        except Exception as exc:  # noqa: BLE001 — the queue's whole job is to survive these
            # Separate transaction: the one above is aborted, and a failure
            # recorded inside it would roll back with the work.
            with conn.transaction():
                status = fail(
                    conn, job["id"], f"{type(exc).__name__}: {exc}", attempts=job["attempts"]
                )
            logger.exception(
                "job failed", extra={"job_id": job["id"], "status": status, "worker": self.name}
            )
            self.failed += 1
        else:
            self.processed += 1
        return True

    def run(self, *, max_jobs: int | None = None, max_idle_polls: int | None = None) -> None:
        """Poll until stopped. max_jobs and max_idle_polls exist for tests
        and for one-shot drains; a long-running worker passes neither."""
        idle = 0
        with psycopg.connect(self.conninfo, autocommit=True) as conn:
            while not self.stopping:
                if self.run_once(conn):
                    idle = 0
                    if max_jobs is not None and self.processed + self.failed >= max_jobs:
                        return
                    continue
                idle += 1
                if max_idle_polls is not None and idle >= max_idle_polls:
                    return
                self.sleep(self.poll_interval_s)


class StaleClaim(Exception):
    """The job stopped being ours between claiming it and finishing it."""
