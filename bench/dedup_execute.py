"""Execute the dedup plan. Reversible by construction (api/dedup/merge.py).

Runs the same rules bench/dedup_plan.py measures, then merges every group
of at most its strategy's cap, inside one transaction per group. Groups
above the cap are recorded in dedup_review and left alone.

Strategy order gives attribution; grouping is union-find over all pairs.
jmir_doi is an IDENTITY, not a heuristic: 10.2196/preprints.N and
10.2196/N are the same paper by construction within one publisher's
namespace, which is why it sits beside doi_exact rather than among the
fuzzy passes.
"""

import argparse
import json
import os
from collections import Counter
from pathlib import Path

import psycopg

from api.dedup.cascade import UnionFind
from api.dedup.merge import merge_group
from api.dedup.rules import ABSTRACT_TITLE_SIM, TRGM_THRESHOLD, max_group_size
from bench.dedup_plan import EXACT_PAIRS, build_scratch

# A mechanical publisher transform, verified in this corpus: 524 of 1,013
# JMIR preprint DOIs have their transformed DOI present as a separate paper.
# Checked for other publishers with preprint DOIs (10.20944 Preprints.org,
# 10.7287 PeerJ, 10.3897, 10.24108, 10.1590 SciELO): none derive the
# published DOI from the preprint DOI, so none get a rule.
JMIR_DOI_SQL = """
SELECT p.id, q.id, 1.0
FROM papers p JOIN papers q ON q.doi = replace(p.doi, '/preprints.', '/')
WHERE p.doi LIKE '10.2196/preprints.%' AND p.id <> q.id
"""

ORDER = [
    "doi_exact",
    "jmir_doi",
    "id_exact",
    "abstract_hash",
    "title_exact",
    "title_trgm",
    "preprint_trgm",
]


def collect_pairs(conn: psycopg.Connection) -> list[tuple[int, int, float, str]]:
    sql = dict(EXACT_PAIRS)
    sql["jmir_doi"] = JMIR_DOI_SQL
    sql["abstract_hash"] = (
        f"SELECT a,b,sim FROM dd_abs WHERE sim >= {ABSTRACT_TITLE_SIM} AND NOT enum_sib"
    )
    sql["title_trgm"] = (
        f"SELECT a,b,sim FROM dd_scored WHERE sim>={TRGM_THRESHOLD} AND NOT enum_sib"
    )
    sql["preprint_trgm"] = (
        f"SELECT a,b,sim FROM dd_scored_pp WHERE sim>={TRGM_THRESHOLD} AND NOT enum_sib"
    )
    pairs: list[tuple[int, int, float, str]] = []
    seen: set[tuple[int, int]] = set()
    for name in ORDER:
        for a, b, sim in conn.execute(sql[name]).fetchall():
            pair = (min(int(a), int(b)), max(int(a), int(b)))
            if pair in seen:
                continue
            seen.add(pair)
            pairs.append((pair[0], pair[1], float(sim), name))
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--execute", action="store_true", help="without this, plan only")
    args = ap.parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SET statement_timeout='45min'")
        conn.execute("SET work_mem='256MB'")
        build_scratch(conn, args.rebuild)

        before = conn.execute(
            "SELECT count(*), count(embedding), (SELECT count(*) FROM merges),"
            " (SELECT count(*) FROM source_records) FROM papers"
        ).fetchone()
        assert before is not None

        pairs = collect_pairs(conn)
        per_strategy_pairs = Counter(p[3] for p in pairs)
        uf = UnionFind()
        for a, b, _, _ in pairs:
            uf.union(a, b)
        groups = uf.groups()

        # Attribution: a group's strategy is the earliest strategy in ORDER
        # that contributed any of its edges, and similarity is that edge's.
        best: dict[int, tuple[int, str, float]] = {}
        for a, _b, sim, name in pairs:
            root = uf.find(a)
            rank = ORDER.index(name)
            if root not in best or rank < best[root][0]:
                best[root] = (rank, name, sim)

        # The cap is per strategy (DECISION-3c): title_exact caps at 2 on its
        # measured 0.684 precision for 3+ groups, everything else at 8.
        def cap_for(root: int) -> int:
            return max_group_size(best.get(root, (0, "unknown", None))[1])

        mergeable = {r: m for r, m in groups.items() if len(m) <= cap_for(r)}
        flagged = {r: m for r, m in groups.items() if len(m) > cap_for(r)}

        print(
            f"plan: {len(pairs)} pairs, {len(groups)} groups, "
            f"{len(mergeable)} mergeable, {len(flagged)} flagged",
            flush=True,
        )
        if not args.execute:
            print("(plan only; pass --execute to merge)")
            return

        conn.execute("""
            CREATE TABLE IF NOT EXISTS dedup_review (
                id BIGSERIAL PRIMARY KEY,
                member_ids BIGINT[] NOT NULL,
                size INTEGER NOT NULL,
                strategies TEXT NOT NULL,
                note TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        strat_by_root: dict[int, set[str]] = {}
        for a, _b, _s, name in pairs:
            strat_by_root.setdefault(uf.find(a), set()).add(name)
        for root, members in flagged.items():
            conn.execute(
                "INSERT INTO dedup_review (member_ids, size, strategies, note)"
                " VALUES (%s, %s, %s, %s)",
                (
                    members,
                    len(members),
                    ",".join(sorted(strat_by_root.get(root, set()))),
                    f"over cap {cap_for(root)} for strategy "
                    f"{best.get(root, (0, 'unknown', None))[1]}; "
                    "likely versioned/periodic releases or a generic title",
                ),
            )

        executed: Counter[str] = Counter()
        deleted_total = 0
        text_moved = 0
        errors: list[str] = []
        for i, (root, members) in enumerate(mergeable.items(), start=1):
            _rank, strategy, sim = best.get(root, (0, "unknown", None))  # type: ignore[assignment]
            try:
                with conn.transaction():
                    res = merge_group(conn, members, strategy, sim)
                if res.get("skipped"):
                    continue
                executed[strategy] += 1
                deleted_total += len(res["deleted"])
                text_moved += 1 if res["text_moved"] else 0
            except Exception as exc:  # noqa: BLE001 — record and continue
                errors.append(f"group {members[:4]}...: {exc}")
            if i % 2000 == 0:
                print(f"  merged {i}/{len(mergeable)} groups", flush=True)

        after = conn.execute(
            "SELECT count(*), count(embedding), (SELECT count(*) FROM merges),"
            " (SELECT count(*) FROM source_records) FROM papers"
        ).fetchone()
        orphans = conn.execute(
            "SELECT count(*) FROM source_records sr WHERE sr.paper_id IS NOT NULL"
            " AND NOT EXISTS (SELECT 1 FROM papers p WHERE p.id = sr.paper_id)"
        ).fetchone()
        assert after is not None and orphans is not None

    report = {
        "before": {
            "papers": before[0],
            "embedded": before[1],
            "merges": before[2],
            "source_records": before[3],
        },
        "after": {
            "papers": after[0],
            "embedded": after[1],
            "merges": after[2],
            "source_records": after[3],
        },
        "pairs_per_strategy": dict(per_strategy_pairs),
        "groups_merged_per_strategy": dict(executed),
        "papers_deleted": deleted_total,
        "survivors_with_text_moved": text_moved,
        "groups_flagged": len(flagged),
        "papers_in_flagged": sum(len(m) for m in flagged.values()),
        "orphaned_source_records": orphans[0],
        "errors": errors[:10],
        "error_count": len(errors),
    }
    out = Path(__file__).parent / "results_dedup_executed.json"
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
