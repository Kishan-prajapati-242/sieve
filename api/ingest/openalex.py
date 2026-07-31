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
import json
import os
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import psycopg

from api.ingest.http import QuotaExhausted, RequestMeter, get_json
from api.ingest.ratelimit import TokenBucket
from api.ingest.store import (
    PaperFields,
    create_or_link_paper,
    owns_paper,
    refresh_paper,
    upsert_record,
)

BASE_URL = "https://api.openalex.org"
USER_AGENT = "sieve/0.1 (mailto:prajapati.kish@northeastern.edu)"

# Documented ceiling is 10 req/s and 100K/day; 5/s leaves polite headroom
# and still fetches 50K works (250 pages of 200) in under a minute of
# request budget.
OPENALEX_RATE = 5.0

# DECISION-1c: junk document types never become papers (their raw records
# are still stored — audit trail). Measured 2026-07-29: 144 of 26,378
# papers (paratext proceedings volumes were ranking beside their own member
# papers, carrying identical abstracts). Deliberately NOT here:
# is_retracted=true works — a screening tool must show retracted papers so
# reviewers exclude them on purpose; they carry a flag instead.
EXCLUDED_TYPES = frozenset(
    {"paratext", "editorial", "erratum", "supplementary-materials", "peer-review", "retraction"}
)

SELECT_FIELDS = ",".join(
    [
        "id",
        "doi",
        "display_name",
        "publication_year",
        "type",  # article | conference-paper | paratext | editorial | ...
        "is_paratext",
        "is_retracted",
        "primary_location",
        "locations",  # venue fallback chain; primary alone is null for ~1/3 of works
        "cited_by_count",
        "abstract_inverted_index",
        "authorships",
        "ids",
    ]
)

# The corpus composition (DECISION-2, Kishan, 2026-07-29): topic filters
# replaced the deprecated concept plus exact-phrase searches. Topics are
# both broader (no exact-phrase result cap — clinical-nlp maxed out at ~919
# works total) and list-class, i.e. 10x cheaper per page than .search:
# filters (measured: 182 vs 13 works per credit). has_abstract:true
# throughout — the abstract feeds both fts and the Phase 2 embeddings.
#
# Weights are NOMINAL WORK TARGETS at the full 205K pull; split_budget
# normalizes, so --limit 205000 hits these exactly and smaller limits
# scale proportionally. Budgets are fixed, not rolled over: a query that
# exhausts below budget reports the shortfall rather than silently
# backfilling from the broad query. Measured pools (works with abstracts,
# meta.count 2026-07-29): T10181 376K, T11710 224K, T10350|T13702 170K,
# T13629 49K, T12488 54K — every budget sits under its pool.
QUERIES: list[tuple[str, str, float]] = [
    ("general-nlp", "topics.id:T10181,has_abstract:true", 60_000),
    ("biomedical-clinical-text", "topics.id:T11710,has_abstract:true", 70_000),
    # Deliberately small (Kishan): T10350/T13702 are largely not NLP — at
    # 20% of the corpus they stop being hard negatives and start skewing
    # the Phase 4 hybrid-vs-BM25 comparison. 10%, not more.
    ("clinical-informatics", "topics.id:T10350|T13702,has_abstract:true", 20_000),
    ("text-simplification", "topics.id:T13629,has_abstract:true", 25_000),
    ("mental-health-nlp", "topics.id:T12488,has_abstract:true", 25_000),
    # The original exact-phrase queries, kept as a small high-precision
    # core. Result-capped by nature (they exhaust below budget and report
    # it); search-class, so 10x page cost — at these budgets that is noise.
    (
        "clinical-nlp-phrase",
        'title_and_abstract.search:"clinical natural language processing"'
        ' OR "clinical text mining" OR "clinical NLP",has_abstract:true',
        1_250,
    ),
    (
        "biomedical-nlp-phrase",
        'title_and_abstract.search:"biomedical natural language processing"'
        ' OR "biomedical text mining",has_abstract:true',
        1_250,
    ),
    (
        "text-simplification-phrase",
        'title_and_abstract.search:"text simplification" OR "lexical simplification"'
        ' OR "readability assessment",has_abstract:true',
        1_250,
    ),
    (
        "mental-health-nlp-phrase",
        'title_and_abstract.search:"mental health" AND "natural language processing"'
        ",has_abstract:true",
        1_250,
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
    # Refresh that moved title/abstract, so DECISION-3a nulled the vector
    # and the embedding queue will recompute it.
    refreshed_text_changed: int = 0
    skipped_no_title: int = 0
    # DECISION-1c: junk types skipped at ingest, counted per type so a run
    # shows what it refused, not just what it took.
    skipped_by_type: dict[str, int] = field(default_factory=dict)
    per_query: dict[str, int] = field(default_factory=dict)
    # Credits, not requests: search-filter pages bill 10x list pages, so the
    # spend profile is invisible in request counts. Populated when a
    # RequestMeter is passed to ingest().
    per_query_credits: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"fetched={self.fetched} new_papers={self.new_papers}"
            f" linked_by_doi={self.linked_by_doi} refreshed={self.refreshed}"
            f" refreshed_text_changed={self.refreshed_text_changed}"
            f" skipped_no_title={self.skipped_no_title}"
        )


def make_client(
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
    meter: RequestMeter | None = None,
) -> httpx.Client:
    """The only place an OpenAlex connection is configured: explicit timeout,
    mailto User-Agent, and the api_key as a default query param so EVERY
    request through this client carries it — OpenAlex bills per request and
    a keyless request runs against the $0.01/day anonymous budget.

    transport is injectable for tests; meter hooks count what a run spends.
    """
    event_hooks: dict[str, list[Callable[..., Any]]] = {}
    if meter is not None:
        event_hooks = {"request": [meter.on_request], "response": [meter.on_response]}
    return httpx.Client(
        base_url=BASE_URL,
        headers={"User-Agent": USER_AGENT},
        params={"api_key": api_key} if api_key else {},
        timeout=httpx.Timeout(15.0, connect=5.0),
        transport=transport,
        event_hooks=event_hooks,
    )


def extract_venue(work: dict[str, Any]) -> str | None:
    """Venue via fallback chain; a single field misses a third of the corpus.

    Canonical first: source.display_name on primary_location, then on each
    entry of locations[] — these are OpenAlex's deduplicated Source entities.
    Then raw_source_name in the same order: publisher-deposited free text,
    less clean, but it is where the venue lives for works whose source
    entity is unlinked — measured 2026-07-29, 92% of the corpus's ACL
    Anthology papers (DOI 10.18653/...) had source null everywhere while
    raw_source_name carried the full proceedings title.
    """
    spots = [work.get("primary_location"), *(work.get("locations") or [])]
    for key in ("source", "raw_source_name"):
        for spot in spots:
            if not spot:
                continue
            if key == "source":
                name = (spot.get("source") or {}).get("display_name")
            else:
                name = spot.get("raw_source_name")
            if name:
                return str(name)
    return None


def extract_authors(work: dict[str, Any]) -> list[str] | None:
    """Author display names in publication order; None when OpenAlex has none.

    Reads authorships[].author.display_name and nothing else — positions and
    institutions stay in the raw record. None (not []) when empty, so the
    column reads NULL for "unknown" rather than asserting zero authors.
    """
    names = [
        name
        for a in work.get("authorships") or []
        if (name := (a.get("author") or {}).get("display_name"))
    ]
    return [str(n) for n in names] or None


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


def paper_fields(work: dict[str, Any]) -> PaperFields:
    """Derive the source-agnostic paper shape from an OpenAlex work."""
    ids = work.get("ids") or {}
    pmid = (ids.get("pmid") or "").removeprefix("https://pubmed.ncbi.nlm.nih.gov/") or None
    return PaperFields(
        title=str(work["display_name"]),
        abstract=deinvert_abstract(work.get("abstract_inverted_index")),
        year=work.get("publication_year"),
        venue=extract_venue(work),
        citation_count=work.get("cited_by_count") or 0,
        doi=work.get("doi"),
        is_retracted=bool(work.get("is_retracted")),
        authors=extract_authors(work),
        pubmed_id=pmid,
    )


def store_work(conn: psycopg.Connection, work: dict[str, Any], query_name: str | None) -> str:
    """Upsert one work; returns the IngestStats field to bump.

    Rerun-safe by construction: the source_records upsert converges and only
    unlinked records ever DERIVE a paper. query_name is first-fetch
    provenance: the COALESCE keeps the original attribution on refresh, so
    composition stats don't churn with query order (DECISION-2).

    An already-linked record now REFRESHES its paper rather than stopping
    (2026-07-31): citation counts move, and — the real driver — a paper
    retracted after its crawl would otherwise never show a warning. Text
    changes null the embedding at the write site (DECISION-3a).
    """
    source_id = work["id"].removeprefix("https://openalex.org/")
    record_id, paper_id = upsert_record(
        conn, source="openalex", source_id=source_id, raw=work, query_name=query_name
    )

    title = work.get("display_name")
    if not title:
        # Unsearchable and unmergeable; raw stays for the audit trail. The
        # record remains unlinked, so a future fetch with a title picks it up.
        return "skipped_no_title"

    work_type = work.get("type")
    if work_type in EXCLUDED_TYPES:
        # Junk type (DECISION-1c): audit row kept, no paper derived. The
        # record stays unlinked, and this same check keeps it that way on
        # every refetch.
        return f"skipped_type:{work_type}"

    fields = paper_fields(work)
    if paper_id is not None:
        # Only the owning record writes the paper's text; secondary records
        # (linked by DOI collision) refresh their raw and stop, or they would
        # fight over the row on every crawl.
        if not owns_paper(conn, record_id, paper_id):
            return "refreshed"
        text_changed = refresh_paper(conn, paper_id, fields)
        return "refreshed_text_changed" if text_changed else "refreshed"

    _, outcome = create_or_link_paper(conn, record_id, fields)
    return outcome


def ingest(
    conn: psycopg.Connection,
    client: httpx.Client,
    bucket: TokenBucket,
    *,
    limit: int | None = None,
    queries: Sequence[tuple[str, str, float]] = tuple(QUERIES),
    slices: Sequence[tuple[str, str]] | None = None,
    meter: RequestMeter | None = None,
) -> IngestStats:
    """Crawl every query within its own budget share of --limit, each query
    stratified evenly across year slices (DECISION-1b).

    Budgets are per query, not a global cap, so the broad concept crawl can
    never starve the specialty ones; within a query the same largest-
    remainder split spreads its budget across the year slices. One
    transaction per work: a crash mid-run loses at most one work and a rerun
    converges, never duplicates.
    """
    if not conn.autocommit:
        # Same savepoint trap as the embed backfill (docs/findings.md
        # 2026-07-30): without autocommit, the per-work conn.transaction()
        # is a savepoint inside one giant implicit transaction, and the
        # crash-loses-one-work promise above is false — a kill loses the
        # whole run. Every clean exit had been silently committing at close.
        raise ValueError("ingest requires an autocommit connection; per-work commits are the point")
    if slices is None:
        slices = year_slices(datetime.now(UTC).year)
    budgets: list[int | None]
    if limit is None:
        budgets = [None] * len(queries)
    else:
        budgets = list(split_budget(limit, [w for _, _, w in queries]))
    stats = IngestStats()
    try:
        for (name, filter_, _), budget in zip(queries, budgets, strict=True):
            stats.per_query[name] = 0
            if meter is not None:
                query_credits_start = meter.credits_spent
                stats.per_query_credits[name] = 0
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
                    with conn.transaction():
                        outcome = store_work(conn, work, name)
                    taken += 1
                    stats.fetched += 1
                    stats.per_query[name] += 1
                    if outcome.startswith("skipped_type:"):
                        skipped = outcome.removeprefix("skipped_type:")
                        stats.skipped_by_type[skipped] = stats.skipped_by_type.get(skipped, 0) + 1
                    else:
                        setattr(stats, outcome, getattr(stats, outcome) + 1)
                    if stats.fetched % 1000 == 0:
                        print(f"  {stats.summary()}")
                    if slice_budget is not None and taken >= slice_budget:
                        # Break BEFORE the generator resumes: resuming after
                        # the page is drained fetches (and bills) the next
                        # page just to throw it away — at per_page ==
                        # slice_budget that doubled every slice's cost.
                        break
                if slice_budget is not None and taken < slice_budget:
                    print(f"  {name}/{slice_name}: exhausted at {taken} of {slice_budget}")
                if meter is not None:
                    stats.per_query_credits[name] = meter.credits_spent - query_credits_start
    except QuotaExhausted as exc:
        # The daily budget is spent; everything stored so far is committed.
        # Stopping is the correct move — the crawl is idempotent, so a rerun
        # after the reset re-walks cheaply and continues where the data ends.
        hours = exc.retry_after_s / 3600
        print(f"stopping: {exc}")
        print(f"budget resets in ~{hours:.1f}h; rerun then — the crawl is idempotent")
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
    parser.add_argument(
        "--check-budget",
        action="store_true",
        help="print the remaining OpenAlex budget and reset time, then exit",
    )
    args = parser.parse_args()

    api_key = os.environ.get("OPENALEX_API_KEY")
    if not api_key:
        # Deliberately fatal: a keyless run silently gets the $0.01/day
        # anonymous budget (~100 requests) instead of the free key's $1/day,
        # and dies mid-crawl with 429s that look like a rate bug.
        sys.exit(
            "OPENALEX_API_KEY is not set. OpenAlex requires an API key: without one"
            " the anonymous budget is $0.01/day; a free key raises it to $1/day."
            " Get a key via https://openalex.org/pricing and add it to .env"
            " (see developers.openalex.org)."
        )

    meter = RequestMeter()
    bucket = TokenBucket(rate=OPENALEX_RATE, capacity=OPENALEX_RATE)

    if args.check_budget:
        with make_client(api_key=api_key, meter=meter) as client:
            try:
                info = get_json(client, "/rate-limit", params={}, bucket=bucket)
            except QuotaExhausted as exc:
                sys.exit(f"budget already exhausted: {exc}")
        print(json.dumps(info, indent=2))
        return

    conninfo = os.environ.get("DATABASE_URL")
    if not conninfo:
        sys.exit("DATABASE_URL is not set")
    with (
        make_client(api_key=api_key, meter=meter) as client,
        psycopg.connect(conninfo, autocommit=True) as conn,
    ):
        # Price this run with the server's CURRENT cost table rather than our
        # measured defaults, and show the budget before spending any of it.
        # /rate-limit itself is free (singleton class).
        try:
            rl = get_json(client, "/rate-limit", params={}, bucket=bucket)["rate_limit"]
        except QuotaExhausted as exc:
            sys.exit(f"budget already exhausted: {exc}")
        meter.credit_costs.update(rl.get("credit_costs", {}))
        print(
            f"budget: {rl['credits_remaining']} of {rl['credits_limit']} credits,"
            f" resets in {rl['resets_in_seconds']}s"
        )
        stats = ingest(conn, client, bucket, limit=args.limit, meter=meter)
    print(stats.summary())
    for name, count in stats.per_query.items():
        print(f"  {name}: {count} works, {stats.per_query_credits.get(name, 0)} credits")
    for skipped, count in sorted(stats.skipped_by_type.items()):
        print(f"  skipped type={skipped}: {count}")
    spent = f"credits this run: {meter.credits_spent} ({meter.requests} requests)"
    if meter.remaining is not None:
        spent += f"; server reports {meter.remaining} credits remaining"
    print(spent)


if __name__ == "__main__":
    main()
