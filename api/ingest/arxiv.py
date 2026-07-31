"""arXiv client. Second source, same discipline as OpenAlex.

Differences from OpenAlex that shape this module:

  Atom XML, not JSON. Parsed with stdlib ElementTree — arXiv's schema is
  small and stable, and a feed parser dependency would earn nothing.

  1 request per 3 seconds (arXiv's documented courtesy rate), so the token
  bucket is rate=1/3 with capacity 1: a full 100-record smoke run at 100
  results per page is one request, but a 10,000-record pull is 100 requests
  = 5 minutes of waiting by design. No api_key, no credits, no budget —
  the constraint is wall clock.

  Pagination is start/max_results offsets, NOT a cursor. arXiv caps
  max_results at 2000 and the API is documented as unreliable past ~30,000
  offset; the crawl stops when a page returns fewer entries than requested.

  No citation counts (arXiv has none), so citation_count is 0 — NOT a
  measurement, and it must not be read as one. venue is null for the same
  reason: an arXiv preprint has no venue until it is published, which is
  exactly the preprint/published pair the dedup cascade will merge.

Ordering (Kishan, 2026-07-31): dedup runs BEFORE embedding for this source.
Ingest never embeds; papers merged away are therefore never embedded at
all, which is cheaper than embedding and then deleting.
"""

import argparse
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree

import httpx
import psycopg

from api.ingest.ratelimit import TokenBucket
from api.ingest.store import (
    PaperFields,
    create_or_link_paper,
    owns_paper,
    refresh_paper,
    upsert_record,
)

# https, not http: arXiv 301-redirects http and httpx does not follow
# cross-scheme redirects by default (found live, 2026-07-31).
BASE_URL = "https://export.arxiv.org"
USER_AGENT = "sieve/0.1 (mailto:prajapati.kish@northeastern.edu)"

# arXiv asks for one request every 3 seconds. capacity=1 so a burst can
# never front-load: the very first request waits its turn like the rest.
ARXIV_RATE = 1.0 / 3.0
PER_PAGE = 100

NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# The corpus domain (DECISION-2 domains, expressed in arXiv's category +
# keyword syntax). cat:cs.CL is arXiv's computational-linguistics category —
# the closest analogue to the OpenAlex NLP topic.
QUERIES: list[tuple[str, str, float]] = [
    ("arxiv-cl", "cat:cs.CL", 0.50),
    ("arxiv-clinical", 'cat:cs.CL AND (abs:"clinical" OR abs:"patient")', 0.20),
    ("arxiv-simplification", 'abs:"text simplification" OR abs:"readability"', 0.15),
    ("arxiv-mental-health", 'abs:"mental health" AND (abs:"NLP" OR abs:"language model")', 0.15),
]


@dataclass
class ArxivStats:
    fetched: int = 0
    new_papers: int = 0
    linked_by_doi: int = 0
    refreshed: int = 0
    refreshed_text_changed: int = 0
    skipped_no_title: int = 0
    per_query: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"fetched={self.fetched} new_papers={self.new_papers}"
            f" linked_by_doi={self.linked_by_doi} refreshed={self.refreshed}"
            f" refreshed_text_changed={self.refreshed_text_changed}"
            f" skipped_no_title={self.skipped_no_title}"
        )


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(30.0, connect=5.0),
        transport=transport,
    )


def parse_entry(entry: ElementTree.Element) -> dict[str, Any]:
    """One Atom entry -> a plain dict, stored verbatim as the raw record.

    Kept as a dict rather than the XML string so source_records.raw stays
    queryable JSONB like OpenAlex's, and so the dedup cascade can read
    arXiv fields without an XML parser.
    """

    def text(path: str) -> str | None:
        node = entry.find(path, NS)
        return node.text.strip() if node is not None and node.text else None

    raw_id = text("atom:id") or ""
    # http://arxiv.org/abs/2301.01234v2 -> 2301.01234v2
    versioned = raw_id.rsplit("/", 1)[-1]
    return {
        "id": versioned,
        # Version-stripped: v1 and v3 of one preprint are the SAME paper, and
        # the idempotency key must say so or every revision forks a row.
        "arxiv_id": versioned.split("v")[0] if "v" in versioned else versioned,
        "title": " ".join((text("atom:title") or "").split()),
        "summary": " ".join((text("atom:summary") or "").split()),
        "published": text("atom:published"),
        "updated": text("atom:updated"),
        "doi": text("arxiv:doi"),
        "journal_ref": text("arxiv:journal_ref"),
        "primary_category": (
            entry.find("arxiv:primary_category", NS).get("term")  # type: ignore[union-attr]
            if entry.find("arxiv:primary_category", NS) is not None
            else None
        ),
        "categories": [c.get("term") for c in entry.findall("atom:category", NS)],
        "authors": [
            a.text.strip()
            for a in entry.findall("atom:author/atom:name", NS)
            if a.text and a.text.strip()
        ],
    }


def iter_entries(
    client: httpx.Client, bucket: TokenBucket, search_query: str, *, per_page: int = PER_PAGE
) -> Iterator[dict[str, Any]]:
    """Every entry for a query, oldest-relevance first, page by page."""
    start = 0
    while True:
        bucket.acquire()
        resp = client.get(
            "/api/query",
            params={
                "search_query": search_query,
                "start": start,
                "max_results": per_page,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
        )
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.text)
        entries = root.findall("atom:entry", NS)
        if not entries:
            return
        for entry in entries:
            yield parse_entry(entry)
        if len(entries) < per_page:
            return  # short page: the result set is exhausted
        start += per_page


def paper_fields(entry: dict[str, Any]) -> PaperFields:
    published = entry.get("published") or ""
    year = int(published[:4]) if published[:4].isdigit() else None
    return PaperFields(
        title=str(entry["title"]),
        abstract=entry.get("summary") or None,
        year=year,
        # No venue and no citations from arXiv — absent, not zero-as-measured.
        venue=None,
        citation_count=0,
        doi=entry.get("doi"),
        authors=entry.get("authors") or None,
        arxiv_id=entry.get("arxiv_id"),
    )


def store_entry(conn: psycopg.Connection, entry: dict[str, Any], query_name: str | None) -> str:
    """Upsert one arXiv entry through the shared store layer, so DECISION-3a
    (null the embedding when text moves) applies here without a second copy."""
    record_id, paper_id = upsert_record(
        conn,
        source="arxiv",
        source_id=str(entry["arxiv_id"]),
        raw=entry,
        query_name=query_name,
    )
    if not entry.get("title"):
        return "skipped_no_title"

    fields = paper_fields(entry)
    if paper_id is not None:
        if not owns_paper(conn, record_id, paper_id):
            return "refreshed"
        return "refreshed_text_changed" if refresh_paper(conn, paper_id, fields) else "refreshed"

    _, outcome = create_or_link_paper(conn, record_id, fields)
    return outcome


def ingest(
    conn: psycopg.Connection,
    client: httpx.Client,
    bucket: TokenBucket,
    *,
    limit: int | None = None,
    queries: list[tuple[str, str, float]] | None = None,
) -> ArxivStats:
    """Crawl each query within its share of --limit. One transaction per
    entry: a crash loses at most one entry and a rerun converges."""
    if not conn.autocommit:
        raise ValueError(
            "ingest requires an autocommit connection; per-entry commits are the point"
        )
    queries = list(queries if queries is not None else QUERIES)
    from api.ingest.openalex import split_budget

    budgets: list[int | None]
    budgets = (
        [None] * len(queries)
        if limit is None
        else list(split_budget(limit, [w for _, _, w in queries]))
    )

    stats = ArxivStats()
    for (name, search_query, _), budget in zip(queries, budgets, strict=True):
        stats.per_query[name] = 0
        if budget == 0:
            continue
        print(f"query {name}: starting (budget {'unlimited' if budget is None else budget})")
        taken = 0
        per_page = PER_PAGE if budget is None else min(PER_PAGE, budget)
        for entry in iter_entries(client, bucket, search_query, per_page=per_page):
            with conn.transaction():
                outcome = store_entry(conn, entry, name)
            taken += 1
            stats.fetched += 1
            stats.per_query[name] += 1
            setattr(stats, outcome, getattr(stats, outcome) + 1)
            if budget is not None and taken >= budget:
                # Break BEFORE the generator resumes, or it fetches (and
                # waits 3s for) a page nobody reads — the same overfetch
                # bug the OpenAlex credit meter caught (findings.md).
                break
        if budget is not None and taken < budget:
            print(f"  {name}: exhausted at {taken} of {budget}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest arXiv preprints.")
    parser.add_argument("--limit", type=int, default=None, help="total entries across queries")
    args = parser.parse_args()

    conninfo = os.environ.get("DATABASE_URL")
    if not conninfo:
        print("DATABASE_URL is required", file=sys.stderr)
        raise SystemExit(2)

    start = time.perf_counter()
    with make_client() as client, psycopg.connect(conninfo, autocommit=True) as conn:
        stats = ingest(conn, client, TokenBucket(rate=ARXIV_RATE, capacity=1.0), limit=args.limit)
    print(stats.summary())
    for name, count in stats.per_query.items():
        print(f"  {name}: {count} entries")
    print(f"elapsed: {time.perf_counter() - start:.0f}s (rate-limited to 1 request / 3s)")


if __name__ == "__main__":
    main()
