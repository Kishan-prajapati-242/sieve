"""The queue's ingestion handlers: self-perpetuating pages, and what
happens when a worker dies in the middle of a crawl.

HTTP is the same mock transport the PubMed client tests use; the database
and the queue are real.
"""

from typing import Any

import psycopg
import pytest

from api.db.migrate import migrate
from api.queue.claim import claim, enqueue, queue_depth
from api.queue.handlers import enqueue_pubmed_crawl, handle_pubmed_page, pubmed_page_key
from api.queue.worker import Worker
from tests.test_pubmed import article_xml, eutils_transport


@pytest.fixture
def queue_db(scratch_db: str) -> str:
    migrate(scratch_db)
    return scratch_db


def patched_client(
    monkeypatch: pytest.MonkeyPatch, pages: list[list[int]], seen: list[Any]
) -> None:
    """Point the handler's client factory at a canned E-utilities server."""
    from api.ingest import pubmed

    transport = eutils_transport(pages, {p: article_xml(p) for row in pages for p in row}, seen)
    monkeypatch.setattr(
        "api.queue.handlers.pubmed_client", lambda: pubmed.make_client(transport=transport)
    )


QUERIES = [("test-q", "nlp[tiab]", 1.0)]


def test_seeding_a_crawl_enqueues_one_job_per_query(queue_db: str) -> None:
    with psycopg.connect(queue_db, autocommit=True) as conn:
        ids = enqueue_pubmed_crawl(conn, QUERIES)
        assert len(ids) == 1
        # Re-seeding after a crash adds nothing: the dedupe key is the page.
        assert enqueue_pubmed_crawl(conn, QUERIES) == []
        assert queue_depth(conn) == {"pending": 1}


def test_a_page_stores_its_records_and_enqueues_the_next(
    queue_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []
    patched_client(monkeypatch, [[1, 2], [3, 4], [5]], seen)
    with psycopg.connect(queue_db, autocommit=True) as conn:
        with conn.transaction():
            handle_pubmed_page(
                conn, {"query_name": "test-q", "term": "nlp", "retstart": 0, "per_page": 2}
            )
        assert conn.execute("SELECT count(*) FROM papers").fetchone() == (2,)
        successor = conn.execute(
            "SELECT dedupe_key, payload FROM ingest_jobs WHERE status = 'pending'"
        ).fetchone()
    assert successor is not None
    assert successor[0] == pubmed_page_key("test-q", 2)
    assert successor[1]["retstart"] == 2


def test_a_short_page_ends_the_chain(queue_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The crawl stops by not enqueuing a successor — no result count needed
    up front, which none of these APIs promises."""
    patched_client(monkeypatch, [[1]], [])
    with psycopg.connect(queue_db, autocommit=True) as conn:
        with conn.transaction():
            handle_pubmed_page(
                conn, {"query_name": "test-q", "term": "nlp", "retstart": 0, "per_page": 2}
            )
        assert queue_depth(conn) == {}


def test_a_crawl_runs_to_completion_through_the_worker(
    queue_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    patched_client(monkeypatch, [[1, 2], [3, 4], [5]], [])
    with psycopg.connect(queue_db, autocommit=True) as conn:
        enqueue_pubmed_crawl(conn, QUERIES, per_page=2)

    worker = Worker(queue_db, {}, name="w1", sleep=lambda _s: None)
    from api.queue.handlers import HANDLERS

    worker.handlers = dict(HANDLERS)
    worker.run(max_idle_polls=1)

    with psycopg.connect(queue_db, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM papers").fetchone() == (5,)
        assert queue_depth(conn) == {"done": 3}
    assert worker.processed == 3


def test_a_worker_killed_mid_crawl_loses_no_pages(
    queue_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Phase 3 acceptance criterion. The second page's transaction is
    aborted mid-flight; its records and its successor roll back together,
    and a fresh worker redoes exactly that page."""
    patched_client(monkeypatch, [[1, 2], [3, 4], [5]], [])
    with psycopg.connect(queue_db, autocommit=True) as conn:
        enqueue_pubmed_crawl(conn, QUERIES, per_page=2)

    from api.queue.handlers import HANDLERS

    calls = {"n": 0}

    def flaky(conn: psycopg.Connection, payload: dict[str, Any]) -> None:
        HANDLERS["pubmed_page"](conn, payload)
        calls["n"] += 1
        if calls["n"] == 2:  # die AFTER writing page two, before committing
            raise RuntimeError("worker killed")

    dying = Worker(queue_db, {"pubmed_page": flaky}, name="doomed", sleep=lambda _s: None)
    dying.run(max_jobs=2)

    with psycopg.connect(queue_db, autocommit=True) as conn:
        # Page one committed; page two rolled back entirely.
        assert conn.execute("SELECT count(*) FROM papers").fetchone() == (2,)
        conn.execute("UPDATE ingest_jobs SET run_after = now()")  # skip backoff
        depth = queue_depth(conn)
    assert depth == {"done": 1, "pending": 1}

    restarted = Worker(queue_db, dict(HANDLERS), name="w2", sleep=lambda _s: None)
    restarted.run(max_idle_polls=1)

    with psycopg.connect(queue_db, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM papers").fetchone() == (5,)
        assert conn.execute("SELECT count(*) FROM source_records").fetchone() == (5,)
        assert queue_depth(conn) == {"done": 3}


def test_rerunning_a_completed_crawl_creates_no_duplicates(
    queue_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "No duplicates" holds at both layers: the queue refuses to re-enqueue
    a page, and the store refuses to re-create a record."""
    patched_client(monkeypatch, [[1, 2], [3]], [])
    from api.queue.handlers import HANDLERS

    with psycopg.connect(queue_db, autocommit=True) as conn:
        enqueue_pubmed_crawl(conn, QUERIES, per_page=2)
    Worker(queue_db, dict(HANDLERS), name="w1", sleep=lambda _s: None).run(max_idle_polls=1)

    with psycopg.connect(queue_db, autocommit=True) as conn:
        before = conn.execute("SELECT count(*) FROM papers").fetchone()
        assert enqueue_pubmed_crawl(conn, QUERIES, per_page=2) == []
        # Force the page to run again anyway: the store must still converge.
        enqueue(
            conn,
            job_type="pubmed_page",
            payload={"query_name": "test-q", "term": "nlp", "retstart": 0, "per_page": 2},
        )
    Worker(queue_db, dict(HANDLERS), name="w2", sleep=lambda _s: None).run(max_idle_polls=1)

    with psycopg.connect(queue_db, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM papers").fetchone() == before


def test_two_workers_do_not_run_the_same_page(
    queue_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    patched_client(monkeypatch, [[1, 2], [3, 4], [5]], [])
    with psycopg.connect(queue_db, autocommit=True) as conn:
        enqueue_pubmed_crawl(conn, QUERIES, per_page=2)
        first = claim(conn, worker="a", limit=1)
        second = claim(conn, worker="b", limit=1)
    assert len(first) == 1
    assert second == [], "only one page is due until the first one enqueues its successor"
