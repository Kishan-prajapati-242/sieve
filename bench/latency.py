"""Per-mode latency percentiles — the Phase 2 acceptance number ("you have
latency percentiles for each mode") and the Phase 4 comparison baseline.

All three modes measured through the same functions the API calls, same
protocol (pg_prewarm, 520 distinct queries, 1 discarded warmup run + 3
measured, across_runs gate), same session, so the numbers are comparable:

  bm25   — search_bm25(): GIN-filtered ts_rank_cd, k=20 full rows.
  vector — search_vector(): HNSW, ef_search=40 (held default), k=20.
  hybrid — search_hybrid(): fused statement, depth=200/ef=600
           (DECISION-2e production defaults).

e2e adds the per-sample embed cost for vector/hybrid (bm25 embeds
nothing); the embed-cached scenario is the SQL window alone. p99 gates to
a range per the harness rule; inherits the known-item caveat.
"""

import json
import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from api.embed.onnx_encoder import OnnxEncoder
from api.embed.texts import query_text
from api.search.bm25 import search_bm25
from api.search.fusion import search_hybrid
from api.search.vector import search_vector
from bench.harness import (
    across_runs,
    carry_superseded,
    db_state,
    load_ground_truth,
    method_record,
    state_key,
)

K = 20
VECTOR_EF = 40
HYBRID_DEPTH = 200
HYBRID_EF = 600  # DECISION-2e defaults
N_RUNS = 3
WARMUP_RUNS = 1


def main() -> None:
    out_dir = Path(__file__).parent
    labels, _gt_method = load_ground_truth(out_dir / "labels" / "exact_top200_wide.json")
    encoder = OnnxEncoder(os.environ["EMBED_MODEL_DIR"])

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm")
        conn.execute("SELECT pg_prewarm('papers_embed_idx', 'read'), pg_prewarm('papers', 'read')")
        state = db_state(conn)

        for _ in range(3):
            encoder.encode([query_text("warmup")])
        embed_ms = []
        for entry in labels:
            t0 = time.perf_counter()
            encoder.encode([query_text(entry["query"])])
            embed_ms.append((time.perf_counter() - t0) * 1000)

        def run_mode(fn: Any) -> dict[str, Any]:
            runs: list[list[float]] = []
            for run_index in range(WARMUP_RUNS + N_RUNS):
                ms: list[float] = []
                for entry in labels:
                    t0 = time.perf_counter()
                    fn(entry)
                    ms.append((time.perf_counter() - t0) * 1000)
                if run_index >= WARMUP_RUNS:
                    runs.append(ms)
            return {
                "sql": across_runs(runs),
                "runs": runs,
            }

        bm25 = run_mode(lambda e: search_bm25(conn, query=e["query"], k=K))
        vector = run_mode(
            lambda e: search_vector(conn, query_vec=e["embedding"], k=K, ef_search=VECTOR_EF)
        )
        hybrid = run_mode(
            lambda e: search_hybrid(
                conn,
                query=e["query"],
                query_vec=e["embedding"],
                k=K,
                depth=HYBRID_DEPTH,
                ef_search=HYBRID_EF,
            )
        )

    def with_embed(mode: dict[str, Any]) -> dict[str, Any]:
        return across_runs(
            [[a + b for a, b in zip(embed_ms, run, strict=True)] for run in mode["runs"]]
        )

    results_path = out_dir / "results_mode_latency.json"
    old = json.loads(results_path.read_text()) if results_path.exists() else None
    old_state = (old or {}).get("method", {}).get("db_state")
    prior = (
        carry_superseded(
            old,
            key=state_key(old_state) if old_state else "superseded_unrecorded_state",
            why=f"measured against {old_state or 'an unrecorded database state'}; "
            f"this run measured {state}",
            keep=("measured_at", "bm25", "vector", "hybrid"),
        )
        if old_state != state
        else carry_superseded(old)
    )

    results = {
        "measured_at": datetime.now(UTC).isoformat(),
        "method": method_record(
            timing_window="sql: the mode's search function as the API calls it "
            "(k=20 full rows; SET LOCALs where applicable). e2e_with_embed adds "
            "the per-sample single-text encode (vector/hybrid only). "
            "embed-cached e2e = the sql block itself.",
            protocol=f"pg_prewarm; 520 distinct queries; per mode {WARMUP_RUNS} warmup "
            f"+ {N_RUNS} measured runs, across_runs gate; one session, all modes",
            settings={
                "k": K,
                "vector_ef": VECTOR_EF,
                "hybrid_depth": HYBRID_DEPTH,
                "hybrid_ef": HYBRID_EF,
            },
            embed_component={
                "p50_ms": round(statistics.median(embed_ms), 1),
                "note": "sampled once per query, this session",
            },
            known_item_caveat="500/520 queries are corpus titles (see recall sweep caveats)",
            db_state=state,
        ),
        "bm25": {"sql": bm25["sql"]},
        "vector": {"sql": vector["sql"], "e2e_with_embed": with_embed(vector)},
        "hybrid": {"sql": hybrid["sql"], "e2e_with_embed": with_embed(hybrid)},
        **prior,
    }
    results_path.write_text(json.dumps(results, indent=2))
    summary = {
        mode: {
            "sql_p50": results[mode]["sql"]["p50_ms"],
            "sql_p95": results[mode]["sql"]["p95_ms"],
        }
        for mode in ("bm25", "vector", "hybrid")
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
