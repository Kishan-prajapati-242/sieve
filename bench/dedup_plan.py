"""Dry run of the dedup cascade: measure the plan, execute nothing.

Kishan, 2026-07-31: "Before executing a single merge, measure and report."
This script never writes to papers or merges.

Blocking, not self-joining (docs/findings.md): exact-key strategies are
GROUP BY + star fan-out; the fuzzy strategies block on (author surname,
year) and only score titles inside a block. The shared-surname condition
is therefore the blocking KEY rather than a post-filter, which is what
makes the trigram step finish at all.

Two fuzzy passes, reported separately because they find different things:
  title_trgm    same year. Catches formatting/encoding twins.
  preprint_trgm preprint subset x anything, +/-1 year. Catches the arXiv-
                2024 / proceedings-2025 pair that a same-year block can
                never see — the exact class DECISION-3b was written for.

The length prefilter is PROPORTIONAL: for similarity >= t with one trigram
set contained in the other, |len_a - len_b| <= len_short * (1-t)/t. It is
derived from the LOOSEST threshold in the sweep so no point on the curve
is truncated; a constant 15 chars silently dropped 874 valid pairs at 0.85
(15.6% of that population) before this was fixed.
"""

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg

from api.dedup.cascade import SURVIVOR_SQL, UnionFind, survivor_of

LOOSEST = 0.85
OPERATING_POINT = float(os.environ.get("TRGM_THRESHOLD", "0.92"))
SWEEP = [0.85, 0.90, 0.92, 0.95, 0.98]

SETUP = f"""
SET statement_timeout='45min';
SET work_mem='256MB';

CREATE TEMP TABLE preprints AS
SELECT p.id FROM papers p
WHERE p.arxiv_id IS NOT NULL OR p.doi LIKE '%/preprints.%'
   OR p.venue ILIKE '%arxiv%' OR p.venue ILIKE '%biorxiv%' OR p.venue ILIKE '%medrxiv%'
   OR p.venue ILIKE '%preprint%' OR p.venue ILIKE '%research square%' OR p.venue ILIKE '%ssrn%'
   OR EXISTS (SELECT 1 FROM source_records sr WHERE sr.paper_id=p.id
              AND (sr.source='arxiv' OR sr.raw->>'type'='preprint'));
CREATE INDEX ON preprints (id);

CREATE TEMP TABLE sn AS
SELECT DISTINCT p.id, p.year, p.title_norm, length(p.title_norm) len,
       lower(split_part(a,' ',array_length(string_to_array(a,' '),1))) AS surname
FROM papers p, unnest(p.authors) a
WHERE p.year IS NOT NULL AND length(p.title_norm) >= 20 AND p.authors IS NOT NULL;
CREATE INDEX ON sn (surname, year);

CREATE TEMP TABLE sn_pp AS SELECT s.* FROM sn s JOIN preprints p ON p.id = s.id;
CREATE INDEX ON sn_pp (surname, year);

CREATE TEMP TABLE scored AS
SELECT DISTINCT ON (a,b) a, b, sim FROM (
  SELECT s1.id a, s2.id b, similarity(s1.title_norm, s2.title_norm) sim
  FROM sn s1 JOIN sn s2 ON s1.surname = s2.surname AND s1.year = s2.year AND s1.id < s2.id
  WHERE length(s1.surname) >= 3 AND s1.title_norm <> s2.title_norm
    AND abs(s1.len - s2.len) <= ceil(least(s1.len, s2.len) * {(1 - LOOSEST) / LOOSEST:.4f}) + 2
) t WHERE sim >= {LOOSEST};

CREATE TEMP TABLE scored_pp AS
SELECT DISTINCT ON (a,b) a, b, sim FROM (
  SELECT least(s1.id,s2.id) a, greatest(s1.id,s2.id) b,
         similarity(s1.title_norm, s2.title_norm) sim
  FROM sn_pp s1 JOIN sn s2 ON s2.surname = s1.surname AND s2.year = s1.year + 1
  WHERE length(s1.surname) >= 3 AND s1.id <> s2.id
    AND abs(s1.len - s2.len) <= ceil(least(s1.len,s2.len) * {(1 - LOOSEST) / LOOSEST:.4f}) + 2
  UNION ALL
  SELECT least(s1.id,s2.id), greatest(s1.id,s2.id),
         similarity(s1.title_norm, s2.title_norm)
  FROM sn_pp s1 JOIN sn s2 ON s2.surname = s1.surname AND s2.year = s1.year - 1
  WHERE length(s1.surname) >= 3 AND s1.id <> s2.id
    AND abs(s1.len - s2.len) <= ceil(least(s1.len,s2.len) * {(1 - LOOSEST) / LOOSEST:.4f}) + 2
) t WHERE sim >= {LOOSEST};
"""

EXACT_PAIRS = {
    "doi_exact": """
        SELECT ids[1], unnest(ids[2:]), 1.0 FROM (
          SELECT array_agg(id ORDER BY id) ids FROM papers
          WHERE doi IS NOT NULL GROUP BY doi HAVING count(*)>1) g
    """,
    "id_exact": """
        SELECT ids[1], unnest(ids[2:]), 1.0 FROM (
          SELECT array_agg(id ORDER BY id) ids FROM papers
          WHERE arxiv_id IS NOT NULL GROUP BY arxiv_id HAVING count(*)>1
          UNION ALL
          SELECT array_agg(id ORDER BY id) FROM papers
          WHERE pubmed_id IS NOT NULL GROUP BY pubmed_id HAVING count(*)>1) g
    """,
    "abstract_hash": """
        SELECT ids[1], unnest(ids[2:]), 1.0 FROM (
          SELECT array_agg(p.id ORDER BY p.id) ids FROM papers p
          WHERE p.abstract IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM boilerplate_abstracts x WHERE x.abstract_md5=md5(p.abstract))
          GROUP BY md5(p.abstract) HAVING count(*)>1) g
    """,
    "title_exact": """
        SELECT ids[1], unnest(ids[2:]), 1.0 FROM (
          SELECT array_agg(id ORDER BY id) ids FROM papers
          WHERE year IS NOT NULL AND length(title_norm)>=20
          GROUP BY title_norm, year HAVING count(*)>1) g
    """,
}

REACH_SQL = """
SELECT count(*) FILTER (WHERE authors IS NULL OR cardinality(authors)=0) AS no_authors,
       count(*) FILTER (WHERE doi IS NULL) AS no_doi,
       count(*) FILTER (WHERE abstract IS NULL OR EXISTS (
           SELECT 1 FROM boilerplate_abstracts b WHERE b.abstract_md5=md5(abstract)
       )) AS no_usable_abstract,
       count(*) FILTER (
         WHERE doi IS NULL AND arxiv_id IS NULL AND pubmed_id IS NULL
           AND (authors IS NULL OR cardinality(authors)=0)
           AND (abstract IS NULL OR EXISTS (
                SELECT 1 FROM boilerplate_abstracts b WHERE b.abstract_md5=md5(abstract)))
       ) AS unreachable_by_every_strategy,
       count(*) AS total
FROM papers
"""


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(SETUP)
        reach = conn.execute(REACH_SQL).fetchone()
        assert reach is not None
        pre = conn.execute("SELECT count(*) FROM preprints").fetchone()
        assert pre is not None

        sweeps: dict[str, dict[str, int]] = {"title_trgm": {}, "preprint_trgm": {}}
        for table, key in (("scored", "title_trgm"), ("scored_pp", "preprint_trgm")):
            for th in SWEEP:
                row = conn.execute(
                    f"SELECT count(*) FROM {table} WHERE sim >= %s",
                    (th,),  # noqa: S608
                ).fetchone()
                sweeps[key][str(th)] = int(row[0]) if row else 0

        pairs: list[tuple[int, int, float, str]] = []
        seen: set[tuple[int, int]] = set()
        per_strategy: Counter[str] = Counter()
        strategy_sql = dict(EXACT_PAIRS)
        strategy_sql["title_trgm"] = f"SELECT a,b,sim FROM scored WHERE sim>={OPERATING_POINT}"
        strategy_sql["preprint_trgm"] = (
            f"SELECT a,b,sim FROM scored_pp WHERE sim>={OPERATING_POINT}"
        )
        for name, sql in strategy_sql.items():
            for a, b, sim in conn.execute(sql).fetchall():
                pair = (min(int(a), int(b)), max(int(a), int(b)))
                if pair in seen:
                    continue
                seen.add(pair)
                pairs.append((pair[0], pair[1], float(sim), name))
                per_strategy[name] += 1

        uf = UnionFind()
        for a, b, _, _ in pairs:
            uf.union(a, b)
        groups = uf.groups()
        sizes = Counter(len(m) for m in groups.values())

        # Transitivity: groups whose edges came from more than one strategy,
        # and groups larger than a single pair.
        root_strategies: dict[int, set[str]] = {}
        for a, _b, _s, name in pairs:
            root_strategies.setdefault(uf.find(a), set()).add(name)
        multi = sum(1 for s in root_strategies.values() if len(s) > 1)

        largest: list[dict[str, Any]] = []
        for members in sorted(groups.values(), key=len, reverse=True)[:20]:
            rows = conn.execute(SURVIVOR_SQL, {"ids": members}).fetchall()
            info = [
                {
                    "id": r[0],
                    "title": r[1],
                    "year": r[2],
                    "venue": r[3],
                    "citations": r[4],
                    "publication_rank": r[8],
                }
                for r in rows
            ]
            largest.append(
                {
                    "size": len(members),
                    "strategies": sorted(root_strategies.get(uf.find(members[0]), set())),
                    "survivor_id": survivor_of(info)["id"],
                    "members": info,
                }
            )

    report = {
        "operating_point_trgm": OPERATING_POINT,
        "preprint_subset": pre[0],
        "reachability": {
            "no_authors": reach[0],
            "no_doi": reach[1],
            "no_usable_abstract": reach[2],
            "unreachable_by_every_strategy": reach[3],
            "total": reach[4],
        },
        "threshold_sweeps": sweeps,
        "pairs_per_strategy": dict(per_strategy),
        "total_pairs": len(pairs),
        "groups": len(groups),
        "papers_in_groups": sum(len(m) for m in groups.values()),
        "rows_merged_away": sum(len(m) for m in groups.values()) - len(groups),
        "size_distribution": dict(sorted(sizes.items())),
        "groups_from_multiple_strategies": multi,
        "largest_20": largest,
    }
    out = Path(__file__).parent / "results_dedup_plan.json"
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "largest_20"}, indent=2))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
