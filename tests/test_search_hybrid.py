"""mode=hybrid (RRF) against a real migrated database, stubbed encoder —
same pattern as test_search_vector.py.

Geometry gives each ranker something the other can't see:
  both-docs:    lexical match AND vector near the query
  lexical-only: lexical match, vector antipodal (missed at any sane depth)
  vector-only:  no lexical overlap, vector near the query
so the fused result must union them, carry per-ranker ranks, and score
both-docs above single-ranker docs (two reciprocal terms beat one).
"""

import math
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

import api.embed.runtime as runtime
from api.db.migrate import migrate
from api.db.pool import close_pool
from api.main import app
from api.search.fusion import RRF_K, search_hybrid

DIMS = 384
E1 = [1.0] + [0.0] * (DIMS - 1)


def vec_near(axis: float, i: int) -> str:
    v = [axis] + [0.0] * (DIMS - 1)
    v[1 + (i % 30)] = 0.2 + 0.005 * i
    n = math.sqrt(sum(x * x for x in v))
    return "[" + ",".join(f"{x / n:.8g}" for x in v) + "]"


class StubEncoder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        return [E1 for _ in texts]


@pytest.fixture
def client(scratch_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    migrate(scratch_db)
    # Wobble magnitude sets distance from the query (monotone), so the
    # vector top-10 at depth=10 is exactly: both(3) + vec-only(3) + the 4
    # nearest fillers — the antipodal lex docs and remaining fillers fall
    # outside. The corpus must EXCEED depth or the vector CTE returns
    # everything and "lexical-only" stops existing (first version's bug).
    rows = (
        [(f"Quantum entanglement study {i}", f"both {i}", 2020, vec_near(1, i)) for i in range(3)]
        + [
            (f"Completely different words {i}", f"vec {i}", 2015, vec_near(1, i + 5))
            for i in range(3)
        ]
        + [
            (f"Irrelevant filler item {i}", f"fill {i}", 2015, vec_near(1, i + 20))
            for i in range(20)
        ]
        + [
            (f"Quantum entanglement archive {i}", f"lex {i}", 2010, vec_near(-1, i))
            for i in range(3)
        ]
    )
    with psycopg.connect(scratch_db) as conn:
        for title, norm, year, emb in rows:
            conn.execute(
                "INSERT INTO papers (title, title_norm, year, embedding) VALUES (%s, %s, %s, %s)",
                (title, norm, year, emb),
            )
    close_pool()
    monkeypatch.setenv("DATABASE_URL", scratch_db)
    monkeypatch.setattr(runtime, "_encoder", StubEncoder())
    with TestClient(app) as c:
        yield c
    close_pool()


def hybrid(client: TestClient, **body: object) -> dict:  # type: ignore[type-arg]
    resp = client.post(
        "/api/search", json={"query": "quantum entanglement", "mode": "hybrid", **body}
    )
    assert resp.status_code == 200, resp.text
    data: dict = resp.json()  # type: ignore[type-arg]
    return data


def test_hybrid_unions_rankers_with_breakdown(client: TestClient) -> None:
    data = hybrid(client, k=9, depth=10)
    by_title = {r["title"]: r for r in data["results"]}

    both = by_title["Quantum entanglement study 0"]
    assert both["bm25_rank"] is not None and both["vector_rank"] is not None
    assert both["sources"] == ["bm25", "vector"]

    lex = by_title["Quantum entanglement archive 0"]
    assert lex["bm25_rank"] is not None and lex["vector_rank"] is None
    assert lex["sources"] == ["bm25"]

    vec = by_title["Completely different words 0"]
    assert vec["bm25_rank"] is None and vec["vector_rank"] is not None
    assert vec["sources"] == ["vector"]

    # RRF arithmetic is self-consistent with the reported ranks.
    for r in data["results"]:
        expected = sum(1.0 / (RRF_K + rank) for rank in (r["bm25_rank"], r["vector_rank"]) if rank)
        assert r["score"] == pytest.approx(expected, abs=1e-9)

    # Two reciprocal terms beat one: every both-doc outranks every
    # single-ranker doc in this geometry (ranks are small and comparable).
    scores = [r["score"] for r in data["results"]]
    assert scores == sorted(scores, reverse=True)
    both_scores = [r["score"] for r in data["results"] if len(r["sources"]) == 2]
    single_scores = [r["score"] for r in data["results"] if len(r["sources"]) == 1]
    assert min(both_scores) > max(single_scores)


def test_ef_search_defaults_and_auto_raise(client: TestClient) -> None:
    """DECISION-2e: hybrid defaults to ef=600; an explicit ef is honored;
    and NOTHING escapes the >= depth floor, because ef < depth silently
    truncates the vector candidate list. The response reports what ran."""
    data = hybrid(client, k=5, depth=300)  # no explicit ef -> hybrid default
    assert data["ef_search"] == 600
    data = hybrid(client, k=5, depth=700)  # default below depth -> raised
    assert data["ef_search"] == 700
    data = hybrid(client, k=5, depth=50, ef_search=500)  # explicit wins
    assert data["ef_search"] == 500
    data = hybrid(client, k=5, depth=100, ef_search=20)  # explicit but truncating -> floor
    assert data["ef_search"] == 100


def test_search_hybrid_refuses_truncating_ef(scratch_db: str) -> None:
    migrate(scratch_db)
    with (
        psycopg.connect(scratch_db, autocommit=True) as conn,
        pytest.raises(ValueError, match="silently truncates"),
    ):
        search_hybrid(conn, query="x", query_vec=E1, k=5, depth=100, ef_search=40)


def test_hybrid_timings_and_year_filter(client: TestClient) -> None:
    data = hybrid(client, k=9, depth=10, year_from=2014)
    years = [r["year"] for r in data["results"]]
    assert years and all(y >= 2014 for y in years)
    # The 2010 lexical-only docs are filtered in BOTH CTEs, not post-fusion.
    assert "Quantum entanglement archive 0" not in [r["title"] for r in data["results"]]
    assert data["timings"]["embed_ms"] is not None
    assert data["mode"] == "hybrid"


def test_other_modes_report_no_breakdown(client: TestClient) -> None:
    data = client.post("/api/search", json={"query": "quantum", "mode": "bm25"}).json()
    assert all(
        r["bm25_rank"] is None and r["vector_rank"] is None and r["sources"] is None
        for r in data["results"]
    )
