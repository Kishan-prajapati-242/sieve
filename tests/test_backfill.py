"""Resumable-backfill semantics against a real migrated database (Kishan's
requirement 2b, as a fast repeatable test): a run that dies mid-way must
resume with no duplicates, no gaps, and no rewrites of committed rows.
The encoder is a deterministic stub — these tests are about the claim/
write/resume loop, not ONNX; the live 5K kill-proof exercises the real
model."""

import hashlib

import numpy as np
import psycopg
import pytest

from api.db.migrate import migrate
from api.embed.backfill import backfill, vector_literal


class StubEncoder:
    """Deterministic per-text vectors; records what it was asked to encode."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def encode(self, texts: list[str]) -> np.ndarray:
        self.seen.extend(texts)
        out = np.zeros((len(texts), 384), dtype=np.float32)
        for i, t in enumerate(texts):
            digest = hashlib.sha256(t.encode()).digest()
            out[i, : len(digest)] = np.frombuffer(digest, dtype=np.uint8) / 255.0
        return out


class DiesAfter(StubEncoder):
    """Simulates the crash: the Nth encode call never returns, so the Nth
    batch's transaction never opens — exactly what SIGKILL leaves behind."""

    def __init__(self, calls: int) -> None:
        super().__init__()
        self.calls_left = calls

    def encode(self, texts: list[str]) -> np.ndarray:
        if self.calls_left == 0:
            raise RuntimeError("simulated kill")
        self.calls_left -= 1
        return super().encode(texts)


def seed(conn: psycopg.Connection, n: int) -> None:
    for i in range(n):
        conn.execute(
            "INSERT INTO papers (title, title_norm, abstract) VALUES (%s, %s, %s)",
            (f"Paper {i}", f"paper {i}", f"Abstract number {i}."),
        )


@pytest.fixture
def db(scratch_db: str) -> str:
    migrate(scratch_db)
    return scratch_db


def test_backfill_embeds_everything_exactly_once(db: str) -> None:
    with psycopg.connect(db) as conn:
        seed(conn, 40)
    with psycopg.connect(db, autocommit=True) as conn:
        encoder = StubEncoder()
        written = backfill(conn, encoder, batch_rows=16)
        null_count = conn.execute("SELECT count(*) FROM papers WHERE embedding IS NULL").fetchone()
    assert written == 40
    assert null_count == (0,)
    assert len(encoder.seen) == 40  # nothing encoded twice


def test_backfill_refuses_a_default_mode_connection(db: str) -> None:
    """The guard for the savepoint trap (findings.md 2026-07-30): on a
    non-autocommit connection, per-batch 'commits' are savepoints inside
    one giant transaction that dies with the process. Refuse loudly."""
    with psycopg.connect(db) as conn:
        seed(conn, 5)
        with pytest.raises(ValueError, match="autocommit"):
            backfill(conn, StubEncoder(), batch_rows=16)


def test_limit_takes_the_lowest_ids_and_stops(db: str) -> None:
    with psycopg.connect(db) as conn:
        seed(conn, 30)
    with psycopg.connect(db, autocommit=True) as conn:
        assert backfill(conn, StubEncoder(), limit=10, batch_rows=16) == 10
        embedded = conn.execute(
            "SELECT id FROM papers WHERE embedding IS NOT NULL ORDER BY id"
        ).fetchall()
        lowest = conn.execute("SELECT id FROM papers ORDER BY id LIMIT 10").fetchall()
    assert embedded == lowest


def test_killed_run_resumes_with_no_dupes_gaps_or_rewrites(db: str) -> None:
    """The night-saving property: kill after 2 of 5 batches, restart, and
    the union is complete, gapless, and the survivors' bytes are untouched.

    Every verification runs on a DIFFERENT connection than the writer, and
    the dying writer's connection is closed without commit — what process
    death leaves behind. The first version of this test read through the
    writer's own connection and passed while the code lost every row on a
    real SIGKILL (findings.md 2026-07-30): a connection always sees its own
    uncommitted work, so same-connection reads cannot verify durability."""
    with psycopg.connect(db) as conn:
        seed(conn, 80)

    dying = psycopg.connect(db, autocommit=True)
    with pytest.raises(RuntimeError, match="simulated kill"):
        backfill(dying, DiesAfter(2), batch_rows=16)
    dying.close()  # no commit, no goodbye — as SIGKILL would leave it

    with psycopg.connect(db) as conn:
        survivors = conn.execute(
            "SELECT id, embedding::text FROM papers WHERE embedding IS NOT NULL ORDER BY id"
        ).fetchall()
    assert len(survivors) == 32  # exactly the two batches that committed

    resume_encoder = StubEncoder()
    with psycopg.connect(db, autocommit=True) as conn:
        written = backfill(conn, resume_encoder, batch_rows=16)

    with psycopg.connect(db) as conn:
        after: dict[int, str] = dict(
            conn.execute(
                "SELECT id, embedding::text FROM papers WHERE embedding IS NOT NULL"
            ).fetchall()
        )
        gaps = conn.execute("SELECT count(*) FROM papers WHERE embedding IS NULL").fetchone()

    assert written == 48  # only the missing rows — no double work
    assert len(resume_encoder.seen) == 48
    assert gaps == (0,)
    assert len(after) == 80  # no duplicates possible, count proves no gaps
    for paper_id, text_before in survivors:
        assert after[paper_id] == text_before  # byte-identical, never rewritten


def test_vector_literal_roundtrips_deterministically(db: str) -> None:
    """halfvec quantizes fp32 to fp16 — writing the same vector twice must
    yield the same stored bytes, or 'byte-identical after resume' would be
    unprovable. %.8g exceeds fp16 precision, so the cast is stable."""
    vec = np.array([0.123456789, -1.5e-5, 0.0, 3.14159] + [0.5] * 380, dtype=np.float32)
    with psycopg.connect(db) as conn:
        seed(conn, 1)
        stored_texts = []
        for _ in range(2):
            conn.execute(
                "UPDATE papers SET embedding = %s::halfvec",
                (vector_literal(vec),),
            )
            row = conn.execute("SELECT embedding::text FROM papers").fetchone()
            assert row is not None
            stored_texts.append(row[0])
    assert stored_texts[0] == stored_texts[1]
