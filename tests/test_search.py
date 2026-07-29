"""POST /api/search against a real seeded database, through the real app.

The fixture repoints DATABASE_URL at the per-test scratch database and
resets the pool global, so the endpoint exercises the exact pool -> SQL
path production uses — no mocks, per the working agreement.
"""

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from api.db.migrate import migrate
from api.db.pool import close_pool
from api.main import app

PAPERS = [
    # (title, abstract, year, citations, is_retracted) — one query separates them.
    ("Clinical text simplification with transformers", "We simplify EHR notes.", 2023, 40, False),
    (
        "A survey of machine translation",
        "Includes a section on text simplification.",
        2019,
        90,
        False,
    ),
    ("Protein folding dynamics", "Molecular dynamics of folding pathways.", 2023, 10, False),
    ("Text simplification for aphasia", None, 2021, 5, True),  # retracted, stays visible
    # Two textually identical rows: identical fts vectors, identical
    # ts_rank_cd scores — the tie the ORDER BY must break deterministically.
    ("Tied score probe alpha", "Identical twin rows for the tie test.", 2020, 1, False),
    ("Tied score probe alpha", "Identical twin rows for the tie test.", 2020, 1, False),
]


@pytest.fixture
def client(scratch_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    migrate(scratch_db)
    with psycopg.connect(scratch_db) as conn:
        for title, abstract, year, citations, is_retracted in PAPERS:
            conn.execute(
                """
                INSERT INTO papers (title, title_norm, abstract, year, citation_count,
                                    is_retracted)
                VALUES (%s, lower(%s), %s, %s, %s, %s)
                """,
                (title, title, abstract, year, citations, is_retracted),
            )
    close_pool()  # the next get_pool() must see the scratch DATABASE_URL
    monkeypatch.setenv("DATABASE_URL", scratch_db)
    with TestClient(app) as c:
        yield c
    close_pool()


def search(client: TestClient, **body: object) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/search", json=body)
    assert resp.status_code == 200, resp.text
    data: dict = resp.json()  # type: ignore[type-arg]
    return data


def test_title_match_outranks_abstract_match(client: TestClient) -> None:
    data = search(client, query="text simplification")
    titles = [r["title"] for r in data["results"]]
    # Both title-matched papers (setweight A) must beat the abstract-only
    # match (setweight B); the protein paper must not appear at all.
    assert titles[-1] == "A survey of machine translation"
    assert set(titles[:-1]) == {
        "Clinical text simplification with transformers",
        "Text simplification for aphasia",
    }
    assert "Protein folding dynamics" not in titles


def test_ranks_are_sequential_and_scores_descend(client: TestClient) -> None:
    data = search(client, query="text simplification")
    ranks = [r["rank"] for r in data["results"]]
    scores = [r["score"] for r in data["results"]]
    assert ranks == list(range(1, len(ranks) + 1))
    assert scores == sorted(scores, reverse=True)
    assert all(s > 0 for s in scores)
    assert data["mode"] == "bm25"
    assert data["took_ms"] >= 0


def test_year_filter_bounds_results(client: TestClient) -> None:
    data = search(client, query="text simplification", year_from=2022)
    assert [r["title"] for r in data["results"]] == [
        "Clinical text simplification with transformers"
    ]
    data = search(client, query="text simplification", year_from=2020, year_to=2021)
    assert [r["title"] for r in data["results"]] == ["Text simplification for aphasia"]


def test_k_caps_the_result_count(client: TestClient) -> None:
    data = search(client, query="text simplification", k=1)
    assert len(data["results"]) == 1
    assert data["results"][0]["rank"] == 1


def test_no_match_is_empty_not_error(client: TestClient) -> None:
    assert search(client, query="quantum chromodynamics")["results"] == []


def test_stopword_only_query_is_empty_not_500(client: TestClient) -> None:
    # websearch_to_tsquery reduces this to an empty tsquery; must not raise.
    assert search(client, query="the of and")["results"] == []


@pytest.mark.parametrize(
    "body",
    [
        {},  # query is required
        {"query": ""},  # too short
        {"query": "x", "k": 0},  # k below bounds
        {"query": "x", "k": 101},  # k above bounds
        {"query": "x", "mode": "vector"},  # Phase 2, not yet
    ],
)
def test_invalid_requests_are_422(client: TestClient, body: dict) -> None:  # type: ignore[type-arg]
    assert client.post("/api/search", json=body).status_code == 422


def test_retracted_papers_stay_visible_and_flagged(client: TestClient) -> None:
    """DECISION-1c: a screening tool shows retractions so reviewers exclude
    them deliberately; silently dropping them is worse."""
    data = search(client, query="text simplification")
    by_title = {r["title"]: r for r in data["results"]}
    assert by_title["Text simplification for aphasia"]["is_retracted"] is True
    assert by_title["Clinical text simplification with transformers"]["is_retracted"] is False


def test_score_ties_break_deterministically_by_id(client: TestClient) -> None:
    """Identical documents rank identically; without the id tiebreak their
    order is whatever the executor felt like, and Phase 4 keyset pagination
    would skip or repeat rows at page boundaries."""
    runs = [search(client, query="tied score probe") for _ in range(3)]
    first = runs[0]["results"]
    assert len(first) == 2
    assert first[0]["score"] == first[1]["score"]
    assert first[0]["id"] < first[1]["id"]  # ascending id within equal scores
    for run in runs[1:]:
        assert [r["id"] for r in run["results"]] == [r["id"] for r in first]


def test_request_id_is_echoed_or_minted(client: TestClient) -> None:
    resp = client.post("/api/search", json={"query": "x"}, headers={"x-request-id": "trace-42"})
    assert resp.headers["x-request-id"] == "trace-42"
    resp = client.post("/api/search", json={"query": "x"})
    assert resp.headers["x-request-id"]  # minted when absent
