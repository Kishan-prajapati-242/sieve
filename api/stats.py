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


class StatsResponse(BaseModel):
    papers: int
    retracted_papers: int
    source_records: int
    unlinked_records: int
    papers_by_query: dict[str, int]
    records_by_query: dict[str, int]


def read_stats(conn: psycopg.Connection) -> StatsResponse:
    totals = conn.execute(TOTALS_SQL).fetchone()
    assert totals is not None
    papers, retracted, records, unlinked = totals
    records_by_query: dict[str, int] = dict(conn.execute(RECORDS_BY_QUERY_SQL).fetchall())
    papers_by_query: dict[str, int] = dict(conn.execute(PAPERS_BY_QUERY_SQL).fetchall())
    return StatsResponse(
        papers=papers,
        retracted_papers=retracted,
        source_records=records,
        unlinked_records=unlinked,
        papers_by_query=papers_by_query,
        records_by_query=records_by_query,
    )


@router.get("/stats")
def stats() -> StatsResponse:
    with get_pool().connection() as conn:
        return read_stats(conn)
