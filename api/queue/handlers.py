"""Job handlers: what the worker actually runs.

Converting ingestion to the queue means one job per PAGE, not one job per
crawl. A page is the unit because it is the unit that is idempotent: a page
is identified by (source, query, offset), which is exactly the dedupe_key,
so enqueueing the same page twice is a no-op and re-running a crashed crawl
resumes rather than restarts.

Pages are self-perpetuating. A fetch_page job stores its own page and then
enqueues the NEXT page, inside the same transaction as its writes. That is
why there is no crawl coordinator: the queue holds the crawl's position,
committed with the data it describes, so killing every worker mid-crawl and
starting new ones resumes from the last committed page. A pre-enqueued list
of pages would have to know the result count up front, which none of these
APIs promises before the last page.

A short page ends the chain by simply not enqueuing a successor.

arXiv and PubMed are offset-paginated, so a page is addressable and this
works. OpenAlex is CURSOR-paginated: page N+1 is only reachable by holding
the cursor from page N, so a cursor crawl cannot be resumed from a dedupe
key and is not converted here. Its client keeps its own resumable loop.

Rate limiting stays per-process: each worker builds its own token bucket
sized to the source's documented limit. With N workers that is N times the
intended rate, so the deployment note is real — scale workers for embedding
throughput, not for fetch throughput, until the buckets are coordinated
through Postgres. That coordination is deliberately not built yet: it needs
a measured reason, and one worker per source saturates these limits already.
"""

import logging
from collections.abc import Mapping
from typing import Any

import psycopg

from api.ingest.pubmed import PUBMED_RATE
from api.ingest.pubmed import fetch_articles as pubmed_fetch
from api.ingest.pubmed import iter_pmids as pubmed_ids
from api.ingest.pubmed import make_client as pubmed_client
from api.ingest.pubmed import store_entry as pubmed_store
from api.ingest.ratelimit import TokenBucket
from api.queue.claim import enqueue
from api.queue.worker import Handler

logger = logging.getLogger("sieve.queue.handlers")

PUBMED_PAGE = 100


def pubmed_page_key(query_name: str, retstart: int) -> str:
    return f"pubmed:{query_name}:{retstart}"


def handle_pubmed_page(conn: psycopg.Connection, payload: dict[str, Any]) -> None:
    """Fetch one PubMed page, store it, and enqueue the next one.

    Everything here runs inside the worker's transaction, including the
    enqueue of the successor — so a crash rolls back the page AND its
    successor together, and the retry re-does exactly one page.
    """
    query_name = str(payload["query_name"])
    term = str(payload["term"])
    retstart = int(payload.get("retstart", 0))
    per_page = int(payload.get("per_page", PUBMED_PAGE))

    from api.ingest.pubmed import PubmedStats

    stats = PubmedStats()
    bucket = TokenBucket(rate=PUBMED_RATE, capacity=1.0)
    with pubmed_client() as client:
        pmids = list(
            pubmed_ids(client, bucket, term, stats, per_page=per_page, limit=per_page)
            if retstart == 0
            else _page_of(client, bucket, term, stats, retstart, per_page)
        )
        articles = pubmed_fetch(client, bucket, pmids, stats) if pmids else []

    for entry in articles:
        pubmed_store(conn, entry, query_name)

    logger.info(
        "pubmed page stored",
        extra={"query": query_name, "retstart": retstart, "articles": len(articles)},
    )
    # A short page means the result set is exhausted: end the chain.
    if len(pmids) == per_page:
        enqueue(
            conn,
            job_type="pubmed_page",
            payload={
                "query_name": query_name,
                "term": term,
                "retstart": retstart + per_page,
                "per_page": per_page,
            },
            dedupe_key=pubmed_page_key(query_name, retstart + per_page),
        )


def _page_of(
    client: Any, bucket: TokenBucket, term: str, stats: Any, retstart: int, per_page: int
) -> list[str]:
    """One esearch page at an explicit offset.

    iter_pmids() paginates from zero; a job knows its own offset and must
    not re-walk the pages before it, which would spend the whole crawl's
    rate budget on every job.
    """
    from api.ingest.http import get_json
    from api.ingest.pubmed import EMAIL, TOOL

    stats.esearch_requests += 1
    data = get_json(
        client,
        "/esearch.fcgi",
        params={
            "db": "pubmed",
            "term": term,
            "retmode": "json",
            "retstart": retstart,
            "retmax": per_page,
            "sort": "date",
            "tool": TOOL,
            "email": EMAIL,
        },
        bucket=bucket,
    )
    ids: list[str] = data.get("esearchresult", {}).get("idlist", [])
    return ids


def enqueue_pubmed_crawl(
    conn: psycopg.Connection, queries: list[tuple[str, str, float]], *, per_page: int = PUBMED_PAGE
) -> list[int]:
    """Seed one job per query. Every later page enqueues itself."""
    ids = []
    for name, term, _weight in queries:
        job_id = enqueue(
            conn,
            job_type="pubmed_page",
            payload={"query_name": name, "term": term, "retstart": 0, "per_page": per_page},
            dedupe_key=pubmed_page_key(name, 0),
        )
        if job_id is not None:
            ids.append(job_id)
    return ids


HANDLERS: Mapping[str, Handler] = {"pubmed_page": handle_pubmed_page}
