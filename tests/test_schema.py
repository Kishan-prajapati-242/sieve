"""Schema behavior tests for the core tables (0002).

These assert behavior, not catalog contents: the idempotent-upsert pattern
ingestion will lean on, the generated fts column, the halfvec typmod, and the
enqueue-once dedupe key. A schema change that silently breaks one of those
assumptions should fail here, not three modules downstream.
"""

import psycopg
import pytest

from api.db.migrate import migrate


@pytest.fixture
def migrated_db(scratch_db: str) -> str:
    migrate(scratch_db)
    return scratch_db


def test_source_records_upsert_is_idempotent(migrated_db: str) -> None:
    # The exact shape the ingestion client will use: refetching a record
    # converges to one row holding the latest payload, never a duplicate.
    upsert = """
        INSERT INTO source_records (source, source_id, raw)
        VALUES (%s, %s, %s)
        ON CONFLICT (source, source_id) DO UPDATE SET raw = EXCLUDED.raw
    """
    with psycopg.connect(migrated_db) as conn:
        conn.execute(upsert, ("openalex", "W123", '{"title": "first fetch"}'))
        conn.execute(upsert, ("openalex", "W123", '{"title": "refetched"}'))
        assert conn.execute("SELECT count(*) FROM source_records").fetchone() == (1,)
        assert conn.execute("SELECT raw->>'title' FROM source_records").fetchone() == ("refetched",)


def test_papers_fts_generates_from_title_and_abstract(migrated_db: str) -> None:
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO papers (title, title_norm, abstract) VALUES (%s, %s, %s)",
            (
                "Clinical text simplification",
                "clinical text simplification",
                "We study readability of biomedical prose.",
            ),
        )
        for term in ("simplification", "readability"):
            hit = conn.execute(
                "SELECT count(*) FROM papers WHERE fts @@ plainto_tsquery('english', %s)",
                (term,),
            ).fetchone()
            assert hit == (1,), f"fts should match {term!r}"


def test_papers_embedding_enforces_384_dims(migrated_db: str) -> None:
    ok = "[" + ",".join(["0"] * 384) + "]"
    wrong = "[" + ",".join(["0"] * 3) + "]"
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO papers (title, title_norm, embedding) VALUES ('a', 'a', %s)", (ok,)
        )
        with pytest.raises(psycopg.errors.DataException):
            conn.execute(
                "INSERT INTO papers (title, title_norm, embedding) VALUES ('b', 'b', %s)", (wrong,)
            )


def test_ingest_jobs_dedupe_key_makes_enqueue_idempotent(migrated_db: str) -> None:
    enqueue = """
        INSERT INTO ingest_jobs (job_type, payload, dedupe_key)
        VALUES ('fetch_page', '{}', %s)
        ON CONFLICT (dedupe_key) DO NOTHING
    """
    with psycopg.connect(migrated_db) as conn:
        conn.execute(enqueue, ("openalex:page:1",))
        conn.execute(enqueue, ("openalex:page:1",))
        assert conn.execute("SELECT count(*) FROM ingest_jobs").fetchone() == (1,)


def test_ingest_jobs_rejects_unknown_status(migrated_db: str) -> None:
    with psycopg.connect(migrated_db) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO ingest_jobs (job_type, payload, status) VALUES ('fetch_page', '{}', %s)",
            ("runnign",),
        )
