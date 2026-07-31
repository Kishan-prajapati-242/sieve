"""arXiv client: Atom parsing, rate limiting, and — the Phase 1 acceptance
criterion carried forward — idempotency against the real database.

HTTP is a mock transport serving canned Atom feeds; the database side runs
on a real migrated scratch database, no mocks, per the working agreement.
"""

from typing import Any
from xml.etree import ElementTree

import httpx
import psycopg
import pytest

from api.db.migrate import migrate
from api.ingest.arxiv import (
    ARXIV_RATE,
    ArxivStats,
    ingest,
    iter_entries,
    make_client,
    parse_entry,
    store_entry,
)
from api.ingest.ratelimit import TokenBucket

FEED_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
"""


def entry_xml(
    n: int,
    *,
    title: str | None = None,
    summary: str = "We study things.",
    doi: str | None = None,
    version: int = 1,
) -> str:
    doi_el = f"<arxiv:doi>{doi}</arxiv:doi>" if doi else ""
    return f"""<entry>
    <id>http://arxiv.org/abs/2301.{n:05d}v{version}</id>
    <title>{title if title is not None else f"Preprint {n}"}</title>
    <summary>{summary}</summary>
    <published>2023-01-{(n % 28) + 1:02d}T00:00:00Z</published>
    <updated>2023-02-01T00:00:00Z</updated>
    {doi_el}
    <arxiv:primary_category term="cs.CL"/>
    <category term="cs.CL"/>
    <category term="cs.AI"/>
    <author><name>Ada Lovelace</name></author>
    <author><name>Grace Hopper</name></author>
</entry>"""


def feed(*entries: str) -> str:
    return FEED_HEAD + "\n".join(entries) + "\n</feed>"


def paging_transport(pages: list[str], seen: list[dict[str, Any]] | None = None):  # type: ignore[no-untyped-def]
    """Serves pages by `start` offset, recording the params it was asked for."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(dict(request.url.params))
        start = int(request.url.params.get("start", 0))
        per_page = int(request.url.params.get("max_results", 100))
        index = start // per_page
        body = pages[index] if index < len(pages) else feed()
        return httpx.Response(200, text=body)

    return httpx.MockTransport(handler)


def free_bucket() -> TokenBucket:
    return TokenBucket(rate=1e9, capacity=1e9)


def test_parse_entry_extracts_the_fields_we_store() -> None:
    root = ElementTree.fromstring(feed(entry_xml(1234, doi="10.1/x", version=3)))
    parsed = parse_entry(root.find("{http://www.w3.org/2005/Atom}entry"))  # type: ignore[arg-type]
    assert parsed["id"] == "2301.01234v3"
    # Version stripped: v1 and v3 are the same paper, so the idempotency key
    # must not fork per revision.
    assert parsed["arxiv_id"] == "2301.01234"
    assert parsed["title"] == "Preprint 1234"
    assert parsed["summary"] == "We study things."
    assert parsed["doi"] == "10.1/x"
    assert parsed["primary_category"] == "cs.CL"
    assert parsed["categories"] == ["cs.CL", "cs.AI"]
    assert parsed["authors"] == ["Ada Lovelace", "Grace Hopper"]


def test_rate_is_arxivs_documented_courtesy_limit() -> None:
    assert pytest.approx(1 / 3) == ARXIV_RATE


def test_iter_entries_pages_until_a_short_page() -> None:
    pages = [
        feed(entry_xml(1), entry_xml(2)),
        feed(entry_xml(3)),  # short page ends the crawl
    ]
    seen: list[dict[str, Any]] = []
    with make_client(transport=paging_transport(pages, seen)) as client:
        got = [e["arxiv_id"] for e in iter_entries(client, free_bucket(), "cat:cs.CL", per_page=2)]
    assert got == ["2301.00001", "2301.00002", "2301.00003"]
    assert [p["start"] for p in seen] == ["0", "2"]
    assert seen[0]["search_query"] == "cat:cs.CL"


def test_ingest_twice_is_idempotent(scratch_db: str) -> None:
    """The Phase 1 criterion, held for source two: rerunning changes no counts."""
    migrate(scratch_db)
    pages = [
        feed(
            entry_xml(1, doi="10.1/a"),
            entry_xml(2),  # no DOI
            entry_xml(3, title=""),  # untitled: audit row only
        )
    ]
    queries = [("test", "cat:cs.CL", 1.0)]
    with (
        make_client(transport=paging_transport(pages)) as client,
        psycopg.connect(scratch_db, autocommit=True) as conn,
    ):
        first = ingest(conn, client, free_bucket(), queries=queries)
        second = ingest(conn, client, free_bucket(), queries=queries)
        counts = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
            for table in ("source_records", "papers")
        }
        linked = conn.execute(
            "SELECT count(*) FROM source_records WHERE paper_id IS NOT NULL"
        ).fetchone()
        sources = conn.execute("SELECT DISTINCT source FROM source_records").fetchall()

    assert first == ArxivStats(fetched=3, new_papers=2, skipped_no_title=1, per_query={"test": 3})
    assert second == ArxivStats(fetched=3, refreshed=2, skipped_no_title=1, per_query={"test": 3})
    assert counts["source_records"] == (3,)
    assert counts["papers"] == (2,)
    assert linked == (2,)
    assert sources == [("arxiv",)]


def test_arxiv_fields_land_on_the_paper(scratch_db: str) -> None:
    migrate(scratch_db)
    pages = [feed(entry_xml(7, doi="10.5/z"))]
    with (
        make_client(transport=paging_transport(pages)) as client,
        psycopg.connect(scratch_db, autocommit=True) as conn,
    ):
        ingest(conn, client, free_bucket(), queries=[("q", "cat:cs.CL", 1.0)])
        row = conn.execute(
            "SELECT title, abstract, year, venue, citation_count, arxiv_id, authors, doi"
            " FROM papers"
        ).fetchone()

    assert row == (
        "Preprint 7",
        "We study things.",
        2023,
        None,  # arXiv has no venue: absent, not empty-as-measured
        0,  # and no citation counts
        "2301.00007",
        ["Ada Lovelace", "Grace Hopper"],
        "10.5/z",
    )


def test_limit_stops_before_fetching_an_unread_page(scratch_db: str) -> None:
    """The overfetch lesson from OpenAlex, which here costs 3 seconds of
    wall clock per wasted page rather than credits."""
    migrate(scratch_db)
    pages = [feed(entry_xml(1)), feed(entry_xml(2))]
    seen: list[dict[str, Any]] = []
    with (
        make_client(transport=paging_transport(pages, seen)) as client,
        psycopg.connect(scratch_db, autocommit=True) as conn,
    ):
        stats = ingest(conn, client, free_bucket(), limit=1, queries=[("q", "cat:cs.CL", 1.0)])
    assert stats.fetched == 1
    assert len(seen) == 1  # exactly one request, no page fetched to be discarded


def test_ingest_refuses_a_default_mode_connection(scratch_db: str) -> None:
    migrate(scratch_db)
    with (
        psycopg.connect(scratch_db) as conn,
        make_client(transport=paging_transport([feed(entry_xml(1))])) as client,
        pytest.raises(ValueError, match="autocommit"),
    ):
        ingest(conn, client, free_bucket(), queries=[("q", "cat:cs.CL", 1.0)])


def test_store_entry_nulls_embedding_when_text_moves(scratch_db: str) -> None:
    """DECISION-3a holds for arXiv too, because it shares api/ingest/store."""
    migrate(scratch_db)
    entry = parse_entry(
        ElementTree.fromstring(feed(entry_xml(9))).find("{http://www.w3.org/2005/Atom}entry")  # type: ignore[arg-type]
    )
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        store_entry(conn, entry, "q")
        conn.execute(
            "UPDATE papers SET embedding = %s::halfvec", ("[" + ",".join(["0.1"] * 384) + "]",)
        )
        entry["summary"] = "A completely rewritten abstract."
        outcome = store_entry(conn, entry, "q")
        nulled = conn.execute("SELECT embedding IS NULL FROM papers").fetchone()

    assert outcome == "refreshed_text_changed"
    assert nulled == (True,)
