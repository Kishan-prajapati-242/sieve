"""Semantic search over the HNSW index. Raw SQL — the query IS the module.

The ORDER BY embedding <=> %(q)s::halfvec LIMIT k shape is what the
planner matches against papers_embed_idx (halfvec_cosine_ops); score
returned to callers is cosine SIMILARITY (1 - distance), so bm25 and
vector modes agree that higher is better.

Two session knobs, both SET LOCAL inside one transaction so nothing leaks
back into the pool:

  hnsw.ef_search — the recall/latency dial (candidate list size), request-
  tunable, default 40 (pgvector's default; the recall sweep tunes it).

  hnsw.iterative_scan = strict_order — the year-filter fix. A plain HNSW
  scan yields ~ef_search candidates and the WHERE filter runs AFTER: a
  narrow filter can strain them all out and silently return fewer than k
  rows. Iterative scan keeps traversing until the LIMIT is satisfied (or
  hnsw.max_scan_tuples caps it). strict_order over relaxed_order: exact
  distance order preserved, no re-sort subtlety; revisit for Phase 4
  tuning if filtered latency matters more than ordering simplicity.

Ties: not broken by id here — appending a secondary sort key would break
the pathkey match and cost the index scan. Exact-duplicate embeddings
(preprint twins) are Phase 3 dedup's problem, not the ORDER BY's.

Found while pinning the underfill test: for SELECTIVE year filters the
planner may skip HNSW entirely (papers_year_idx bitmap scan + sort by
distance) — exact results, no underfill possible. Both plans are correct;
iterative_scan protects the HNSW path when the planner takes it.
"""

from typing import Any

import psycopg
from psycopg.rows import dict_row

DEFAULT_EF_SEARCH = 160  # DECISION-4b (was 40)

VECTOR_SQL = """
SELECT id, doi, title, abstract, year, venue, citation_count, is_retracted, authors,
       (embedding <=> %(q)s::halfvec)::float8 AS distance
FROM papers
WHERE (%(year_from)s::smallint IS NULL OR year >= %(year_from)s)
  AND (%(year_to)s::smallint IS NULL OR year <= %(year_to)s)
ORDER BY embedding <=> %(q)s::halfvec
LIMIT %(k)s
"""


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in vec) + "]"


def search_vector(
    conn: psycopg.Connection,
    *,
    query_vec: list[float],
    k: int,
    year_from: int | None = None,
    year_to: int | None = None,
    ef_search: int = DEFAULT_EF_SEARCH,
) -> list[dict[str, Any]]:
    """Top-k nearest papers, best first. Rows match SEARCH_SQL's shape with
    score = cosine similarity. Must be the FIRST work on this connection's
    transaction, so SET LOCAL scopes to a real transaction, not a savepoint.
    """
    with conn.transaction():
        conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(ef_search),))
        conn.execute("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)")
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                VECTOR_SQL,
                {
                    "q": vector_literal(query_vec),
                    "k": k,
                    "year_from": year_from,
                    "year_to": year_to,
                },
            )
            rows = cur.fetchall()
    for row in rows:
        row["score"] = 1.0 - row.pop("distance")
    return rows
