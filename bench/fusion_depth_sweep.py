"""Joint candidate-depth / ef_search sweep for mode=hybrid — the coupled
decision (docs/progress.md 2026-07-31): ef auto-raises to >= depth, so the
sweep varies N and lets ef follow production logic, max(40, N).

What is measured at each N, and what deliberately is not:

  vector recall@N — the vector CTE's top-N against the exact top-200
  denominator (bench/labels/exact_top200_wide.json), tie-aware. This is
  the input-quality number fusion actually depends on, NOT recall@10.
  At N=500 the denominator is capped: recall is over the first 200.

  hybrid convergence — fused QUALITY is not measurable before Phase 4
  relevance labels, and this script does not pretend otherwise. What it
  measures is a stability proxy: mean overlap and identical-order rate of
  hybrid's top-20 at depth N against depth 500 (the deepest tested). A
  converged ranking can still be a converged-to-worse ranking; the proxy
  says only "past this N, deeper candidates stop changing the answer."

  latency — search_hybrid() as the API calls it (SET LOCALs + one fused
  statement returning k=20 full rows), pg_prewarm protocol, 1 discarded
  warmup run + 3 measured, across_runs gate. e2e is reported twice: with
  embed included (today's request path) and with embed at zero (Phase 4
  query-cache hit), because that changes which N is affordable.

Inherits the known-item caveat: 500/520 queries are corpus titles.
"""

import json
import math
import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from api.embed.onnx_encoder import OnnxEncoder
from api.embed.texts import query_text
from api.search.fusion import search_hybrid
from bench.harness import across_runs, load_ground_truth, method_record, summarize
from bench.hnsw_recall_sweep import EXACT_SQL, tie_aware_recall, vector_literal

DEPTHS = [20, 50, 100, 200, 500]
REFERENCE_DEPTH = 500
K = 20
DEFAULT_EF = 40  # production request default; effective ef = max(40, N)
N_RUNS = 3
WARMUP_RUNS = 1


def hybrid_ids_and_ms(
    conn: psycopg.Connection, query: str, vec: list[float], depth: int
) -> tuple[list[int], float]:
    start = time.perf_counter()
    rows = search_hybrid(
        conn,
        query=query,
        query_vec=vec,
        k=K,
        depth=depth,
        ef_search=max(DEFAULT_EF, depth),
    )
    ms = (time.perf_counter() - start) * 1000
    return [r["id"] for r in rows], ms


def main() -> None:
    out_dir = Path(__file__).parent
    labels, _gt_method = load_ground_truth(out_dir / "labels" / "exact_top200_wide.json")
    encoder = OnnxEncoder(os.environ["EMBED_MODEL_DIR"])

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm")
        conn.execute("SELECT pg_prewarm('papers_embed_idx', 'read'), pg_prewarm('papers', 'read')")

        for _ in range(3):
            encoder.encode([query_text("warmup")])
        embed_ms = []
        for entry in labels:
            t0 = time.perf_counter()
            encoder.encode([query_text(entry["query"])])
            embed_ms.append((time.perf_counter() - t0) * 1000)

        # Reference fused rankings at the deepest depth (also serves as the
        # hybrid warmup pass for the whole session).
        reference: dict[str, list[int]] = {}
        for entry in labels:
            ids, _ = hybrid_ids_and_ms(conn, entry["query"], entry["embedding"], REFERENCE_DEPTH)
            reference[entry["query"]] = ids

        points: list[dict[str, Any]] = []
        for depth in DEPTHS:
            # Vector CTE input quality: top-N vs the exact denominator.
            eff_n = min(depth, 200)
            recalls = []
            with conn.transaction():
                conn.execute(
                    "SELECT set_config('hnsw.ef_search', %s, true)", (str(max(DEFAULT_EF, depth)),)
                )
                conn.execute("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)")
                for entry in labels:
                    got = conn.execute(
                        EXACT_SQL, {"q": vector_literal(entry["embedding"]), "k": eff_n}
                    ).fetchall()
                    recalls.append(tie_aware_recall(entry["top200"], got, eff_n))

            # Hybrid latency (warmup run discarded) + convergence from the
            # last measured run's rankings (deterministic given the graph).
            sql_runs: list[list[float]] = []
            rankings: dict[str, list[int]] = {}
            for run_index in range(WARMUP_RUNS + N_RUNS):
                run_ms: list[float] = []
                for entry in labels:
                    ids, ms = hybrid_ids_and_ms(conn, entry["query"], entry["embedding"], depth)
                    run_ms.append(ms)
                    rankings[entry["query"]] = ids
                if run_index >= WARMUP_RUNS:
                    sql_runs.append(run_ms)

            overlaps = []
            identical = 0
            for entry in labels:
                ref = reference[entry["query"]]
                fused = rankings[entry["query"]]
                overlaps.append(len(set(fused) & set(ref)) / max(len(ref), 1) if ref else 1.0)
                identical += fused == ref
            e2e_runs = [[a + b for a, b in zip(embed_ms, run, strict=True)] for run in sql_runs]

            sql = across_runs(sql_runs)
            points.append(
                {
                    "depth": depth,
                    "ef_search_effective": max(DEFAULT_EF, depth),
                    "vector_recall_at_n": round(statistics.mean(recalls), 4),
                    "vector_recall_n_capped_at": eff_n,
                    "vector_recall_se": round(
                        statistics.stdev(recalls) / math.sqrt(len(recalls)), 4
                    ),
                    "hybrid_top20_overlap_vs_max_depth": round(statistics.mean(overlaps), 4),
                    "hybrid_top20_identical_rate": round(identical / len(labels), 4),
                    "sql": sql,
                    "e2e_with_embed": across_runs(e2e_runs),
                    "e2e_embed_cached_p50_ms": sql["p50_ms"],  # embed=0: sql IS the e2e
                    "single_run_sql_full": summarize(sql_runs[-1]),
                }
            )
            print(json.dumps(points[-1]), flush=True)

    results = {
        "measured_at": datetime.now(UTC).isoformat(),
        "method": method_record(
            timing_window="sql: search_hybrid() round trip as the API calls it "
            "(SET LOCALs + one fused statement, k=20 full rows). embed: single-"
            "text encode, sampled once per query, reused across depths. "
            "e2e_with_embed: per-sample sum. e2e_embed_cached: sql alone "
            "(models a Phase 4 query-cache hit, embed=0).",
            protocol=f"pg_prewarm('read') index+heap; 520 distinct queries; per depth: "
            f"{WARMUP_RUNS} warmup + {N_RUNS} measured runs, across_runs gate. "
            f"ef_search = max({DEFAULT_EF}, depth) — production auto-raise logic.",
            convergence_proxy="hybrid QUALITY is not measurable before Phase 4 "
            "labels. overlap/identical-rate of top-20 vs depth=500 measures "
            "stability only: past the convergence depth, deeper candidates stop "
            "changing the answer. A converged ranking can still be worse.",
            known_item_caveat="500/520 queries are corpus titles — inherited from "
            "the recall sweep; see results_hnsw_recall_sweep.json caveats",
            denominator="bench/labels/exact_top200_wide.json (forced seq scan); "
            "recall at N=500 is capped to the top-200 denominator",
            k=K,
        ),
        "points": points,
    }
    (out_dir / "results_fusion_depth_sweep.json").write_text(json.dumps(results, indent=2))
    print("written: bench/results_fusion_depth_sweep.json")


if __name__ == "__main__":
    main()
