"""Hybrid search: Reciprocal Rank Fusion of bm25 and vector, in ONE SQL
statement. Raw SQL — the query IS the module.

Two CTEs each produce (id, rank) for their top-`depth` candidates via
ROW_NUMBER(); a FULL OUTER JOIN unions the candidate sets; the fused score
is sum(1 / (rrf_k + rank)) over the rankers that retrieved the paper
(RRF, Cormack et al. 2009 — rank-based, so the incomparable score scales
of ts_rank_cd and cosine similarity never meet). rrf_k = 60, the paper's
constant; it damps the top-rank dominance and is not exposed as a knob
until an eval says it should be.

Single statement, deliberately: the planner shares the papers relation,
one round trip, and the fusion arithmetic is inspectable in EXPLAIN. The
cost is that per-component timing (bm25 vs vector vs join) is not
observable from the client — that decomposition lives in the committed
EXPLAIN ANALYZE (docs/plans/), not in the per-request timings.

Both CTEs carry the year filter: filtering after fusion would starve the
join of candidates the same way naive HNSW post-filtering under-returns.
The vector CTE's ROW_NUMBER window ORDER BY matches the scan's pathkey, so
papers_embed_idx drives it (verified in the committed plan); the bm25 CTE
sorts its GIN-filtered matches like the plain bm25 mode does.

ef_search must be >= depth or the vector CTE silently truncates below the
requested candidate list — the route auto-raises it; search_hybrid
asserts it as a guard against other callers.
"""

from typing import Any

import psycopg
from psycopg.rows import dict_row

from api.search.vector import vector_literal

RRF_K = 60

# DECISION-2e: recall@200 = .9857 at ef=600 vs .9431 at ef=200, and 600's
# cost is below measurement resolution on this hardware (its p50 sits
# inside ef=200's own cross-run range). Revisit when Phase 4 fixes the
# bm25 tail: bm25 variance currently swamps the vector CTE, so ef's real
# cost is invisible.
HYBRID_DEFAULT_EF_SEARCH = 600

HYBRID_SQL = """
WITH bm25 AS (
    SELECT id,
           ROW_NUMBER() OVER (ORDER BY ts_rank_cd(fts, q) DESC, id) AS rank
    FROM papers, websearch_to_tsquery('english', %(query)s) AS q
    WHERE fts @@ q
      AND (%(year_from)s::smallint IS NULL OR year >= %(year_from)s)
      AND (%(year_to)s::smallint IS NULL OR year <= %(year_to)s)
    ORDER BY ts_rank_cd(fts, q) DESC, id
    LIMIT %(depth)s
),
vec AS (
    SELECT id,
           ROW_NUMBER() OVER (ORDER BY embedding <=> %(qv)s::halfvec) AS rank
    FROM papers
    WHERE (%(year_from)s::smallint IS NULL OR year >= %(year_from)s)
      AND (%(year_to)s::smallint IS NULL OR year <= %(year_to)s)
    ORDER BY embedding <=> %(qv)s::halfvec
    LIMIT %(depth)s
)
SELECT p.id, p.doi, p.title, p.abstract, p.year, p.venue, p.citation_count,
       p.is_retracted, p.authors,
       b.rank AS bm25_rank,
       v.rank AS vector_rank,
       -- The fused candidate pool, free. The FULL OUTER JOIN *is* the union
       -- of the two arms, and the inner join to papers drops nothing (both
       -- CTEs draw their ids from it), so the row count here is exactly
       -- |bm25 candidates u vector candidates|. A window function is
       -- evaluated after the joins and BEFORE ORDER BY/LIMIT, and the query
       -- already has to materialize and sort every candidate to find the
       -- top k -- so this reads a number the plan had already computed.
       -- Counting it separately cost an entire second ef=600 vector search:
       -- 11 ms on a query that otherwise takes 18, for an integer in hand.
       -- (A literal percent sign here is parsed as a psycopg placeholder
       -- even inside a SQL comment. Found the hard way, 2026-08-14.)
       count(*) OVER () AS pool_total,
       (COALESCE(1.0 / (%(rrf_k)s + b.rank), 0) +
        COALESCE(1.0 / (%(rrf_k)s + v.rank), 0))::float8 AS score
FROM bm25 b
FULL OUTER JOIN vec v USING (id)
JOIN papers p USING (id)
ORDER BY score DESC, id
LIMIT %(k)s
"""


def search_hybrid(
    conn: psycopg.Connection,
    *,
    query: str,
    query_vec: list[float],
    k: int,
    depth: int,
    year_from: int | None = None,
    year_to: int | None = None,
    ef_search: int,
) -> list[dict[str, Any]]:
    """Top-k RRF-fused papers, best first. Rows carry bm25_rank/vector_rank
    (NULL when that ranker missed the paper at this depth), score = RRF, and
    pool_total = the size of the fused candidate set the top-k was drawn from
    (identical on every row; the caller reads it off any one of them).
    Must be the first work on this connection, so SET LOCAL scopes to a
    real transaction (the savepoint trap, findings.md 2026-07-30)."""
    if ef_search < depth:
        raise ValueError(f"ef_search ({ef_search}) < depth ({depth}) silently truncates")
    with conn.transaction():
        conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(ef_search),))
        conn.execute("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)")
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                HYBRID_SQL,
                {
                    "query": query,
                    "qv": vector_literal(query_vec),
                    "k": k,
                    "depth": depth,
                    "rrf_k": RRF_K,
                    "year_from": year_from,
                    "year_to": year_to,
                },
            )
            return cur.fetchall()
