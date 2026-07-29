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
from api.ingest.http import RequestMeter
from api.ingest.openalex import (
    USER_AGENT,
    IngestStats,
    deinvert_abstract,
    extract_authors,
    extract_venue,
    ingest,
    iter_works,
    make_client,
    split_budget,
    year_slices,
)
from api.ingest.ratelimit import TokenBucket

# Most tests opt out of year stratification to test one dimension at a time;
# test_year_stratification_spreads_the_budget covers the slicing itself.
UNSLICED = [("all", "")]


def free_bucket() -> TokenBucket:
    return TokenBucket(rate=1e9, capacity=1e9)


def make_work(
    n: int,
    *,
    doi: str | None = None,
    title: str | None | EllipsisType = ...,  # ... means "default title"; None means "absent"
    year: int = 2024,
    type_: str = "article",
    is_retracted: bool = False,
    authors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"https://openalex.org/W{n}",
        "doi": doi,
        "display_name": f"Paper {n}" if title is ... else title,
        "publication_year": year,
        "type": type_,
        "is_paratext": type_ == "paratext",
        "is_retracted": is_retracted,
        "primary_location": {"source": {"display_name": f"Venue {n}"}},
        "cited_by_count": n * 10,
        "abstract_inverted_index": {"About": [0], f"paper{n}.": [1]},
        "authorships": [{"author": {"display_name": name}} for name in authors or []],
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


def test_extract_venue_prefers_canonical_source_then_falls_back_to_raw() -> None:
    canonical = {"primary_location": {"source": {"display_name": "JAMIA"}}}
    assert extract_venue(canonical) == "JAMIA"

    # The measured ACL Anthology shape (92% of ACL papers, 2026-07-29):
    # source null everywhere, venue only in raw_source_name free text.
    acl_shaped = {
        "primary_location": {"source": None, "raw_source_name": "Proceedings of ACL 2017"},
        "locations": [{"source": None, "raw_source_name": "Proceedings of ACL 2017"}],
    }
    assert extract_venue(acl_shaped) == "Proceedings of ACL 2017"

    # A canonical source in locations[] beats raw free text anywhere.
    mixed = {
        "primary_location": {"source": None, "raw_source_name": "raw name"},
        "locations": [{"source": {"display_name": "Canonical Venue"}}],
    }
    assert extract_venue(mixed) == "Canonical Venue"

    assert extract_venue({"primary_location": None, "locations": []}) is None
    assert extract_venue({}) is None


def test_extract_authors_in_publication_order_or_none() -> None:
    work = {
        "authorships": [
            {"author": {"display_name": "First Author"}},
            {"author": None},  # dangling authorship: skipped, not a crash
            {"author": {"display_name": "Last Author"}},
        ]
    }
    assert extract_authors(work) == ["First Author", "Last Author"]
    # None, not []: NULL in the column means "unknown", not "zero authors".
    assert extract_authors({"authorships": []}) is None
    assert extract_authors({}) is None


def test_authors_are_stored_with_the_paper(scratch_db: str) -> None:
    migrate(scratch_db)
    works = [make_work(1, authors=["Ada Lovelace", "Grace Hopper"]), make_work(2)]
    transport = paged_transport({"unused-filter": [works]})
    queries = [("test", "unused-filter", 1.0)]

    with make_client(transport=transport) as client, psycopg.connect(scratch_db) as conn:
        ingest(conn, client, free_bucket(), queries=queries, slices=UNSLICED)
        rows = conn.execute("SELECT title, authors FROM papers ORDER BY id").fetchall()

    assert rows == [
        ("Paper 1", ["Ada Lovelace", "Grace Hopper"]),
        ("Paper 2", None),
    ]


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
        stats = ingest(conn, client, free_bucket(), limit=3, queries=queries, slices=UNSLICED)
        stored = conn.execute("SELECT source_id FROM source_records ORDER BY source_id").fetchall()

    # 2:1 weights over limit 3 -> broad stops at 2 despite having 4 works
    # available, and niche actually runs (the old code never reached it).
    assert stats.per_query == {"broad": 2, "niche": 1}
    assert stats.fetched == 3
    assert stored == [("W1",), ("W2",), ("W5",)]


def test_api_key_rides_on_every_request() -> None:
    """Keyless requests run on OpenAlex's $0.01/day anonymous budget; the
    key must be a client-level default param, not remembered per call."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("api_key", "MISSING"))
        return httpx.Response(200, json={"meta": {"next_cursor": None}, "results": [make_work(1)]})

    with make_client(api_key="test-key", transport=httpx.MockTransport(handler)) as client:
        list(iter_works(client, free_bucket(), "f"))
    assert seen == ["test-key"]


def test_ingest_stops_cleanly_when_quota_is_exhausted(scratch_db: str) -> None:
    """Mid-crawl quota death must return partial stats, not raise: the data
    already stored is committed and a rerun after the reset converges."""
    migrate(scratch_db)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["filter"].endswith("y1"):
            return httpx.Response(
                200, json={"meta": {"next_cursor": None}, "results": [make_work(1), make_work(2)]}
            )
        return httpx.Response(
            429, headers={"Retry-After": "17000"}, text='{"message":"Insufficient budget"}'
        )

    with (
        make_client(transport=httpx.MockTransport(handler)) as client,
        psycopg.connect(scratch_db) as conn,
    ):
        stats = ingest(
            conn,
            client,
            free_bucket(),
            limit=4,
            queries=[("q", "f", 1.0)],
            slices=[("y1", "y1"), ("y2", "y2")],
        )
        count = conn.execute("SELECT count(*) FROM source_records").fetchone()

    assert stats.per_query == {"q": 2}
    assert stats.fetched == 2
    assert count == (2,)


def test_exhausted_budget_never_fetches_the_next_page(scratch_db: str) -> None:
    """Found live: with per_page == slice_budget, the budget check ran when
    the generator resumed — after it had already fetched (and been billed
    for) the next page. One slice, budget 1: exactly one request."""
    migrate(scratch_db)
    requests_made: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_made.append(str(request.url.params.get("cursor")))
        return httpx.Response(
            200, json={"meta": {"next_cursor": "more"}, "results": [make_work(len(requests_made))]}
        )

    with (
        make_client(transport=httpx.MockTransport(handler)) as client,
        psycopg.connect(scratch_db) as conn,
    ):
        stats = ingest(
            conn, client, free_bucket(), limit=1, queries=[("q", "f", 1.0)], slices=UNSLICED
        )
    assert stats.fetched == 1
    assert requests_made == ["*"]


def test_ingest_attributes_credits_per_query(scratch_db: str) -> None:
    """Credits, not requests: one page of each query here costs 1 vs 10
    credits, which raw request counts would report as identical."""
    migrate(scratch_db)
    list_filter = "concepts.id:C1"
    search_filter = 'title_and_abstract.search:"x"'
    transport = paged_transport(
        {
            list_filter: [[make_work(1), make_work(2)]],
            search_filter: [[make_work(3), make_work(4)]],
        }
    )
    meter = RequestMeter()
    queries = [("cheap", list_filter, 1.0), ("pricey", search_filter, 1.0)]

    with (
        make_client(transport=transport, meter=meter) as client,
        psycopg.connect(scratch_db) as conn,
    ):
        stats = ingest(conn, client, free_bucket(), queries=queries, slices=UNSLICED, meter=meter)

    assert stats.per_query == {"cheap": 2, "pricey": 2}
    assert stats.per_query_credits == {"cheap": 1, "pricey": 10}
    assert meter.credits_spent == 11


def test_junk_types_are_audited_but_never_become_papers(scratch_db: str) -> None:
    """DECISION-1c: the six junk types keep their raw audit row but derive
    no paper; retracted real papers DO become papers, carrying the flag."""
    migrate(scratch_db)
    works = [
        make_work(1, type_="paratext"),  # the proceedings-volume ghost
        make_work(2, type_="editorial"),
        make_work(3, type_="article", is_retracted=True),  # stays, flagged
        make_work(4, type_="article"),
    ]
    transport = paged_transport({"unused": [works]})

    with make_client(transport=transport) as client, psycopg.connect(scratch_db) as conn:
        stats = ingest(conn, client, free_bucket(), queries=[("t", "unused", 1.0)], slices=UNSLICED)
        papers = conn.execute("SELECT title, is_retracted FROM papers ORDER BY id").fetchall()
        audit = conn.execute("SELECT count(*) FROM source_records").fetchall()
        unlinked = conn.execute(
            "SELECT count(*) FROM source_records WHERE paper_id IS NULL"
        ).fetchone()

    assert stats.skipped_by_type == {"paratext": 1, "editorial": 1}
    assert stats.new_papers == 2
    assert papers == [("Paper 3", True), ("Paper 4", False)]
    assert audit == [(4,)]  # every fetch is audited, skipped or not
    assert unlinked == (2,)  # the junk rows stay unlinked forever


def test_year_slices_cover_span_plus_classics() -> None:
    slices = year_slices(2026)
    assert len(slices) == 11
    assert slices[0] == ("2017", "publication_year:2017")
    assert slices[-2] == ("2026", "publication_year:2026")
    assert slices[-1] == ("pre-2017", "publication_year:<2017")


def test_year_stratification_spreads_the_budget(scratch_db: str) -> None:
    """DECISION-1b: each year slice gets its share instead of the crawl
    spending the whole budget on the (citation-heavy) top of one list."""
    migrate(scratch_db)
    transport = paged_transport(
        {
            "f,publication_year:2025": [[make_work(1), make_work(2), make_work(3)]],
            "f,publication_year:2026": [[make_work(4), make_work(5), make_work(6)]],
        }
    )
    slices = [("2025", "publication_year:2025"), ("2026", "publication_year:2026")]

    with (
        make_client(transport=transport) as client,
        psycopg.connect(scratch_db) as conn,
    ):
        stats = ingest(
            conn,
            client,
            free_bucket(),
            limit=4,
            queries=[("q", "f", 1.0)],
            slices=slices,
        )
        stored = conn.execute("SELECT source_id FROM source_records ORDER BY source_id").fetchall()

    # Budget 4 over two slices -> 2 from each year, not 3+1 off one list.
    assert stats.per_query == {"q": 4}
    assert stored == [("W1",), ("W2",), ("W4",), ("W5",)]


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
        first = ingest(conn, client, free_bucket(), queries=queries, slices=UNSLICED)
        second = ingest(conn, client, free_bucket(), queries=queries, slices=UNSLICED)

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
        stats = ingest(
            conn, client, free_bucket(), limit=1, queries=[("test", "u", 1.0)], slices=UNSLICED
        )
        count = conn.execute("SELECT count(*) FROM source_records").fetchone()
    assert stats.fetched == 1
    assert count == (1,)
