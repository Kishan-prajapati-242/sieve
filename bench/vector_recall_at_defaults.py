"""Vector recall@200 at the SHIPPED defaults, against current ground truth.

Narrow on purpose: the ef sweep (bench/ef_at_fixed_depth.py) answers "what
would other settings buy"; this answers "what does the system actually
deliver as configured" — hybrid depth 200, ef_search 600 (DECISION-2e),
iterative_scan strict_order, against a ground truth rebuilt for THIS
corpus.

Tie-aware, same rule as the sweep: a retrieved id counts as a hit if it is
in the exact top-k or its distance is within 1e-9 of the k-th exact
distance, because halfvec's fp16 quantization makes exact ties common and
set intersection alone would punish the index for returning an
equally-near twin.
"""

import json
import math
import os
import statistics
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from api.dedup.rules import TRGM_THRESHOLD  # noqa: F401  (kept: shared config module)
from api.search.fusion import HYBRID_DEFAULT_EF_SEARCH
from bench.harness import load_ground_truth, method_record
from bench.hnsw_recall_sweep import EXACT_SQL, tie_aware_recall, vector_literal

DEPTH = 200


def main() -> None:
    out_dir = Path(__file__).parent
    labels, gt_method = load_ground_truth(out_dir / "labels" / "exact_top200_wide.json")

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SET statement_timeout='30min'")
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm")
        conn.execute("SELECT pg_prewarm('papers_embed_idx', 'read'), pg_prewarm('papers', 'read')")
        corpus = conn.execute("SELECT count(*) FROM papers").fetchone()
        assert corpus is not None

        r200, r10 = [], []
        with conn.transaction():
            conn.execute(
                "SELECT set_config('hnsw.ef_search', %s, true)",
                (str(HYBRID_DEFAULT_EF_SEARCH),),
            )
            conn.execute("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)")
            plan = "\n".join(
                r[0]
                for r in conn.execute(
                    f"EXPLAIN {EXACT_SQL}",  # noqa: S608
                    {"q": vector_literal(labels[0]["embedding"]), "k": DEPTH},
                )
            )
            assert "papers_embed_idx" in plan, plan
            for entry in labels:
                got = conn.execute(
                    EXACT_SQL, {"q": vector_literal(entry["embedding"]), "k": DEPTH}
                ).fetchall()
                r200.append(tie_aware_recall(entry["top200"], got, DEPTH))
                r10.append(tie_aware_recall(entry["top200"], got, 10))

    def stat(vals: list[float]) -> dict[str, float]:
        return {
            "mean": round(statistics.mean(vals), 4),
            "se": round(statistics.stdev(vals) / math.sqrt(len(vals)), 4),
        }

    report = {
        "measured_at": datetime.now(UTC).isoformat(),
        "method": method_record(
            timing_window="n/a — recall, not latency",
            corpus_papers=corpus[0],
            settings={
                "depth": DEPTH,
                "ef_search": HYBRID_DEFAULT_EF_SEARCH,
                "iterative_scan": "strict_order",
            },
            ground_truth_corpus=gt_method.get("corpus_papers", "unknown"),
            plan="Index Scan using papers_embed_idx (natural planner choice, not forced)",
            tie_handling="hit if id in exact top-k OR distance <= boundary + 1e-9",
            known_item_caveat="500/520 queries are corpus titles",
        ),
        "recall_at_200": stat(r200),
        "recall_at_10": stat(r10),
        "n_queries": len(labels),
    }
    (out_dir / "results_vector_recall_defaults.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "method"}, indent=2))


if __name__ == "__main__":
    main()
