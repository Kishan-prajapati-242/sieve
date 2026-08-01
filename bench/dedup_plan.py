"""Dry run of the dedup cascade: measure the plan, execute nothing.

Never writes to papers or merges. Scratch tables are real (not TEMP) so a
re-plan can reuse the expensive trigram scoring across sessions; refresh
them with --rebuild.

Rules applied beyond the naive cascade (api/dedup/rules.py):
  * abstract_hash additionally requires title similarity — same abstract
    with different titles means SIBLINGS under a shared parent, not
    duplicates (textbook chapters, versioned releases, supplementary
    files, proceedings volumes).
  * every fuzzy strategy refuses enumerated siblings: titles differing
    only in their digits.
  * components larger than MAX_GROUP_SIZE are flagged for review, never
    merged.
  * chain detection: union-find assumes transitivity and similarity is not
    transitive, so each component reports edge density and mean pairwise
    title similarity.
"""

import argparse
import json
import os
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg

from api.dedup.cascade import SURVIVOR_SQL, UnionFind
from api.dedup.rules import ABSTRACT_TITLE_SIM, MAX_GROUP_SIZE, TRGM_THRESHOLD, sibling_sql

LOOSEST = 0.85
SWEEP = [0.85, 0.90, 0.92, 0.95, 0.98]
SLACK = f"{(1 - LOOSEST) / LOOSEST:.4f}"

SCRATCH = f"""
CREATE TABLE dd_preprints AS
SELECT p.id FROM papers p
WHERE p.arxiv_id IS NOT NULL OR p.doi LIKE '%/preprints.%'
   OR p.venue ILIKE '%arxiv%' OR p.venue ILIKE '%biorxiv%' OR p.venue ILIKE '%medrxiv%'
   OR p.venue ILIKE '%preprint%' OR p.venue ILIKE '%research square%' OR p.venue ILIKE '%ssrn%'
   OR EXISTS (SELECT 1 FROM source_records sr WHERE sr.paper_id=p.id
              AND (sr.source='arxiv' OR sr.raw->>'type'='preprint'));
CREATE INDEX ON dd_preprints (id);

CREATE TABLE dd_sn AS
SELECT DISTINCT p.id, p.year, p.title_norm, length(p.title_norm) len,
       lower(split_part(a,' ',array_length(string_to_array(a,' '),1))) AS surname
FROM papers p, unnest(p.authors) a
WHERE p.year IS NOT NULL AND length(p.title_norm) >= 20 AND p.authors IS NOT NULL;
CREATE INDEX ON dd_sn (surname, year);

CREATE TABLE dd_sn_pp AS SELECT s.* FROM dd_sn s JOIN dd_preprints p ON p.id = s.id;
CREATE INDEX ON dd_sn_pp (surname, year);

CREATE TABLE dd_scored AS
SELECT DISTINCT ON (a,b) a, b, sim, enum_sib FROM (
  SELECT s1.id a, s2.id b, similarity(s1.title_norm, s2.title_norm) sim,
         {sibling_sql("s1.title_norm", "s2.title_norm")} AS enum_sib
  FROM dd_sn s1 JOIN dd_sn s2
    ON s1.surname = s2.surname AND s1.year = s2.year AND s1.id < s2.id
  WHERE length(s1.surname) >= 3 AND s1.title_norm <> s2.title_norm
    AND abs(s1.len - s2.len) <= ceil(least(s1.len, s2.len) * {SLACK}) + 2
) t WHERE sim >= {LOOSEST};

CREATE TABLE dd_scored_pp AS
SELECT DISTINCT ON (a,b) a, b, sim, enum_sib FROM (
  SELECT least(s1.id,s2.id) a, greatest(s1.id,s2.id) b,
         similarity(s1.title_norm, s2.title_norm) sim,
         {sibling_sql("s1.title_norm", "s2.title_norm")} AS enum_sib
  FROM dd_sn_pp s1 JOIN dd_sn s2 ON s2.surname = s1.surname AND s2.year = s1.year + 1
  WHERE length(s1.surname) >= 3 AND s1.id <> s2.id
    AND abs(s1.len - s2.len) <= ceil(least(s1.len,s2.len) * {SLACK}) + 2
  UNION ALL
  SELECT least(s1.id,s2.id), greatest(s1.id,s2.id),
         similarity(s1.title_norm, s2.title_norm),
         {sibling_sql("s1.title_norm", "s2.title_norm")}
  FROM dd_sn_pp s1 JOIN dd_sn s2 ON s2.surname = s1.surname AND s2.year = s1.year - 1
  WHERE length(s1.surname) >= 3 AND s1.id <> s2.id
    AND abs(s1.len - s2.len) <= ceil(least(s1.len,s2.len) * {SLACK}) + 2
) t WHERE sim >= {LOOSEST};

-- Full pairwise inside each shared-abstract group, scored on TITLE, so the
-- sibling rule can see every pair rather than a spanning tree.
CREATE TABLE dd_abs AS
WITH grp AS (
  SELECT array_agg(p.id ORDER BY p.id) ids
  FROM papers p
  WHERE p.abstract IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM boilerplate_abstracts x WHERE x.abstract_md5=md5(p.abstract))
  GROUP BY md5(p.abstract) HAVING count(*) > 1
), cand AS (
  SELECT g.ids[i] a, g.ids[j] b
  FROM grp g, generate_subscripts(g.ids,1) i, generate_subscripts(g.ids,1) j
  WHERE i < j
)
SELECT c.a, c.b, similarity(pa.title_norm, pb.title_norm) sim,
       {sibling_sql("pa.title_norm", "pb.title_norm")} AS enum_sib
FROM cand c JOIN papers pa ON pa.id=c.a JOIN papers pb ON pb.id=c.b;
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
    "title_exact": """
        SELECT ids[1], unnest(ids[2:]), 1.0 FROM (
          SELECT array_agg(id ORDER BY id) ids FROM papers
          WHERE year IS NOT NULL AND length(title_norm)>=20
          GROUP BY title_norm, year HAVING count(*)>1) g
    """,
}

ORDER = ["doi_exact", "id_exact", "abstract_hash", "title_exact", "title_trgm", "preprint_trgm"]


SCRATCH_TABLES = ("dd_scored", "dd_scored_pp", "dd_abs", "dd_sn_pp", "dd_sn", "dd_preprints")


def build_scratch(conn: psycopg.Connection, rebuild: bool) -> None:
    # Check EVERY table, not just the last one: a killed build leaves some
    # created and some not, and a partial set must be rebuilt, not reused.
    present = [
        t
        for t in SCRATCH_TABLES
        if (row := conn.execute("SELECT to_regclass(%s)", (t,)).fetchone()) and row[0]
    ]
    if len(present) == len(SCRATCH_TABLES) and not rebuild:
        print("reusing scratch tables (--rebuild to refresh)", flush=True)
        return
    for t in SCRATCH_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    print("building scratch tables (several minutes)...", flush=True)
    conn.execute(SCRATCH)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SET statement_timeout='45min'")
        conn.execute("SET work_mem='256MB'")
        build_scratch(conn, args.rebuild)

        sweeps: dict[str, dict[str, dict[str, int]]] = {}
        for table, key in (("dd_scored", "title_trgm"), ("dd_scored_pp", "preprint_trgm")):
            sweeps[key] = {}
            for th in SWEEP:
                row = conn.execute(  # noqa: S608
                    "SELECT count(*) FILTER (WHERE NOT enum_sib),"
                    f" count(*) FILTER (WHERE enum_sib) FROM {table} WHERE sim >= %s",
                    (th,),
                ).fetchone()
                assert row is not None
                sweeps[key][str(th)] = {"kept": int(row[0]), "enum_refused": int(row[1])}

        band = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE enum_sib)"
            " FROM dd_scored WHERE sim >= 0.95 AND sim < 0.98"
        ).fetchone()
        abs_before = conn.execute("SELECT count(*) FROM dd_abs").fetchone()
        abs_after = conn.execute(
            "SELECT count(*) FROM dd_abs WHERE sim >= %s AND NOT enum_sib", (ABSTRACT_TITLE_SIM,)
        ).fetchone()
        assert band is not None and abs_before is not None and abs_after is not None

        strategy_sql = dict(EXACT_PAIRS)
        strategy_sql["abstract_hash"] = (
            f"SELECT a,b,sim FROM dd_abs WHERE sim >= {ABSTRACT_TITLE_SIM} AND NOT enum_sib"
        )
        strategy_sql["title_trgm"] = (
            f"SELECT a,b,sim FROM dd_scored WHERE sim>={TRGM_THRESHOLD} AND NOT enum_sib"
        )
        strategy_sql["preprint_trgm"] = (
            f"SELECT a,b,sim FROM dd_scored_pp WHERE sim>={TRGM_THRESHOLD} AND NOT enum_sib"
        )

        pairs: list[tuple[int, int, float, str]] = []
        seen: set[tuple[int, int]] = set()
        per_strategy: Counter[str] = Counter()
        for name in ORDER:
            for a, b, sim in conn.execute(strategy_sql[name]).fetchall():
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

        edges_by_root: Counter[int] = Counter()
        strat_by_root: dict[int, set[str]] = {}
        for a, _b, _s, name in pairs:
            edges_by_root[uf.find(a)] += 1
            strat_by_root.setdefault(uf.find(a), set()).add(name)

        chain_report: list[dict[str, Any]] = []
        for root, members in groups.items():
            n = len(members)
            if n < 4:
                continue
            possible = n * (n - 1) // 2
            sims = conn.execute(
                "SELECT similarity(pa.title_norm, pb.title_norm) FROM papers pa, papers pb"
                " WHERE pa.id = ANY(%(ids)s) AND pb.id = ANY(%(ids)s) AND pa.id < pb.id",
                {"ids": members},
            ).fetchall()
            vals = [float(s[0]) for s in sims]
            chain_report.append(
                {
                    "size": n,
                    "edges_found": edges_by_root[root],
                    "edge_density": round(edges_by_root[root] / possible, 3),
                    "mean_pairwise_sim": round(statistics.mean(vals), 3) if vals else 1.0,
                    "min_pairwise_sim": round(min(vals), 3) if vals else 1.0,
                    "strategies": sorted(strat_by_root.get(root, set())),
                }
            )

        oversized = {r: m for r, m in groups.items() if len(m) > MAX_GROUP_SIZE}
        merged = {r: m for r, m in groups.items() if len(m) <= MAX_GROUP_SIZE}
        sizes = Counter(len(m) for m in merged.values())

        flagged: list[dict[str, Any]] = []
        for root, members in sorted(oversized.items(), key=lambda kv: -len(kv[1]))[:12]:
            rows = conn.execute(SURVIVOR_SQL, {"ids": members}).fetchall()
            titles = Counter((r[1] or "")[:58] for r in rows)
            flagged.append(
                {
                    "size": len(members),
                    "distinct_titles": len(titles),
                    "strategies": sorted(strat_by_root.get(root, set())),
                    "top_titles": titles.most_common(3),
                }
            )

    chains = [c for c in chain_report if c["mean_pairwise_sim"] < TRGM_THRESHOLD]
    report = {
        "rules": {
            "abstract_title_sim": ABSTRACT_TITLE_SIM,
            "trgm_threshold": TRGM_THRESHOLD,
            "max_group_size": MAX_GROUP_SIZE,
        },
        "threshold_sweeps_post_fix": sweeps,
        "band_0.95_to_0.98": {"total": band[0], "enumerated_siblings": band[1]},
        "abstract_hash": {
            "pairs_before_sibling_rule": abs_before[0],
            "pairs_after_sibling_rule": abs_after[0],
        },
        "pairs_per_strategy": dict(per_strategy),
        "total_pairs": len(pairs),
        "groups_total": len(groups),
        "groups_merged": len(merged),
        "groups_flagged_for_review": len(oversized),
        "papers_in_flagged_groups": sum(len(m) for m in oversized.values()),
        "rows_merged_away": sum(len(m) for m in merged.values()) - len(merged),
        "size_distribution_merged": dict(sorted(sizes.items())),
        "components_4plus": len(chain_report),
        "components_that_are_chains": len(chains),
        "chain_examples": sorted(chains, key=lambda c: c["mean_pairwise_sim"])[:8],
        "flagged_examples": flagged,
    }
    out = Path(__file__).parent / "results_dedup_plan.json"
    out.write_text(json.dumps(report, indent=2))
    slim = {k: v for k, v in report.items() if k not in ("chain_examples", "flagged_examples")}
    print(json.dumps(slim, indent=2))


if __name__ == "__main__":
    main()
