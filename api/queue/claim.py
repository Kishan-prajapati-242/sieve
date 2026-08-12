"""Claiming and completing jobs. Raw SQL — this is the module to read.

The claim:

    UPDATE ingest_jobs SET status = 'running' ...
    WHERE id IN (
        SELECT id FROM ingest_jobs
        WHERE status = 'pending' AND run_after <= now()
        ORDER BY run_after, id
        FOR UPDATE SKIP LOCKED
        LIMIT n
    )
    RETURNING ...

Every clause is load-bearing:

  FOR UPDATE takes a row lock, so two workers cannot select the same row.
  Without it, both would read the same pending rows and both would write
  status='running' — the second UPDATE blocks, then succeeds, and the job
  runs twice.

  SKIP LOCKED is what makes it a queue rather than a line. Without it the
  second worker BLOCKS on the first worker's locked row and waits for the
  transaction to end; with it, the second worker steps over locked rows and
  claims different ones. Throughput becomes N workers instead of 1.

  The subquery exists because FOR UPDATE cannot appear in an UPDATE. It
  also bounds the lock to LIMIT n rows rather than everything the WHERE
  matches.

  ORDER BY run_after, id makes the queue FIFO within a due-time, and gives
  SKIP LOCKED a deterministic scan order so workers collide on the head of
  the queue and skip forward, rather than scattering. It orders which rows
  are SELECTED; the outer UPDATE's RETURNING emits them in whatever order
  it processed them, which is unspecified and does not matter.

  run_after <= now() is the whole backoff mechanism: a failed job is
  rescheduled by pushing run_after into the future, and it is invisible to
  the claim until then. No separate delay queue.

Completion is the other half of the contract. `complete()` and the job's
own writes happen in ONE transaction: a worker that dies between doing the
work and marking it done rolls BOTH back, and the job is simply claimed
again. That is why the queue is a table in the same database as the data —
the alternative is two systems and a distributed-commit problem.

A crashed worker leaves a row 'running' with locked_at set and no
transaction holding it. reap_stale() is the recovery path: it returns such
rows to 'pending' after a timeout. It counts the attempt, because a job
that reliably kills its worker must eventually reach 'dead' rather than
cycling forever.
"""

from datetime import timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from api.queue.backoff import retry_delay_s

CLAIM_SQL = """
UPDATE ingest_jobs j
SET status = 'running',
    attempts = j.attempts + 1,
    locked_at = now(),
    locked_by = %(worker)s,
    updated_at = now()
WHERE j.id IN (
    SELECT id FROM ingest_jobs
    WHERE status = 'pending' AND run_after <= now()
    ORDER BY run_after, id
    FOR UPDATE SKIP LOCKED
    LIMIT %(limit)s
)
RETURNING j.id, j.job_type, j.payload, j.attempts, j.max_attempts
"""

ENQUEUE_SQL = """
INSERT INTO ingest_jobs (job_type, payload, dedupe_key, max_attempts, run_after)
VALUES (%(job_type)s, %(payload)s, %(dedupe_key)s, %(max_attempts)s,
        now() + make_interval(secs => %(delay_s)s))
ON CONFLICT (dedupe_key) DO NOTHING
RETURNING id
"""

COMPLETE_SQL = """
UPDATE ingest_jobs
SET status = 'done', locked_at = NULL, locked_by = NULL, updated_at = now()
WHERE id = %(id)s AND status = 'running'
RETURNING id
"""

# One statement decides retry-vs-dead, so the two cannot disagree: a job at
# its attempt ceiling goes to 'dead', anything else goes back to 'pending'
# with run_after pushed out by the caller's jittered delay.
FAIL_SQL = """
UPDATE ingest_jobs
SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
    run_after = CASE WHEN attempts >= max_attempts THEN run_after
                     ELSE now() + make_interval(secs => %(delay_s)s) END,
    last_error = %(error)s,
    locked_at = NULL,
    locked_by = NULL,
    updated_at = now()
WHERE id = %(id)s
RETURNING status, attempts, run_after
"""

REAP_SQL = """
UPDATE ingest_jobs
SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
    last_error = COALESCE(last_error, '') || %(note)s,
    locked_at = NULL,
    locked_by = NULL,
    updated_at = now()
WHERE status = 'running' AND locked_at < now() - make_interval(secs => %(timeout_s)s)
RETURNING id, status
"""


def enqueue(
    conn: psycopg.Connection,
    *,
    job_type: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
    max_attempts: int = 5,
    delay_s: float = 0.0,
) -> int | None:
    """Add a job. Returns its id, or None when dedupe_key already exists.

    None is a normal outcome, not an error: re-running an enqueuer after a
    crash is supposed to be a no-op.
    """
    row = conn.execute(
        ENQUEUE_SQL,
        {
            "job_type": job_type,
            "payload": Jsonb(payload),
            "dedupe_key": dedupe_key,
            "max_attempts": max_attempts,
            "delay_s": delay_s,
        },
    ).fetchone()
    return int(row[0]) if row else None


def claim(conn: psycopg.Connection, *, worker: str, limit: int = 1) -> list[dict[str, Any]]:
    """Claim up to `limit` due jobs for this worker. Never blocks on another
    worker's rows — it steps over them."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(CLAIM_SQL, {"worker": worker, "limit": limit})
        return cur.fetchall()


def complete(conn: psycopg.Connection, job_id: int) -> bool:
    """Mark a claimed job done. Must run in the SAME transaction as the work.

    Returns False when the row was not 'running' — it was reaped out from
    under this worker, and the work has been or will be redone by someone
    else. The caller should roll back rather than commit half a job.
    """
    return conn.execute(COMPLETE_SQL, {"id": job_id}).fetchone() is not None


def fail(
    conn: psycopg.Connection,
    job_id: int,
    error: str,
    *,
    attempts: int,
    rng: Any = None,
) -> str:
    """Record a failure: reschedule with jittered backoff, or dead-letter.

    Returns the new status. The error text is truncated because last_error
    holds a stack trace and the column is read by humans and /api/stats.
    """
    delay = retry_delay_s(attempts, **({"rng": rng} if rng else {}))
    row = conn.execute(FAIL_SQL, {"id": job_id, "error": error[:2000], "delay_s": delay}).fetchone()
    assert row is not None, f"fail() on a job that does not exist: {job_id}"
    return str(row[0])


def reap_stale(
    conn: psycopg.Connection, *, timeout: timedelta = timedelta(minutes=15)
) -> list[tuple[int, str]]:
    """Return jobs whose worker died holding them.

    A killed worker's transaction aborts, so its writes are gone, but the
    'running' row was committed by the claim and stays. Without this, one
    SIGKILL removes a job from the queue permanently. The attempt was
    already counted at claim time, so a job that reliably kills workers
    walks to 'dead' instead of cycling forever.
    """
    rows = conn.execute(
        REAP_SQL,
        {
            "timeout_s": timeout.total_seconds(),
            "note": f" [reaped: no heartbeat for {timeout}]",
        },
    ).fetchall()
    return [(int(r[0]), str(r[1])) for r in rows]


def queue_depth(conn: psycopg.Connection) -> dict[str, int]:
    """Counts by status, for /api/stats and for tests."""
    rows = conn.execute("SELECT status, count(*) FROM ingest_jobs GROUP BY status").fetchall()
    return {str(status): int(count) for status, count in rows}
