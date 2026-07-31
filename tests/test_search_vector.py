"""mode=vector against a real migrated database (HNSW index included via
migration 0006) — same no-mocks-for-the-DB pattern as test_search.py.

The ONNX encoder is the one stubbed piece: CI carries no 127MB model, so
tests install a recording stub UNDER api.embed.runtime.embed_query and pin
the whole chain — if the route (or runtime) ever stops applying the bge
instruction prefix, the recorded text loses it and the test fails, which
is the point (silent retrieval degradation has no other tripwire).

Geometry: 60 papers cluster near e1 = [1, 0, ...] (year 2020), 3 papers
near -e1 (year 1999). A query at e1 with ef_search=40 fills its candidate
list entirely from the cluster, so a year<=1999 filter strains out every
candidate — the case iterative_scan exists for.
"""

import math
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

import api.embed.runtime as runtime
from api.db.migrate import migrate
from api.db.pool import close_pool
from api.embed.texts import QUERY_PREFIX
from api.main import app
from api.search.vector import VECTOR_SQL, search_vector, vector_literal

DIMS = 384
E1 = [1.0] + [0.0] * (DIMS - 1)


def unit_near(axis: float, i: int) -> list[float]:
    """A distinct unit vector near axis*e1. The wobble spreads across 30
    dims with real magnitude (0.2..0.5): near-identical vectors give HNSW
    degenerate connectivity and the geometry stops meaning anything."""
    v = [axis] + [0.0] * (DIMS - 1)
    v[1 + (i % 30)] = 0.2 + 0.005 * i
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


class RecordingStub:
    """Stands in for the ONNX encoder under runtime.embed_query: returns e1
    for any text and records exactly what it was asked to encode."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return [E1 for _ in texts]


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> RecordingStub:
    s = RecordingStub()
    monkeypatch.setattr(runtime, "_encoder", s)
    return s


def seed_geometry(conn: psycopg.Connection) -> None:
    for i in range(60):
        conn.execute(
            "INSERT INTO papers (title, title_norm, year, embedding) VALUES (%s, %s, %s, %s)",
            (
                f"Cluster paper {i}",
                f"cluster paper {i}",
                2020,
                vector_literal(unit_near(1, i)),
            ),
        )
    for i in range(3):
        conn.execute(
            "INSERT INTO papers (title, title_norm, year, embedding) VALUES (%s, %s, %s, %s)",
            (
                f"Far paper {i}",
                f"far paper {i}",
                1999,
                vector_literal(unit_near(-1, i)),
            ),
        )


@pytest.fixture
def client(scratch_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    migrate(scratch_db)
    with psycopg.connect(scratch_db) as conn:
        seed_geometry(conn)
    close_pool()
    monkeypatch.setenv("DATABASE_URL", scratch_db)
    with TestClient(app) as c:
        yield c
    close_pool()


def test_vector_mode_prefixes_ranks_and_reports(client: TestClient, stub: RecordingStub) -> None:
    resp = client.post(
        "/api/search", json={"query": "clinical text simplification", "mode": "vector", "k": 5}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # The prefix contract, end to end: route -> runtime -> encoder. This is
    # the test that fails if anything in the chain bypasses query_text().
    assert stub.seen == [f"{QUERY_PREFIX}clinical text simplification"]

    titles = [r["title"] for r in data["results"]]
    assert len(titles) == 5 and all(t.startswith("Cluster paper") for t in titles)
    scores = [r["score"] for r in data["results"]]
    assert scores == sorted(scores, reverse=True)
    assert all(0.85 < s <= 1.0 for s in scores)  # cosine similarity, near the query

    assert data["ef_search"] == 40  # the default, recorded per query
    assert data["timings"]["embed_ms"] is not None and data["timings"]["embed_ms"] >= 0
    assert data["timings"]["retrieve_ms"] >= 0


def test_ef_search_is_tunable_and_echoed(client: TestClient, stub: RecordingStub) -> None:
    data = client.post(
        "/api/search", json={"query": "x", "mode": "vector", "k": 3, "ef_search": 100}
    ).json()
    assert data["ef_search"] == 100
    assert (
        client.post(
            "/api/search", json={"query": "x", "mode": "vector", "ef_search": 0}
        ).status_code
        == 422
    )  # below pgvector's floor


def test_bm25_reports_no_ef_search_and_no_embed(client: TestClient) -> None:
    data = client.post("/api/search", json={"query": "cluster paper", "mode": "bm25"}).json()
    assert data["ef_search"] is None
    assert data["timings"]["embed_ms"] is None


def test_narrow_year_filter_underfills_naively_and_iterative_scan_rescues_it(
    scratch_db: str,
) -> None:
    """The pin Kishan asked for — with a twist the first version of this
    test discovered: given a SELECTIVE year filter, the planner correctly
    prefers papers_year_idx (bitmap scan by year, then sort by distance) —
    exact results, HNSW never involved, no underfill possible. The failure
    mode this test pins only exists on the HNSW path, so the scratch DB
    drops the year index and disables seq/bitmap scans to force it; the
    live 197K EXPLAIN covers natural planner choice."""
    migrate(scratch_db)
    with psycopg.connect(scratch_db) as conn:
        seed_geometry(conn)

    with psycopg.connect(scratch_db, autocommit=True) as conn:
        conn.execute("DROP INDEX papers_year_idx")
        conn.execute("SET enable_seqscan = off")
        conn.execute("SET enable_bitmapscan = off")

        # Naive HNSW post-filtering: the candidate list (ef_search=10) fills
        # with 2020-cluster papers — all nearer the query than any 1999
        # paper — and the year filter strains out every one of them.
        conn.execute("SET hnsw.iterative_scan = 'off'")
        conn.execute("SET hnsw.ef_search = 10")
        naive = conn.execute(
            VECTOR_SQL, {"q": vector_literal(E1), "k": 10, "year_from": None, "year_to": 1999}
        ).fetchall()
        assert len(naive) < 3  # under-returns: the failure mode being pinned

        # The production path (search_vector sets iterative_scan) finds all 3
        # at the same ef_search.
        rows = search_vector(conn, query_vec=E1, k=10, year_to=1999, ef_search=10)
        assert sorted(r["title"] for r in rows) == ["Far paper 0", "Far paper 1", "Far paper 2"]
        assert all(r["year"] == 1999 for r in rows)

        # And it genuinely used the index: same session, forced index path.
        plan = "\n".join(
            r[0]
            for r in conn.execute(
                f"EXPLAIN {VECTOR_SQL}",  # noqa: S608
                {"q": vector_literal(E1), "k": 10, "year_from": None, "year_to": 1999},
            )
        )
        assert "papers_embed_idx" in plan
