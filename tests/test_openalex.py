"""OpenAlex client: pagination, mapping, and — the Phase 1 acceptance
criterion — ingestion idempotency against the real database.

HTTP is a mock transport serving canned OpenAlex-shaped pages (these tests
are about our crawl and mapping logic, not their API); the database side
runs on a real migrated scratch database, no mocks, per the working
agreement.
"""

from types import EllipsisType
from typing import Any

import httpx
import psycopg
import pytest

from api.db.migrate import migrate
from api.ingest.openalex import (
    USER_AGENT,
    IngestStats,
    deinvert_abstract,
    ingest,
    iter_works,
    make_client,
    split_budget,
)
from api.ingest.ratelimit import TokenBucket


def free_bucket() -> TokenBucket:
    return TokenBucket(rate=1e9, capacity=1e9)


def make_work(
    n: int,
    *,
    doi: str | None = None,
    title: str | None | EllipsisType = ...,  # ... means "default title"; None means "absent"
    year: int = 2024,
) -> dict[str, Any]:
    return {
        "id": f"https://openalex.org/W{n}",
        "doi": doi,
        "display_name": f"Paper {n}" if title is ... else title,
        "publication_year": year,
        "primary_location": {"source": {"display_name": f"Venue {n}"}},
        "cited_by_count": n * 10,
        "abstract_inverted_index": {"About": [0], f"paper{n}.": [1]},
        "authorships": [],
        "ids": {"openalex": f"https://openalex.org/W{n}"},
    }


def paged_transport(pages_by_filter: dict[str, list[list[dict[str, Any]]]]) -> httpx.MockTransport:
    """Serves per-filter page lists, keyed by cursor: '*' -> pages[0], 'c1' -> pages[1], ..."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "mailto:prajapati.kish@northeastern.edu" in request.headers["user-agent"]
        pages = pages_by_filter[request.url.params["filter"]]
        cursor = request.url.params["cursor"]
        idx = 0 if cursor == "*" else int(cursor.removeprefix("c"))
        next_cursor = f"c{idx + 1}" if idx + 1 < len(pages) else None
        return httpx.Response(
            200, json={"meta": {"next_cursor": next_cursor}, "results": pages[idx]}
        )

    return httpx.MockTransport(handler)


def test_deinvert_abstract_orders_by_position() -> None:
    inverted = {"learning": [1], "Deep": [0], "again": [4], "wins": [2, 3]}
    assert deinvert_abstract(inverted) == "Deep learning wins wins again"
    assert deinvert_abstract(None) is None
    assert deinvert_abstract({}) is None


def test_cursor_pagination_walks_every_page_with_polite_user_agent() -> None:
    pages = [[make_work(1), make_work(2)], [make_work(3)], [make_work(4)]]
    with make_client(transport=paged_transport({"unused": pages})) as client:
        got = [w["id"] for w in iter_works(client, free_bucket(), "unused")]
    assert got == [f"https://openalex.org/W{n}" for n in (1, 2, 3, 4)]
    assert USER_AGENT.endswith("(mailto:prajapati.kish@northeastern.edu)")


def test_split_budget_matches_the_agreed_weights() -> None:
    # The production weights at the smoke-test size: 40% concept, 15% each.
    assert split_budget(100, [0.40, 0.15, 0.15, 0.15, 0.15]) == [40, 15, 15, 15, 15]


def test_split_budget_sums_exactly_despite_rounding() -> None:
    for limit in (1, 3, 7, 101, 199_999):
        budgets = split_budget(limit, [0.40, 0.15, 0.15, 0.15, 0.15])
        assert sum(budgets) == limit
    # Weights need not sum to 1; ratios are what count.
    assert split_budget(30, [2.0, 1.0]) == [20, 10]


def test_split_budget_rejects_nonpositive_weights() -> None:
    with pytest.raises(ValueError):
        split_budget(10, [0.5, 0.0])


def test_every_query_gets_its_own_budget(scratch_db: str) -> None:
    """The --limit bug: a global cap let the first (broad) query consume the
    whole budget and the specialty queries never ran at all."""
    migrate(scratch_db)
    transport = paged_transport(
        {
            "broad": [[make_work(1), make_work(2)], [make_work(3), make_work(4)]],
            "niche": [[make_work(5), make_work(6)]],
        }
    )
    queries = [("broad", "broad", 2.0), ("niche", "niche", 1.0)]

    with (
        make_client(transport=transport) as client,
        psycopg.connect(scratch_db) as conn,
    ):
        stats = ingest(conn, client, free_bucket(), limit=3, queries=queries)
        stored = conn.execute("SELECT source_id FROM source_records ORDER BY source_id").fetchall()

    # 2:1 weights over limit 3 -> broad stops at 2 despite having 4 works
    # available, and niche actually runs (the old code never reached it).
    assert stats.per_query == {"broad": 2, "niche": 1}
    assert stats.fetched == 3
    assert stored == [("W1",), ("W2",), ("W5",)]


def test_ingest_twice_is_idempotent(scratch_db: str) -> None:
    """Phase 1 acceptance: rerunning the same fetch changes no row counts."""
    migrate(scratch_db)
    works = [
        make_work(1, doi="https://doi.org/10.1000/SAME"),
        make_work(2, doi="doi:10.1000/same"),  # same paper after normalization
        make_work(3, title=None),  # no title: audit row only, no paper
        make_work(4),  # no DOI at all: NULLs must not "conflict"
    ]
    transport = paged_transport({"unused-filter": [works]})
    queries = [("test", "unused-filter", 1.0)]

    with make_client(transport=transport) as client, psycopg.connect(scratch_db) as conn:
        first = ingest(conn, client, free_bucket(), queries=queries)
        second = ingest(conn, client, free_bucket(), queries=queries)

        counts = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
            for table in ("source_records", "papers", "merges")
        }
        linked = conn.execute(
            "SELECT count(*) FROM source_records WHERE paper_id IS NOT NULL"
        ).fetchone()
        abstract = conn.execute("SELECT abstract FROM papers WHERE title = 'Paper 1'").fetchone()

    assert first == IngestStats(
        fetched=4, new_papers=2, linked_by_doi=1, skipped_no_title=1, per_query={"test": 4}
    )
    # Rerun: every linked record just refreshes; the title-less one is
    # re-attempted and re-skipped; nothing new is created anywhere.
    assert second == IngestStats(fetched=4, refreshed=3, skipped_no_title=1, per_query={"test": 4})
    assert counts["source_records"] == (4,)
    assert counts["papers"] == (2,)
    assert counts["merges"] == (1,)
    assert linked == (3,)
    assert abstract == ("About paper1.",)


def test_limit_stops_mid_crawl(scratch_db: str) -> None:
    migrate(scratch_db)
    pages = [[make_work(1), make_work(2)], [make_work(3)]]
    with (
        make_client(transport=paged_transport({"u": pages})) as client,
        psycopg.connect(scratch_db) as conn,
    ):
        stats = ingest(conn, client, free_bucket(), limit=1, queries=[("test", "u", 1.0)])
        count = conn.execute("SELECT count(*) FROM source_records").fetchone()
    assert stats.fetched == 1
    assert count == (1,)
