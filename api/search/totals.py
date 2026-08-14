"""How many papers a query reached — and what that number MEANS per mode.

A single "20 of N" would be the unlabeled-number failure this project has
caught five times, on the most visible surface in the app (Kishan,
2026-08-14). N is three different quantities:

  bm25       a genuine MATCH count. `fts @@ q` either matches or it does
             not, so "4,187 matches" is true and useful.

  vector     meaningless as a match count. Every paper has an embedding, so
             every paper is a candidate and N is the corpus — 183,167. The
             number is true and says nothing about the query. Reported as
             "ranked all N", which is what actually happened.

  hybrid     the FUSED CANDIDATE POOL: depth from the vector arm, plus the
             bm25 matches it took, minus the overlap — 202 on the demo
             query (200 + 5 - 3). That is a function of `depth`, OUR tuning
             parameter, so labelling it "results" would report our own
             configuration back to the reader as a property of the corpus.
             Reported as "N candidates fused".

So `kind` travels with the number and the UI must render the label; a bare
integer is not offered.
"""

from typing import Any, Literal

import psycopg

Kind = Literal["matches", "ranked", "candidates"]

BM25_COUNT_SQL = """
SELECT count(*) FROM papers, websearch_to_tsquery('english', %(query)s) AS q
WHERE fts @@ q
  AND (%(year_from)s::smallint IS NULL OR year >= %(year_from)s)
  AND (%(year_to)s::smallint IS NULL OR year <= %(year_to)s)
"""

VECTOR_COUNT_SQL = """
SELECT count(*) FROM papers
WHERE embedding IS NOT NULL
  AND (%(year_from)s::smallint IS NULL OR year >= %(year_from)s)
  AND (%(year_to)s::smallint IS NULL OR year <= %(year_to)s)
"""


def bm25_total(conn: psycopg.Connection, params: dict[str, Any]) -> tuple[int, Kind]:
    row = conn.execute(BM25_COUNT_SQL, params).fetchone()
    return (int(row[0]) if row else 0), "matches"


def vector_total(conn: psycopg.Connection, params: dict[str, Any]) -> tuple[int, Kind]:
    row = conn.execute(VECTOR_COUNT_SQL, params).fetchone()
    return (int(row[0]) if row else 0), "ranked"


def hybrid_total(
    conn: psycopg.Connection, params: dict[str, Any], depth: int, ef_search: int
) -> tuple[int, Kind]:
    """|bm25 candidates ∪ vector candidates| at the configured depth.

    The bm25 arm contributes min(matches, depth); the vector arm always
    contributes exactly depth (it ranks everything). The overlap cannot be
    known without running the fusion, so this counts the union directly
    rather than estimating it.
    """
    matches, _ = bm25_total(conn, params)
    corpus, _ = vector_total(conn, params)
    overlap = _overlap(conn, params, depth, ef_search)
    return min(matches, depth) + min(corpus, depth) - overlap, "candidates"


OVERLAP_SQL = """
WITH bm AS (
    SELECT id FROM papers, websearch_to_tsquery('english', %(query)s) AS q
    WHERE fts @@ q
      AND (%(year_from)s::smallint IS NULL OR year >= %(year_from)s)
      AND (%(year_to)s::smallint IS NULL OR year <= %(year_to)s)
    ORDER BY ts_rank_cd(fts, q) DESC, id
    LIMIT %(depth)s
), vec AS (
    SELECT id FROM papers
    WHERE embedding IS NOT NULL
      AND (%(year_from)s::smallint IS NULL OR year >= %(year_from)s)
      AND (%(year_to)s::smallint IS NULL OR year <= %(year_to)s)
    ORDER BY embedding <=> %(qv)s::halfvec
    LIMIT %(depth)s
)
SELECT count(*) FROM bm JOIN vec USING (id)
"""


def _overlap(conn: psycopg.Connection, params: dict[str, Any], depth: int, ef_search: int) -> int:
    """Count the overlap under the SAME index settings fusion used.

    Measured 2026-08-14: without this the vector CTE runs at the default
    ef_search=40 with iterative_scan off, so it retrieves a different (and
    worse) top-`depth` than the fused query did — the reported pool size
    then describes a candidate set the search never built. The first run
    returned overlap=1 while three rows in the visible top-20 carried BOTH
    ranks, which is impossible and is what exposed it.
    """
    with conn.transaction():
        conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(ef_search),))
        conn.execute("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)")
        row = conn.execute(OVERLAP_SQL, {**params, "depth": depth}).fetchone()
    return int(row[0]) if row else 0
