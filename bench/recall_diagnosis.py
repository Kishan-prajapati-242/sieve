"""Why recall fell at low ef: graph rebuild, or a changed query population?

The ef ladder re-measured on 2026-08-12 showed recall@200 at ef=200 falling
0.9431 -> 0.8918 and recall@10 at ef=40 falling 0.9394 -> 0.8825, while
everything at ef>=400 was unmoved. Two hypotheses fit both columns:

  H1  VACUUM FULL rebuilt the HNSW graph on a 7% smaller corpus. Low ef is
      sensitive to entry point and local connectivity; high ef is robust to
      graph shape. Predicts a roughly UNIFORM drop across queries.

  H2  500 of the 520 queries are corpus titles. Where a query's own source
      paper was merged away by dedup, its known-item target is gone (or
      carries a survivor-rewritten abstract), so the query silently became
      topical — harder, and hardest at low ef. Predicts the drop
      CONCENTRATES in the affected queries, and means the before/after
      columns describe different query populations.

The split: a known-item query's own paper is rank 1 of the OLD ground
truth, so `old.top200[0].id` identifies it. Whether that id still exists in
papers is the group label. Recall is then recomputed per group, from the
CURRENT ground truth, at the ef values where the drop appears.

Also emitted here: recall@20, which the exchange-rate table needs. A
speedup measured on search_vector(k=20) has to be quoted against recall at
k=20, not against recall@200 — 200 is the hybrid's candidate depth, and
ef=40 with depth=200 is a configuration the truncation guard forbids.
"""

import json
import math
import os
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from bench.harness import db_state, load_ground_truth, method_record
from bench.hnsw_recall_sweep import EXACT_SQL, tie_aware_recall, vector_literal

DEPTH = 200
EF_VALUES = (40, 200, 600)
KS = (10, 20, 200)
OLD_GT = "exact_top200_wide.superseded_196893.json"


def stat(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 4),
        "se": round(statistics.stdev(vals) / math.sqrt(len(vals)), 4) if len(vals) > 1 else None,
    }


def main() -> None:
    out_dir = Path(__file__).parent
    labels, gt_method = load_ground_truth(out_dir / "labels" / "exact_top200_wide.json")
    old_labels, old_method = load_ground_truth(out_dir / "labels" / OLD_GT)

    # Map query text -> the id that was its own top hit in the OLD ground truth.
    old_self = {}
    for entry in old_labels:
        top = entry.get("top200") or entry.get("top50") or []
        if top:
            old_self[entry["query"]] = top[0]["id"]

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SET statement_timeout='30min'")
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm")
        conn.execute("SELECT pg_prewarm('papers_embed_idx', 'read'), pg_prewarm('papers', 'read')")
        state = db_state(conn)

        ids = [i for i in old_self.values()]
        alive = {
            r[0]
            for r in conn.execute("SELECT id FROM papers WHERE id = ANY(%s)", (ids,)).fetchall()
        }
        groups = {
            e["query"]: (
                "unknown"
                if e["query"] not in old_self
                else ("survived" if old_self[e["query"]] in alive else "deleted")
            )
            for e in labels
        }

        by_ef: dict[str, Any] = {}
        for ef in EF_VALUES:
            per_query: dict[str, dict[int, float]] = {}
            with conn.transaction():
                conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(ef),))
                conn.execute("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)")
                for entry in labels:
                    got = conn.execute(
                        EXACT_SQL, {"q": vector_literal(entry["embedding"]), "k": DEPTH}
                    ).fetchall()
                    per_query[entry["query"]] = {
                        k: tie_aware_recall(entry["top200"], got, k) for k in KS
                    }
            block: dict[str, Any] = {}
            for group in ("survived", "deleted", "unknown", "all"):
                members = [q for q in per_query if group == "all" or groups.get(q) == group]
                block[group] = {
                    f"recall_at_{k}": stat([per_query[q][k] for q in members]) for k in KS
                }
            by_ef[str(ef)] = block

    report = {
        "measured_at": datetime.now(UTC).isoformat(),
        "method": method_record(
            timing_window="n/a — recall, not latency",
            db_state=state,
            ground_truth_corpus=gt_method.get("corpus_papers", gt_method.get("corpus_size")),
            old_ground_truth=OLD_GT,
            old_ground_truth_method=old_method,
            grouping="a query is 'deleted' when the paper that was rank 1 of its OLD "
            "ground-truth list no longer exists in papers; 'survived' otherwise",
            depth=DEPTH,
            tie_handling="hit if id in exact top-k OR distance <= boundary + 1e-9",
        ),
        "group_sizes": {
            g: sum(1 for v in groups.values() if v == g) for g in ("survived", "deleted", "unknown")
        },
        "by_ef": by_ef,
    }
    (out_dir / "results_recall_diagnosis.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"group_sizes": report["group_sizes"]}, indent=2))
    for ef_label, block in by_ef.items():
        print(f"\n--- ef={ef_label}")
        for group in ("all", "survived", "deleted"):
            row = block[group]
            n = row["recall_at_10"]["n"]
            print(
                f"  {group:9s} n={n:4d}  "
                + "  ".join(
                    f"r@{k}={row[f'recall_at_{k}']['mean']:.4f}"
                    if row[f"recall_at_{k}"].get("mean") is not None
                    else f"r@{k}=n/a"
                    for k in KS
                )
            )


if __name__ == "__main__":
    main()
