"""Unwind merges that a tightened rule would no longer make, and route the
groups to dedup_review instead.

Written for DECISION-3c: the hand-labeled measurement put title_exact at
0.684 precision on groups of 3+ (n=19), so its cap drops from 8 to 2 and
the 122 already-executed groups above that cap have to come back. This is
the first real exercise of the rollback path outside its unit tests, which
is the point — reversibility that has never been used on production data
is a claim, not a property.

Every unwind runs in one transaction per merge: restore the deleted papers
with their original ids, repoint source_records, restore the survivor's
overwritten fields, delete the merges row, then record the group in
dedup_review so it is not silently forgotten.

    python -m bench.dedup_unwind --strategy title_exact --max-size 2 [--execute]
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg

from api.dedup.merge import rollback

SELECT_SQL = """
SELECT id,
       merged_from->'member_ids' AS members,
       jsonb_array_length(merged_from->'deleted_papers') + 1 AS group_size
FROM merges
WHERE strategy = %(strategy)s
  AND merged_from ? 'deleted_papers'
  AND jsonb_array_length(merged_from->'deleted_papers') + 1 > %(max_size)s
ORDER BY id
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--max-size", type=int, required=True)
    ap.add_argument("--execute", action="store_true", help="without this, report only")
    args = ap.parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SET statement_timeout='30min'")
        targets = conn.execute(
            SELECT_SQL, {"strategy": args.strategy, "max_size": args.max_size}
        ).fetchall()
        before = conn.execute(
            "SELECT count(*), count(embedding), (SELECT count(*) FROM merges),"
            " (SELECT count(*) FROM dedup_review) FROM papers"
        ).fetchone()
        assert before is not None

        print(
            f"{len(targets)} {args.strategy} merges above size {args.max_size}"
            f" (restoring {sum(g - 1 for _, _, g in targets)} papers)",
            flush=True,
        )
        if not args.execute:
            print("(report only; pass --execute to unwind)")
            return

        restored, errors = 0, []
        for merge_id, members, group_size in targets:
            try:
                with conn.transaction():
                    result = rollback(conn, merge_id)
                    conn.execute(
                        "INSERT INTO dedup_review (member_ids, size, strategies, note)"
                        " VALUES (%s, %s, %s, %s)",
                        (
                            members,
                            group_size,
                            args.strategy,
                            f"unwound by DECISION-3c: {args.strategy} caps at "
                            f"{args.max_size}; measured 0.684 precision on 3+ groups",
                        ),
                    )
                restored += len(result["restored"])
            except Exception as exc:  # noqa: BLE001 — record and continue
                errors.append(f"merge {merge_id}: {exc}")

        after = conn.execute(
            "SELECT count(*), count(embedding), (SELECT count(*) FROM merges),"
            " (SELECT count(*) FROM dedup_review) FROM papers"
        ).fetchone()
        orphans = conn.execute(
            "SELECT count(*) FROM source_records sr WHERE sr.paper_id IS NOT NULL"
            " AND NOT EXISTS (SELECT 1 FROM papers p WHERE p.id = sr.paper_id)"
        ).fetchone()
        assert after is not None and orphans is not None

    report: dict[str, Any] = {
        "strategy": args.strategy,
        "new_max_size": args.max_size,
        "merges_unwound": len(targets) - len(errors),
        "papers_restored": restored,
        "before": {
            "papers": before[0],
            "embedded": before[1],
            "merges": before[2],
            "review_rows": before[3],
        },
        "after": {
            "papers": after[0],
            "embedded": after[1],
            "merges": after[2],
            "review_rows": after[3],
        },
        "orphaned_source_records": orphans[0],
        "errors": errors[:5],
        "error_count": len(errors),
    }
    (Path(__file__).parent / "results_dedup_unwind.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
