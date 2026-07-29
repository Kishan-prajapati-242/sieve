"""OpenAlex client and the Phase 1 synchronous ingestion script.

OpenAlex is the primary source: widest coverage, cleanest metadata, no key.
The email in the User-Agent opts into their "polite pool", which gets
faster, more reliable service than anonymous traffic.

Cursor pagination (cursor=*, then meta.next_cursor) rather than page=N:
offset pagination is capped at 10K results and can skip or duplicate
records when the result set shifts mid-crawl; the cursor is a stable
position. The `select` parameter trims each work to the fields we store —
a full Work is 10-20 KB (referenced_works, counts_by_year, every location),
which at 500K papers is ~7 GB of JSONB nothing reads; the trimmed record is
~2 KB, matching the brief's storage budget, and is still the raw response
the audit trail preserves: what we fetched is exactly what we store.

QUERIES is data, not code. Each entry is an independent cursor crawl, and
the (source, source_id) upsert makes their overlap converge — a work
matching three queries lands once. Papers derive one-to-one from source
records for now (single source); the real dedup cascade arrives in Phase 3
with the second source. The one case handled early: two OpenAlex works
sharing a normalized DOI link to one paper and write a merges row, because
that IS cascade step 1 and the audit trail must not have a blind spot.

Refetches refresh source_records.raw but do not update derived papers rows;
re-derivation belongs to the Phase 3 dedup pass, where changes are handled
uniformly for all sources.

Usage: python -m api.ingest.openalex --limit 100   (reads DATABASE_URL)
"""

import argparse
import os
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb

from api.dedup.normalize import normalize_doi, normalize_title
from api.ingest.http import get_json
from api.ingest.ratelimit import TokenBucket

BASE_URL = "https://api.openalex.org"
USER_AGENT = "sieve/0.1 (mailto:prajapati.kish@northeastern.edu)"

# Documented ceiling is 10 req/s and 100K/day; 5/s leaves polite headroom
# and still fetches 50K works (250 pages of 200) in under a minute of
# request budget.
OPENALEX_RATE = 5.0

SELECT_FIELDS = ",".join(
    [
        "id",
        "doi",
        "display_name",
        "publication_year",
        "primary_location",
        "cited_by_count",
        "abstract_inverted_index",
        "authorships",
        "ids",
    ]
)

# The corpus domain (Kishan, 2026-07-28): NLP, clinical/biomedical NLP, text
# simplification, mental health NLP. has_abstract:true throughout — the
# abstract feeds both fts and the Phase 2 embeddings; a bare title ranks
# poorly in every mode. Sorted most-cited-first (see iter_works) so a capped
# crawl keeps the highest-value works.
#
# The third element is each query's WEIGHT of the total --limit budget
# (Kishan, 2026-07-29: broad concept 40%, the rest split evenly). A single
# global cap would let the 2.2M-work concept query consume everything and
# starve the four specialty crawls — the exact bug this fixes. Budgets are
# fixed, not rolled over: if a specialty query runs out of works below its
# budget, the run reports the shortfall rather than silently backfilling
# with more broad-concept works.
QUERIES: list[tuple[str, str, float]] = [
    # Broad disciplinary base: everything OpenAlex files under the NLP
    # concept (C204321447, "Natural language processing", ~2.2M works).
    ("nlp-concept", "concepts.id:C204321447,has_abstract:true", 0.40),
    # Phrase queries catch clinical/biomedical work indexed under medicine
    # rather than the CS concept tree.
    (
        "clinical-nlp",
        'title_and_abstract.search:"clinical natural language processing"'
        ' OR "clinical text mining" OR "clinical NLP",has_abstract:true',
        0.15,
    ),
    (
        "biomedical-nlp",
        'title_and_abstract.search:"biomedical natural language processing"'
        ' OR "biomedical text mining",has_abstract:true',
        0.15,
    ),
    (
        "text-simplification",
        'title_and_abstract.search:"text simplification" OR "lexical simplification"'
        ' OR "readability assessment",has_abstract:true',
        0.15,
    ),
    (
        "mental-health-nlp",
        'title_and_abstract.search:"mental health" AND "natural language processing"'
        ",has_abstract:true",
        0.15,
    ),
]


def split_budget(limit: int, weights: Sequence[float]) -> list[int]:
    """Split `limit` by weight into integer budgets that sum to exactly limit.

    Largest-remainder method: floor everything, then hand the leftover units
    to the largest fractional parts. Weights need not sum to 1.
    """
    if any(w <= 0 for w in weights):
        raise ValueError("query weights must be positive")
    total = sum(weights)
    raw = [limit * w / total for w in weights]
    budgets = [int(r) for r in raw]
    by_remainder = sorted(range(len(raw)), key=lambda i: raw[i] - budgets[i], reverse=True)
    for i in by_remainder[: limit - sum(budgets)]:
        budgets[i] += 1
    return budgets


def year_slices(now_year: int, span: int = 10) -> list[tuple[str, str]]:
    """DECISION-1b: even temporal slices, citations only compete within a year.

    The last `span` years each get a slice, plus one "classics" slice for
    everything older. Global citation sort was rejected because citations
    accrue with age — it starves the corpus of the recent work the real
    queries target; pure recency was rejected because today's publication
    volume would make the corpus a one-to-two-year slice with no quality
    floor. See docs/decisions.md.
    """
    first_full_year = now_year - span + 1
    slices = [(str(y), f"publication_year:{y}") for y in range(first_full_year, now_year + 1)]
    slices.append((f"pre-{first_full_year}", f"publication_year:<{first_full_year}"))
    return slices


@dataclass
class IngestStats:
    fetched: int = 0
    new_papers: int = 0
    linked_by_doi: int = 0
    refreshed: int = 0
    skipped_no_title: int = 0
    per_query: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"fetched={self.fetched} new_papers={self.new_papers}"
            f" linked_by_doi={self.linked_by_doi} refreshed={self.refreshed}"
            f" skipped_no_title={self.skipped_no_title}"
        )


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """The only place an OpenAlex connection is configured: explicit timeout,
    polite-pool User-Agent. transport is injectable for tests."""
    return httpx.Client(
        base_url=BASE_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(15.0, connect=5.0),
        transport=transport,
    )


def deinvert_abstract(inverted: dict[str, list[int]] | None) -> str | None:
    """OpenAlex ships abstracts as {word: [positions]} (a legal workaround);
    flatten back to text by position."""
    if not inverted:
        return None
    positions = [(pos, word) for word, poss in inverted.items() for pos in poss]
    positions.sort()
    return " ".join(word for _, word in positions)


def iter_works(
    client: httpx.Client, bucket: TokenBucket, filter_: str, *, per_page: int = 200
) -> Iterator[dict[str, Any]]:
    """Every work matching the filter, most-cited first, one page at a time."""
    cursor: str | None = "*"
    while cursor:
        page = get_json(
            client,
            "/works",
            params={
                "filter": filter_,
                "per-page": per_page,
                "cursor": cursor,
                "select": SELECT_FIELDS,
                "sort": "cited_by_count:desc",
            },
            bucket=bucket,
        )
        yield from page["results"]
        cursor = page["meta"].get("next_cursor")


def store_work(conn: psycopg.Connection, work: dict[str, Any]) -> str:
    """Upsert one work; returns the IngestStats field to bump.

    Rerun-safe by construction: the source_records upsert converges, an
    already-linked record refreshes raw and stops, and only unlinked records
    ever derive a paper.
    """
    source_id = work["id"].removeprefix("https://openalex.org/")
    row = conn.execute(
        """
        INSERT INTO source_records (source, source_id, raw)
        VALUES ('openalex', %s, %s)
        ON CONFLICT (source, source_id)
        DO UPDATE SET raw = EXCLUDED.raw, fetched_at = now()
        RETURNING id, paper_id
        """,
        (source_id, Jsonb(work)),
    ).fetchone()
    assert row is not None  # RETURNING on upsert always yields the row
    record_id, paper_id = row
    if paper_id is not None:
        return "refreshed"

    title = work.get("display_name")
    if not title:
        # Unsearchable and unmergeable; raw stays for the audit trail. The
        # record remains unlinked, so a future fetch with a title picks it up.
        return "skipped_no_title"

    doi = normalize_doi(work.get("doi"))
    ids = work.get("ids") or {}
    pubmed_id = (ids.get("pmid") or "").removeprefix("https://pubmed.ncbi.nlm.nih.gov/") or None
    venue = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")

    inserted = conn.execute(
        """
        INSERT INTO papers
            (doi, title, title_norm, abstract, year, venue, citation_count, pubmed_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (doi) DO NOTHING
        RETURNING id
        """,
        (
            doi,
            title,
            normalize_title(title),
            deinvert_abstract(work.get("abstract_inverted_index")),
            work.get("publication_year"),
            venue,
            work.get("cited_by_count") or 0,
            pubmed_id,
        ),
    ).fetchone()

    if inserted is not None:
        paper_id, outcome = inserted[0], "new_papers"
    else:
        # Two OpenAlex works, one normalized DOI: link this record to the
        # existing paper and log it — dedup cascade step 1, audited from day
        # one. (NULL DOIs never conflict, so this branch implies doi is set.)
        existing = conn.execute("SELECT id FROM papers WHERE doi = %s", (doi,)).fetchone()
        assert existing is not None
        paper_id = existing[0]
        conn.execute(
            """
            INSERT INTO merges (kept_paper_id, merged_from, strategy)
            VALUES (%s, %s, 'doi_exact')
            """,
            (paper_id, Jsonb({"source_record_ids": [record_id], "title": title})),
        )
        outcome = "linked_by_doi"

    conn.execute("UPDATE source_records SET paper_id = %s WHERE id = %s", (paper_id, record_id))
    return outcome


def ingest(
    conn: psycopg.Connection,
    client: httpx.Client,
    bucket: TokenBucket,
    *,
    limit: int | None = None,
    queries: Sequence[tuple[str, str, float]] = tuple(QUERIES),
    slices: Sequence[tuple[str, str]] | None = None,
) -> IngestStats:
    """Crawl every query within its own budget share of --limit, each query
    stratified evenly across year slices (DECISION-1b).

    Budgets are per query, not a global cap, so the broad concept crawl can
    never starve the specialty ones; within a query the same largest-
    remainder split spreads its budget across the year slices. One
    transaction per work: a crash mid-run loses at most one work and a rerun
    converges, never duplicates.
    """
    if slices is None:
        slices = year_slices(datetime.now(UTC).year)
    budgets: list[int | None]
    if limit is None:
        budgets = [None] * len(queries)
    else:
        budgets = list(split_budget(limit, [w for _, _, w in queries]))
    stats = IngestStats()
    for (name, filter_, _), budget in zip(queries, budgets, strict=True):
        stats.per_query[name] = 0
        if budget == 0:
            continue
        slice_budgets: list[int | None]
        if budget is None:
            slice_budgets = [None] * len(slices)
        else:
            slice_budgets = list(split_budget(budget, [1.0] * len(slices)))
        print(f"query {name}: starting (budget {'unlimited' if budget is None else budget})")
        for (slice_name, fragment), slice_budget in zip(slices, slice_budgets, strict=True):
            if slice_budget == 0:
                continue
            sliced_filter = f"{filter_},{fragment}" if fragment else filter_
            taken = 0
            per_page = 200 if slice_budget is None else min(200, slice_budget)
            for work in iter_works(client, bucket, sliced_filter, per_page=per_page):
                if slice_budget is not None and taken >= slice_budget:
                    break
                with conn.transaction():
                    outcome = store_work(conn, work)
                taken += 1
                stats.fetched += 1
                stats.per_query[name] += 1
                setattr(stats, outcome, getattr(stats, outcome) + 1)
                if stats.fetched % 1000 == 0:
                    print(f"  {stats.summary()}")
            if slice_budget is not None and taken < slice_budget:
                print(f"  {name}/{slice_name}: exhausted at {taken} of {slice_budget}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest OpenAlex works into sieve")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="total works budget, split across queries by weight;"
        " smoke-test with --limit 100 before a full pull",
    )
    args = parser.parse_args()
    conninfo = os.environ.get("DATABASE_URL")
    if not conninfo:
        sys.exit("DATABASE_URL is not set")
    bucket = TokenBucket(rate=OPENALEX_RATE, capacity=OPENALEX_RATE)
    with make_client() as client, psycopg.connect(conninfo) as conn:
        stats = ingest(conn, client, bucket, limit=args.limit)
    print(stats.summary())
    for name, count in stats.per_query.items():
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main()
