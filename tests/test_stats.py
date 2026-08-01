"""GET /api/stats against a real seeded database, through the real app —
same no-mocks fixture pattern as test_search.py.

The seed builds the exact ambiguities the endpoint must resolve honestly:
a paper fetched by two queries (attributed to its FIRST record), a legacy
record with NULL provenance ("unattributed", never guessed), and an
unlinked audit-trail record.
"""

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from api.db.migrate import migrate
from api.db.pool import close_pool
from api.main import app


@pytest.fixture
def client(scratch_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    migrate(scratch_db)
    with psycopg.connect(scratch_db) as conn:
        ids = [
            conn.execute(
                """
                INSERT INTO papers (title, title_norm, is_retracted)
                VALUES (%s, lower(%s), %s) RETURNING id
                """,
                (t, t, retracted),
            ).fetchone()[0]  # type: ignore[index]
            for t, retracted in [("Paper A", False), ("Paper B", True), ("Paper C", False)]
        ]
        records = [
            # Paper A: fetched first by general-nlp, later also by a
            # specialty query — composition counts it once, under the first.
            ("W1", ids[0], "general-nlp"),
            ("W2", ids[0], "text-simplification"),
            ("W3", ids[1], "text-simplification"),
            ("W4", ids[2], None),  # legacy row, predates provenance
            ("W5", None, "general-nlp"),  # junk-type audit row, no paper
        ]
        for source_id, paper_id, query_name in records:
            conn.execute(
                """
                INSERT INTO source_records (source, source_id, raw, paper_id, query_name)
                VALUES ('openalex', %s, '{}', %s, %s)
                """,
                (source_id, paper_id, query_name),
            )
        # A second-source record, so embedding coverage can distinguish
        # "awaiting dedup" from "stalled".
        conn.execute(
            """
            INSERT INTO source_records (source, source_id, raw, paper_id, query_name)
            VALUES ('arxiv', 'A1', '{}', %s, 'arxiv-cl')
            """,
            (ids[2],),
        )
    close_pool()
    monkeypatch.setenv("DATABASE_URL", scratch_db)
    with TestClient(app) as c:
        yield c
    close_pool()


def test_stats_reports_embedding_coverage(client: TestClient) -> None:
    """NULL embeddings are routine now and fail silently (findings.md
    2026-07-31): the seed leaves all 3 papers un-embedded, and one of them
    is arXiv-sourced, i.e. deliberately awaiting dedup rather than stalled."""
    cov = client.get("/api/stats").json()["embedding_coverage"]
    assert cov["total"] == 3
    assert cov["embedded"] == 0
    assert cov["missing"] == 3
    assert cov["missing_awaiting_dedup"] == 1  # the arxiv-linked paper
    assert cov["missing"] == cov["embedded"] + cov["missing"] - cov["embedded"]


def test_stats_reports_composition_with_first_fetch_attribution(client: TestClient) -> None:
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["papers"] == 3
    assert data["retracted_papers"] == 1
    assert data["source_records"] == 6
    assert data["unlinked_records"] == 1
    # Paper A counts under general-nlp (its first record), not under the
    # overlapping specialty refetch; the legacy NULL reports as-is.
    assert data["papers_by_query"] == {
        "general-nlp": 1,
        "text-simplification": 1,
        "unattributed": 1,
    }
    assert data["records_by_query"] == {
        "general-nlp": 2,
        "text-simplification": 2,
        "arxiv-cl": 1,
        "unattributed": 1,
    }
