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

import time
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


VECTOR_CAPPED_SQL = """
SELECT count(*) FROM (
    SELECT 1 FROM papers
    WHERE embedding IS NOT NULL
      AND (%(year_from)s::smallint IS NULL OR year >= %(year_from)s)
      AND (%(year_to)s::smallint IS NULL OR year <= %(year_to)s)
    LIMIT %(depth)s
) t
"""


def bm25_total(conn: psycopg.Connection, params: dict[str, Any]) -> tuple[int, Kind]:
    row = conn.execute(BM25_COUNT_SQL, params).fetchone()
    return (int(row[0]) if row else 0), "matches"


# The unfiltered embedded-corpus count is a 76 ms index-only scan of 183,167
# rows (measured 2026-08-14) whose answer is a CONSTANT between ingests. Paying
# it per request took vector mode from 2.0 ms to 94.1 ms — a 47x regression to
# display a number that had not changed, on the same screen that now shows the
# latency. Cached with a short TTL: still a real count, just not re-counted
# every keystroke. A year filter makes it query-dependent, so that path is
# never cached.
_CORPUS_TTL_S = 60.0
_corpus_cache: tuple[float, int] | None = None


def vector_total(conn: psycopg.Connection, params: dict[str, Any]) -> tuple[int, Kind]:
    global _corpus_cache
    filtered = params.get("year_from") is not None or params.get("year_to") is not None
    if not filtered:
        now = time.monotonic()
        if _corpus_cache is not None and now - _corpus_cache[0] < _CORPUS_TTL_S:
            return _corpus_cache[1], "ranked"
    row = conn.execute(VECTOR_COUNT_SQL, params).fetchone()
    value = int(row[0]) if row else 0
    if not filtered:
        _corpus_cache = (time.monotonic(), value)
    return value, "ranked"


def hybrid_total(
    conn: psycopg.Connection, params: dict[str, Any], depth: int, ef_search: int
) -> tuple[int, Kind]:
    """|bm25 candidates ∪ vector candidates| at the configured depth.

    The bm25 arm contributes min(matches, depth); the vector arm contributes
    min(corpus, depth), which is depth for any real corpus. The overlap cannot
    be known without running the fusion, so this counts it directly rather
    than estimating it.
    """
    matches, _ = bm25_total(conn, params)
    # NOT vector_total(): hybrid needs min(corpus, depth), and the corpus is
    # three orders of magnitude larger than any depth we run. Counting to the
    # cap answers the same question and stops after `depth` rows instead of
    # scanning 183,167 of them.
    row = conn.execute(VECTOR_CAPPED_SQL, {**params, "depth": depth}).fetchone()
    vec_side = int(row[0]) if row else 0
    overlap = _overlap(conn, params, depth, ef_search)
    return min(matches, depth) + vec_side - overlap, "candidates"


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
