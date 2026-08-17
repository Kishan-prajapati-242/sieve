"""GET /api/stats: corpus totals and per-query composition.

Composition is a README number (DECISION-2), so it comes from recorded
provenance, not from re-deriving topic membership. Raw SQL like every
reporting path. A paper can be fetched by several queries; for the
paper-level composition it is attributed to the query of its EARLIEST
source record (min id — insert order, stable under refresh, unlike
fetched_at which the upsert bumps). Records that predate the provenance
column and were never refetched report as "unattributed" — honest NULLs,
never guessed.
"""

import psycopg
from fastapi import APIRouter
from pydantic import BaseModel

from api.db.pool import get_pool

router = APIRouter(prefix="/api")

TOTALS_SQL = """
SELECT
    (SELECT count(*) FROM papers)                            AS papers,
    (SELECT count(*) FROM papers WHERE is_retracted)         AS retracted_papers,
    (SELECT count(*) FROM source_records)                    AS source_records,
    (SELECT count(*) FROM source_records WHERE paper_id IS NULL) AS unlinked_records
"""

# NULL embeddings became ROUTINE with DECISION-3a and dedup-before-embedding
# (findings.md 2026-07-31), and the failure mode is silent: such a paper is
# invisible to vector search and contributes nothing to fusion while still
# appearing via bm25, so retrieval just gets quietly worse. Hence a number,
# split by the cause where the cause is knowable.
EMBEDDING_COVERAGE_SQL = """
SELECT count(*)                                       AS total,
       count(embedding)                               AS embedded,
       count(*) - count(embedding)                    AS missing,
       count(*) FILTER (
           WHERE embedding IS NULL AND EXISTS (
               SELECT 1 FROM source_records sr
               WHERE sr.paper_id = papers.id AND sr.source <> 'openalex'
           )
       )                                              AS missing_awaiting_dedup,
       count(*) FILTER (
           WHERE embedding IS NULL AND created_at < now() - interval '1 hour'
             AND NOT EXISTS (
               SELECT 1 FROM source_records sr
               WHERE sr.paper_id = papers.id AND sr.source <> 'openalex'
           )
       )                                              AS missing_invalidated_or_stalled
FROM papers
"""

RECORDS_BY_QUERY_SQL = """
SELECT COALESCE(query_name, 'unattributed') AS query_name, count(*) AS n
FROM source_records
GROUP BY 1
ORDER BY n DESC, query_name
"""

PAPERS_BY_QUERY_SQL = """
SELECT COALESCE(first_rec.query_name, 'unattributed') AS query_name, count(*) AS n
FROM (
    SELECT DISTINCT ON (paper_id) paper_id, query_name
    FROM source_records
    WHERE paper_id IS NOT NULL
    ORDER BY paper_id, id
) AS first_rec
GROUP BY 1
ORDER BY n DESC, query_name
"""


class EmbeddingCoverage(BaseModel):
    """Which papers can participate in vector and hybrid search at all."""

    total: int
    embedded: int
    missing: int
    # New-source papers held back deliberately: dedup runs before embedding,
    # so these are correct-by-design, not a backlog.
    missing_awaiting_dedup: int
    # Older papers with no vector: a text change nulled it (DECISION-3a) and
    # the backfill has not caught up, or the backfill is stalled. Non-zero
    # here for long is the thing worth noticing.
    missing_invalidated_or_stalled: int


BY_SOURCE_SQL = """
SELECT source, count(*) AS records, count(DISTINCT paper_id) AS papers
FROM source_records
GROUP BY source
ORDER BY source
"""

MERGES_BY_STRATEGY_SQL = """
SELECT strategy, count(*) AS n
FROM merges
GROUP BY strategy
ORDER BY n DESC, strategy
"""

# Two facts an operator needs about the queue and cannot get from a count
# alone: how much work is waiting, and whether anything has given up.
# oldest_pending_age_s is the one that actually catches a stalled worker —
# a depth of 500 is fine if it is draining and alarming if it is not.
QUEUE_SQL = """
SELECT
    (SELECT count(*) FROM ingest_jobs WHERE status = 'pending')  AS pending,
    (SELECT count(*) FROM ingest_jobs WHERE status = 'running')  AS running,
    (SELECT count(*) FROM ingest_jobs WHERE status = 'done')     AS done,
    (SELECT count(*) FROM ingest_jobs WHERE status = 'dead')     AS dead,
    (SELECT EXTRACT(EPOCH FROM now() - min(created_at))
     FROM ingest_jobs WHERE status = 'pending' AND run_after <= now()) AS oldest_pending_age_s,
    (SELECT count(*) FROM ingest_jobs
     WHERE status = 'running' AND locked_at < now() - interval '15 minutes') AS stale_running
"""

# /api/stats is UNAUTHENTICATED — the landing page reads the corpus size from
# it before anyone signs in. It therefore may only report facts about the
# CORPUS, never about what users did with it.
#
# It previously returned global screening counts, which is other people's
# private review activity served to the open internet, and on a small instance
# it is worse than aggregate: with two users, one who knows their own count can
# derive the other's exactly. Removed rather than scoped, because a public
# endpoint has no caller to scope to.
SCREENING_SQL = """
SELECT (SELECT count(*) FROM collections) AS collections
"""


class SourceCounts(BaseModel):
    records: int
    papers: int


class QueueStats(BaseModel):
    """Queue depth by status, plus the two liveness signals."""

    pending: int
    running: int
    done: int
    dead: int
    # None when nothing is due; seconds since the oldest DUE job was created.
    oldest_pending_age_s: float | None
    # 'running' with no heartbeat: a worker died holding these. reap_stale()
    # is what returns them; a non-zero value that persists means nobody runs it.
    stale_running: int


class ScreeningStats(BaseModel):
    collections: int
    # screened/included deliberately absent: see SCREENING_SQL.


class StatsResponse(BaseModel):
    papers: int
    retracted_papers: int
    source_records: int
    unlinked_records: int
    papers_by_query: dict[str, int]
    records_by_query: dict[str, int]
    embedding_coverage: EmbeddingCoverage
    by_source: dict[str, SourceCounts]
    merges_by_strategy: dict[str, int]
    queue: QueueStats
    screening: ScreeningStats


def read_stats(conn: psycopg.Connection) -> StatsResponse:
    totals = conn.execute(TOTALS_SQL).fetchone()
    assert totals is not None
    papers, retracted, records, unlinked = totals
    coverage_row = conn.execute(EMBEDDING_COVERAGE_SQL).fetchone()
    assert coverage_row is not None
    records_by_query: dict[str, int] = dict(conn.execute(RECORDS_BY_QUERY_SQL).fetchall())
    papers_by_query: dict[str, int] = dict(conn.execute(PAPERS_BY_QUERY_SQL).fetchall())
    by_source = {
        str(src): SourceCounts(records=rec, papers=pap)
        for src, rec, pap in conn.execute(BY_SOURCE_SQL).fetchall()
    }
    merges_by_strategy: dict[str, int] = dict(conn.execute(MERGES_BY_STRATEGY_SQL).fetchall())
    queue_row = conn.execute(QUEUE_SQL).fetchone()
    screening_row = conn.execute(SCREENING_SQL).fetchone()
    assert queue_row is not None and screening_row is not None
    return StatsResponse(
        papers=papers,
        retracted_papers=retracted,
        source_records=records,
        unlinked_records=unlinked,
        papers_by_query=papers_by_query,
        records_by_query=records_by_query,
        embedding_coverage=EmbeddingCoverage(
            total=coverage_row[0],
            embedded=coverage_row[1],
            missing=coverage_row[2],
            missing_awaiting_dedup=coverage_row[3],
            missing_invalidated_or_stalled=coverage_row[4],
        ),
        by_source=by_source,
        merges_by_strategy=merges_by_strategy,
        queue=QueueStats(
            pending=queue_row[0],
            running=queue_row[1],
            done=queue_row[2],
            dead=queue_row[3],
            oldest_pending_age_s=queue_row[4],
            stale_running=queue_row[5],
        ),
        screening=ScreeningStats(
            collections=screening_row[0],

        ),
    )


@router.get("/stats")
def stats() -> StatsResponse:
    with get_pool().connection() as conn:
        return read_stats(conn)
