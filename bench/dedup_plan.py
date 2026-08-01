"""Dry run of the dedup cascade: measure the plan, execute nothing.

Kishan, 2026-07-31: "Before executing a single merge, measure and report."
This script never writes — no merges rows, no paper deletions. It reports
per-strategy pair counts, the group-size distribution, the 20 largest
groups with titles (generic-title collisions are the title cascade's
version of the boilerplate problem), and how much of the grouping comes
from transitive chains that no single strategy saw.
"""

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg

from api.dedup.cascade import (
    STRATEGY_SQL,
    SURVIVOR_SQL,
    TRGM_THRESHOLD,
    UnionFind,
    find_pairs,
    survivor_of,
)


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SET statement_timeout = '20min'")
        per_strategy_raw: dict[str, int] = {}
        for name in STRATEGY_SQL:
            row = conn.execute(
                f"SELECT count(*) FROM ({STRATEGY_SQL[name]}) s",  # noqa: S608
                {"threshold": TRGM_THRESHOLD},
            ).fetchone()
            per_strategy_raw[name] = int(row[0]) if row else 0
            print(f"  {name}: {per_strategy_raw[name]} pairs", flush=True)

        pairs = find_pairs(conn)
        attributed = Counter(p.strategy for p in pairs)

        uf = UnionFind()
        for p in pairs:
            uf.union(p.a, p.b)
        groups = uf.groups()

        sizes = Counter(len(m) for m in groups.values())
        members_total = sum(len(m) for m in groups.values())

        # Transitivity: a group is "chained" when it has more members than
        # any single strategy's pairs alone would connect.
        chained = 0
        by_strategy_edges: dict[int, set[str]] = {}
        for p in pairs:
            for root in (uf.find(p.a),):
                by_strategy_edges.setdefault(root, set()).add(p.strategy)
        multi_strategy_groups = sum(1 for s in by_strategy_edges.values() if len(s) > 1)
        for members in groups.values():
            if len(members) > 2:
                chained += 1

        largest = sorted(groups.values(), key=len, reverse=True)[:20]
        detail: list[dict[str, Any]] = []
        for members in largest:
            rows = conn.execute(SURVIVOR_SQL, {"ids": members}).fetchall()
            info = [
                {
                    "id": r[0],
                    "title": r[1],
                    "year": r[2],
                    "venue": r[3],
                    "citations": r[4],
                    "doi": r[5],
                    "publication_rank": r[8],
                }
                for r in rows
            ]
            strategies = sorted(
                {p.strategy for p in pairs if p.a in set(members) or p.b in set(members)}
            )
            detail.append(
                {
                    "size": len(members),
                    "strategies": strategies,
                    "survivor_id": survivor_of(info)["id"],
                    "members": info,
                }
            )

    report = {
        "pairs_per_strategy_raw": per_strategy_raw,
        "pairs_attributed_first_strategy_wins": dict(attributed),
        "total_pairs": len(pairs),
        "groups": len(groups),
        "papers_in_groups": members_total,
        "rows_merged_away": members_total - len(groups),
        "size_distribution": dict(sorted(sizes.items())),
        "groups_larger_than_two": chained,
        "groups_needing_multiple_strategies": multi_strategy_groups,
        "trgm_threshold": TRGM_THRESHOLD,
        "largest_20": detail,
    }
    out = Path(__file__).parent / "results_dedup_plan.json"
    out.write_text(json.dumps(report, indent=2))
    summary = {k: v for k, v in report.items() if k != "largest_20"}
    print(json.dumps(summary, indent=2))
    print(f"\nwritten: {out} (largest_20 with titles inside)")


if __name__ == "__main__":
    main()
