"""The queue's contract, against a real Postgres.

Mocks would prove nothing here: every property under test — SKIP LOCKED
disjointness, the transaction boundary, backoff visibility — IS database
behavior. So these run against a migrated scratch database with real
concurrent connections.
"""

import threading
from datetime import timedelta

import psycopg
import pytest

from api.db.migrate import migrate
from api.queue.backoff import retry_delay_s
from api.queue.claim import claim, complete, enqueue, fail, queue_depth, reap_stale
from api.queue.worker import Worker


@pytest.fixture
def queue_db(scratch_db: str) -> str:
    migrate(scratch_db)
    return scratch_db


def add(conn: psycopg.Connection, n: int = 1, **kw: object) -> list[int]:
    ids = []
    for i in range(n):
        job_id = enqueue(
            conn,
            job_type=str(kw.pop("job_type", "noop")),
            payload={"i": i},
            dedupe_key=f"{kw.get('prefix', 'k')}-{i}",
            **{k: v for k, v in kw.items() if k in ("max_attempts", "delay_s")},  # type: ignore[arg-type]
        )
        if job_id:
            ids.append(job_id)
    return ids


def test_enqueue_is_idempotent_on_dedupe_key(queue_db: str) -> None:
    """Re-running an enqueuer after a crash must add nothing."""
    with psycopg.connect(queue_db, autocommit=True) as conn:
        first = enqueue(conn, job_type="fetch", payload={"page": 1}, dedupe_key="arxiv:cl:1")
        again = enqueue(conn, job_type="fetch", payload={"page": 1}, dedupe_key="arxiv:cl:1")
        assert first is not None
        assert again is None
        assert queue_depth(conn) == {"pending": 1}


def test_null_dedupe_keys_never_collide(queue_db: str) -> None:
    """NULL is distinct from NULL in a UNIQUE index — jobs that should run
    twice can."""
    with psycopg.connect(queue_db, autocommit=True) as conn:
        a = enqueue(conn, job_type="t", payload={})
        b = enqueue(conn, job_type="t", payload={})
        assert a is not None and b is not None and a != b


def test_concurrent_workers_claim_disjoint_jobs(queue_db: str) -> None:
    """The Phase 3 acceptance criterion. Eight threads, each on its own
    connection, race for 200 jobs; every job goes to exactly one."""
    with psycopg.connect(queue_db, autocommit=True) as conn:
        add(conn, 200)

    claimed: list[list[int]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker(n: int) -> None:
        mine: list[int] = []
        with psycopg.connect(queue_db, autocommit=True) as conn:
            barrier.wait()  # start together: maximize the collision window
            while True:
                jobs = claim(conn, worker=f"w{n}", limit=5)
                if not jobs:
                    break
                mine.extend(j["id"] for j in jobs)
        with lock:
            claimed.append(mine)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    flat = [job_id for batch in claimed for job_id in batch]
    assert len(flat) == 200, "every job claimed exactly once"
    assert len(set(flat)) == 200, "no job claimed twice"
    assert sum(1 for batch in claimed if batch) > 1, "work actually spread across workers"


def test_skip_locked_does_not_block_on_a_held_row(queue_db: str) -> None:
    """Without SKIP LOCKED the second claim would wait for the first
    transaction; with it, it steps over and takes the next job."""
    with psycopg.connect(queue_db, autocommit=True) as conn:
        add(conn, 2)

    with (
        psycopg.connect(queue_db) as holder,  # NOT autocommit: holds its lock
        psycopg.connect(queue_db, autocommit=True) as other,
    ):
        held = claim(holder, worker="holder", limit=1)
        assert len(held) == 1
        # The holder's transaction is still open, so its row is locked.
        stepped_over = claim(other, worker="other", limit=1)
        assert len(stepped_over) == 1
        assert stepped_over[0]["id"] != held[0]["id"]
        holder.rollback()


def test_a_job_that_fails_five_times_lands_in_dead(queue_db: str) -> None:
    """The brief's dead-lettering criterion, walked one attempt at a time."""
    with psycopg.connect(queue_db, autocommit=True) as conn:
        enqueue(conn, job_type="poison", payload={}, dedupe_key="p", max_attempts=5)
        statuses = []
        for _ in range(5):
            conn.execute("UPDATE ingest_jobs SET run_after = now()")  # skip the backoff wait
            job = claim(conn, worker="w", limit=1)[0]
            statuses.append(fail(conn, job["id"], "boom", attempts=job["attempts"]))
        assert statuses == ["pending", "pending", "pending", "pending", "dead"]
        assert queue_depth(conn) == {"dead": 1}
        row = conn.execute("SELECT attempts, last_error FROM ingest_jobs").fetchone()
        assert row is not None and row[0] == 5 and "boom" in row[1]


def test_a_dead_job_is_not_claimable(queue_db: str) -> None:
    with psycopg.connect(queue_db, autocommit=True) as conn:
        enqueue(conn, job_type="t", payload={}, dedupe_key="d", max_attempts=1)
        job = claim(conn, worker="w", limit=1)[0]
        assert fail(conn, job["id"], "boom", attempts=job["attempts"]) == "dead"
        assert claim(conn, worker="w", limit=1) == []


def test_backoff_hides_a_failed_job_until_run_after(queue_db: str) -> None:
    """run_after IS the delay queue: a rescheduled job is invisible to the
    claim, without a second mechanism."""
    with psycopg.connect(queue_db, autocommit=True) as conn:
        enqueue(conn, job_type="t", payload={}, dedupe_key="b")
        job = claim(conn, worker="w", limit=1)[0]
        fail(conn, job["id"], "boom", attempts=job["attempts"], rng=lambda: 1.0)
        assert queue_depth(conn) == {"pending": 1}
        assert claim(conn, worker="w", limit=1) == [], "still backing off"
        conn.execute("UPDATE ingest_jobs SET run_after = now()")
        assert len(claim(conn, worker="w", limit=1)) == 1


def test_retry_delay_is_full_jitter_not_fixed_backoff() -> None:
    """Uniform in [0, cap], so workers that failed together return spread
    out. A fixed schedule re-creates the spike that caused the failure."""
    assert retry_delay_s(1, rng=lambda: 1.0) == 2.0
    assert retry_delay_s(1, rng=lambda: 0.0) == 0.0  # the whole window is live
    assert retry_delay_s(3, rng=lambda: 1.0) == 8.0  # 2 * 2^2
    assert retry_delay_s(50, rng=lambda: 1.0) == 600.0  # capped
    with pytest.raises(ValueError, match="at least one failed attempt"):
        retry_delay_s(0)


def test_work_and_completion_commit_together(queue_db: str) -> None:
    """The reason the queue lives in the same database as the data: a
    handler that raises after writing leaves NEITHER the write nor the
    completion."""
    with psycopg.connect(queue_db, autocommit=True) as conn:
        conn.execute("CREATE TABLE side_effect (v int)")
        enqueue(conn, job_type="halfway", payload={}, dedupe_key="h")

    def handler(conn: psycopg.Connection, _payload: dict[str, object]) -> None:
        conn.execute("INSERT INTO side_effect VALUES (1)")
        raise RuntimeError("died after writing")

    worker = Worker(queue_db, {"halfway": handler}, name="w1")
    with psycopg.connect(queue_db, autocommit=True) as conn:
        worker.run_once(conn)
        rows = conn.execute("SELECT count(*) FROM side_effect").fetchone()
        status = conn.execute("SELECT status, attempts, last_error FROM ingest_jobs").fetchone()

    assert rows == (0,), "the handler's write rolled back with the failure"
    assert status is not None
    assert status[0] == "pending" and status[1] == 1
    assert "died after writing" in status[2], "and the failure itself was recorded"


def test_a_successful_job_commits_its_writes_and_marks_done(queue_db: str) -> None:
    with psycopg.connect(queue_db, autocommit=True) as conn:
        conn.execute("CREATE TABLE side_effect (v int)")
        enqueue(conn, job_type="ok", payload={"v": 42}, dedupe_key="o")

    def handler(conn: psycopg.Connection, payload: dict[str, object]) -> None:
        conn.execute("INSERT INTO side_effect VALUES (%s)", (payload["v"],))

    worker = Worker(queue_db, {"ok": handler}, name="w1")
    with psycopg.connect(queue_db, autocommit=True) as conn:
        worker.run_once(conn)
        assert conn.execute("SELECT v FROM side_effect").fetchone() == (42,)
        assert queue_depth(conn) == {"done": 1}
    assert (worker.processed, worker.failed) == (1, 0)


def test_an_unknown_job_type_dead_letters_immediately(queue_db: str) -> None:
    """No amount of backoff adds a handler, so it should not spend five
    rounds discovering that."""
    with psycopg.connect(queue_db, autocommit=True) as conn:
        enqueue(conn, job_type="mystery", payload={}, dedupe_key="m")
        Worker(queue_db, {}, name="w1").run_once(conn)
        row = conn.execute("SELECT status, last_error FROM ingest_jobs").fetchone()
    assert row is not None and row[0] == "dead"
    assert "no handler" in row[1]


def test_reap_returns_a_killed_workers_job_to_the_queue(queue_db: str) -> None:
    """Kill a worker mid-run and restart it: no lost jobs. The claim was
    committed, so only the reaper can free the row."""
    with psycopg.connect(queue_db, autocommit=True) as conn:
        enqueue(conn, job_type="t", payload={}, dedupe_key="r")
        job = claim(conn, worker="doomed", limit=1)[0]
        # The worker is now gone. Its row stays 'running' forever.
        assert claim(conn, worker="next", limit=1) == []
        conn.execute("UPDATE ingest_jobs SET locked_at = now() - interval '1 hour'")
        assert reap_stale(conn, timeout=timedelta(minutes=15)) == [(job["id"], "pending")]
        recovered = claim(conn, worker="next", limit=1)
        assert len(recovered) == 1 and recovered[0]["id"] == job["id"]
        assert recovered[0]["attempts"] == 2, "the failed attempt still counts"


def test_reap_dead_letters_a_job_that_keeps_killing_workers(queue_db: str) -> None:
    with psycopg.connect(queue_db, autocommit=True) as conn:
        enqueue(conn, job_type="t", payload={}, dedupe_key="r", max_attempts=1)
        claim(conn, worker="doomed", limit=1)
        conn.execute("UPDATE ingest_jobs SET locked_at = now() - interval '1 hour'")
        assert [s for _, s in reap_stale(conn, timeout=timedelta(minutes=15))] == ["dead"]


def test_complete_refuses_a_job_that_was_reaped_away(queue_db: str) -> None:
    """If the row is no longer 'running', someone else owns the work now and
    this transaction must not commit."""
    with psycopg.connect(queue_db, autocommit=True) as conn:
        enqueue(conn, job_type="t", payload={}, dedupe_key="c")
        job = claim(conn, worker="w", limit=1)[0]
        conn.execute("UPDATE ingest_jobs SET status = 'pending' WHERE id = %s", (job["id"],))
        assert complete(conn, job["id"]) is False


def test_claim_takes_the_oldest_jobs_first(queue_db: str) -> None:
    """FIFO is about WHICH rows the claim selects, not the order RETURNING
    hands them back: an UPDATE ... RETURNING emits rows in whatever order it
    processed them, which is unspecified. Assert the set, not the sequence —
    an earlier version of this test asserted the sequence and failed on a
    claim that was behaving correctly."""
    with psycopg.connect(queue_db, autocommit=True) as conn:
        ids = add(conn, 5)
        first_two = {j["id"] for j in claim(conn, worker="w", limit=2)}
        next_two = {j["id"] for j in claim(conn, worker="w", limit=2)}
    assert first_two == set(ids[:2])
    assert next_two == set(ids[2:4])


def test_a_job_scheduled_for_later_is_not_claimed_now(queue_db: str) -> None:
    with psycopg.connect(queue_db, autocommit=True) as conn:
        enqueue(conn, job_type="t", payload={}, dedupe_key="later", delay_s=3600)
        assert claim(conn, worker="w", limit=1) == []
        assert queue_depth(conn) == {"pending": 1}


def test_worker_loop_drains_then_stops_on_idle(queue_db: str) -> None:
    with psycopg.connect(queue_db, autocommit=True) as conn:
        add(conn, 3)
    worker = Worker(queue_db, {"noop": lambda _c, _p: None}, name="w1", sleep=lambda _s: None)
    worker.run(max_idle_polls=1)
    with psycopg.connect(queue_db, autocommit=True) as conn:
        assert queue_depth(conn) == {"done": 3}
    assert worker.processed == 3
