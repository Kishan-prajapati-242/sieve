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
from dataclasses import dataclass
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
QUERIES: list[tuple[str, str]] = [
    # Broad disciplinary base: everything OpenAlex files under the NLP
    # concept (C204321447, "Natural language processing", ~2.2M works).
    ("nlp-concept", "concepts.id:C204321447,has_abstract:true"),
    # Phrase queries catch clinical/biomedical work indexed under medicine
    # rather than the CS concept tree.
    (
        "clinical-nlp",
        'title_and_abstract.search:"clinical natural language processing"'
        ' OR "clinical text mining" OR "clinical NLP",has_abstract:true',
    ),
    (
        "biomedical-nlp",
        'title_and_abstract.search:"biomedical natural language processing"'
        ' OR "biomedical text mining",has_abstract:true',
    ),
    (
        "text-simplification",
        'title_and_abstract.search:"text simplification" OR "lexical simplification"'
        ' OR "readability assessment",has_abstract:true',
    ),
    (
        "mental-health-nlp",
        'title_and_abstract.search:"mental health" AND "natural language processing"'
        ",has_abstract:true",
    ),
]


@dataclass
class IngestStats:
    fetched: int = 0
    new_papers: int = 0
    linked_by_doi: int = 0
    refreshed: int = 0
    skipped_no_title: int = 0

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
    queries: Sequence[tuple[str, str]] = tuple(QUERIES),
) -> IngestStats:
    """Crawl every query; one transaction per work, so a crash mid-run loses
    at most one work and a rerun converges instead of duplicating."""
    stats = IngestStats()
    for name, filter_ in queries:
        print(f"query {name}: starting")
        for work in iter_works(client, bucket, filter_):
            if limit is not None and stats.fetched >= limit:
                print(f"--limit {limit} reached, stopping")
                return stats
            with conn.transaction():
                outcome = store_work(conn, work)
            stats.fetched += 1
            setattr(stats, outcome, getattr(stats, outcome) + 1)
            if stats.fetched % 1000 == 0:
                print(f"  {stats.summary()}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest OpenAlex works into sieve")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="stop after N works in total; smoke-test with --limit 100 before a full pull",
    )
    args = parser.parse_args()
    conninfo = os.environ.get("DATABASE_URL")
    if not conninfo:
        sys.exit("DATABASE_URL is not set")
    bucket = TokenBucket(rate=OPENALEX_RATE, capacity=OPENALEX_RATE)
    with make_client() as client, psycopg.connect(conninfo) as conn:
        stats = ingest(conn, client, bucket, limit=args.limit)
    print(stats.summary())


if __name__ == "__main__":
    main()
