"""The dedup cascade: candidate pairs, grouping, and survivorship.

Strategy order is precision-first, and every step is INDEPENDENT of the
others — each proposes pairs, and grouping happens once at the end. Order
still matters for attribution: a pair found by an earlier (more precise)
strategy keeps that strategy's name in the merges row, so
bench/dedup_precision.py can measure precision per step rather than
per cascade.

  doi_exact       identical normalized DOI. Already enforced by a UNIQUE
                  constraint at insert, so it proposes nothing among
                  existing papers — kept in the cascade because it IS the
                  first line for future ingestion, and its absence from
                  the plan is itself worth reporting.
  id_exact        same arxiv_id, or same pubmed_id. Cross-source glue.
  abstract_hash   identical abstract, GATED on boilerplate_abstracts —
                  without that gate this step would merge 801 unrelated
                  papers sharing the string "International audience".
  title_year      identical normalized title AND same year.
  title_trgm      trigram-similar title AND same year AND a shared author
                  surname. The surname condition is the guard against
                  generic titles: "Results" published twice in 2024 by
                  different groups must not merge.

Under-merging is safer than over-merging (Kishan): a missed duplicate
costs a redundant row, a wrong merge destroys a distinct paper.

Grouping is UNION-FIND over the proposed pairs, because the cascade does
NOT produce transitive closure naturally: A~B by DOI and B~C by title are
found by different steps and would otherwise stay separate merges of the
same real paper. Union-find also makes the result order-independent.
"""

from dataclasses import dataclass
from typing import Any

import psycopg

TRGM_THRESHOLD = 0.92

# Each strategy returns (a_id, b_id, similarity). Exact-key steps are
# GROUPED, not self-joined: a self-join on an unindexed expression is
# O(n^2) and hung for 11 minutes on 197K papers before being killed. Group
# by the key, then fan out pairs from the group's first member — union-find
# rebuilds the same component from that star, at a fraction of the cost.
STRATEGY_SQL: dict[str, str] = {
    # papers.doi is UNIQUE, so this can only fire if that constraint is ever
    # relaxed. It runs anyway: a silent zero is information.
    "doi_exact": """
        SELECT ids[1], unnest(ids[2:]), 1.0 FROM (
            SELECT array_agg(id ORDER BY id) AS ids FROM papers
            WHERE doi IS NOT NULL GROUP BY doi HAVING count(*) > 1
        ) g
    """,
    "id_exact": """
        SELECT ids[1], unnest(ids[2:]), 1.0 FROM (
            SELECT array_agg(id ORDER BY id) AS ids FROM papers
            WHERE arxiv_id IS NOT NULL GROUP BY arxiv_id HAVING count(*) > 1
            UNION ALL
            SELECT array_agg(id ORDER BY id) FROM papers
            WHERE pubmed_id IS NOT NULL GROUP BY pubmed_id HAVING count(*) > 1
        ) g
    """,
    "abstract_hash": """
        SELECT ids[1], unnest(ids[2:]), 1.0 FROM (
            SELECT array_agg(p.id ORDER BY p.id) AS ids
            FROM papers p
            WHERE p.abstract IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM boilerplate_abstracts x
                  WHERE x.abstract_md5 = md5(p.abstract)
              )
            GROUP BY md5(p.abstract) HAVING count(*) > 1
        ) g
    """,
    "title_exact": """
        SELECT ids[1], unnest(ids[2:]), 1.0 FROM (
            SELECT array_agg(id ORDER BY id) AS ids FROM papers
            WHERE year IS NOT NULL AND length(title_norm) >= 20
            GROUP BY title_norm, year HAVING count(*) > 1
        ) g
    """,
    # The GIN trigram index drives `%`; similarity() then scores survivors.
    # Same year AND a shared author surname, both required — the surname is
    # the guard against generic titles ("Results", twice in 2024).
    "title_trgm": """
        SELECT a.id, b.id, similarity(a.title_norm, b.title_norm)::float8 AS sim
        FROM papers a JOIN papers b
          ON a.id < b.id
         AND a.year = b.year
         AND a.title_norm %% b.title_norm
        WHERE a.year IS NOT NULL
          AND length(a.title_norm) >= 20
          AND a.title_norm <> b.title_norm
          AND similarity(a.title_norm, b.title_norm) >= %(threshold)s
          AND a.authors IS NOT NULL AND b.authors IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM unnest(a.authors) sa, unnest(b.authors) sb
              WHERE lower(split_part(sa, ' ', array_length(string_to_array(sa, ' '), 1)))
                  = lower(split_part(sb, ' ', array_length(string_to_array(sb, ' '), 1)))
                AND length(split_part(sa, ' ', array_length(string_to_array(sa, ' '), 1))) >= 3
          )
    """,
}


@dataclass
class Pair:
    a: int
    b: int
    similarity: float
    strategy: str


class UnionFind:
    """Groups pairs into components. Needed because different strategies
    find different edges of the same real duplicate set."""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for node in self.parent:
            out.setdefault(self.find(node), []).append(node)
        return {root: sorted(members) for root, members in out.items() if len(members) > 1}


def find_pairs(conn: psycopg.Connection, strategies: list[str] | None = None) -> list[Pair]:
    """Run each strategy and collect proposed pairs, first strategy wins
    attribution for a pair both propose."""
    seen: set[tuple[int, int]] = set()
    pairs: list[Pair] = []
    for name in strategies or list(STRATEGY_SQL):
        rows = conn.execute(STRATEGY_SQL[name], {"threshold": TRGM_THRESHOLD}).fetchall()
        for a, b, sim in rows:
            if (a, b) in seen:
                continue
            seen.add((a, b))
            pairs.append(Pair(a=a, b=b, similarity=float(sim), strategy=name))
    return pairs


# DECISION-3b survivorship, as a sortable key computed in SQL. Highest
# publication_rank wins; ties break on lowest id for determinism.
SURVIVOR_SQL = """
SELECT p.id,
       p.title,
       p.year,
       p.venue,
       p.citation_count,
       p.doi,
       p.arxiv_id,
       p.pubmed_id,
       CASE
         WHEN EXISTS (SELECT 1 FROM source_records sr
                      WHERE sr.paper_id = p.id
                        AND (sr.source = 'arxiv' OR sr.raw->>'type' = 'preprint'))
           OR p.doi LIKE '%%/preprints.%%'
           OR p.venue ILIKE '%%arxiv%%' OR p.venue ILIKE '%%biorxiv%%'
           OR p.venue ILIKE '%%medrxiv%%' OR p.venue ILIKE '%%preprint%%'
         THEN 0
         WHEN p.venue IS NOT NULL THEN 2
         ELSE 1
       END AS publication_rank
FROM papers p
WHERE p.id = ANY(%(ids)s)
"""


def survivor_of(members: list[dict[str, Any]]) -> dict[str, Any]:
    """DECISION-3b: published beats preprint, then lowest id."""
    return sorted(members, key=lambda m: (-m["publication_rank"], m["id"]))[0]
