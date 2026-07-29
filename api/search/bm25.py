"""Keyword search over the fts column. Raw SQL — the query IS the module.

Honesty note, for the README and any interview: mode is named "bm25" per
the brief's API contract, but the ranking function is Postgres
ts_rank_cd — cover-density ranking, not BM25. It has no term-frequency
saturation (k1) and no corpus-level IDF; weight A/B title-vs-abstract
comes from the setweight() in the generated column. Implementing real BM25
from term statistics is a recorded possible upgrade, not a done thing.

Query parsing is websearch_to_tsquery, not to_tsquery: it accepts anything
a human types into a search box (quoted phrases, OR, -exclusions) and
never raises on malformed input — a stop-word-only query becomes an empty
tsquery that simply matches nothing.

The year filter uses the parameter-is-NULL pattern
(%(x)s IS NULL OR year >= %(x)s) so there is exactly ONE static SQL string
regardless of which filters are set: no string-built SQL, and the plan
cache sees one statement. Ties break on id so identical scores return in a
deterministic order — keyset pagination (Phase 4) depends on a total
order.
"""

from typing import Any

import psycopg
from psycopg.rows import dict_row

SEARCH_SQL = """
SELECT id, doi, title, abstract, year, venue, citation_count,
       ts_rank_cd(fts, q)::float8 AS score
FROM papers, websearch_to_tsquery('english', %(query)s) AS q
WHERE fts @@ q
  AND (%(year_from)s::smallint IS NULL OR year >= %(year_from)s)
  AND (%(year_to)s::smallint IS NULL OR year <= %(year_to)s)
ORDER BY score DESC, id
LIMIT %(k)s
"""


def search_bm25(
    conn: psycopg.Connection,
    *,
    query: str,
    k: int,
    year_from: int | None = None,
    year_to: int | None = None,
) -> list[dict[str, Any]]:
    """Top-k keyword matches, best first. Rows are dicts matching SEARCH_SQL."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            SEARCH_SQL,
            {"query": query, "k": k, "year_from": year_from, "year_to": year_to},
        )
        return cur.fetchall()
