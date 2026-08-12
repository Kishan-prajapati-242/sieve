"""What the HNSW index buys the HYBRID mode, paired — and where hybrid's
18 ms actually goes.

Two questions in one run, because both need the same session:

  1. The flagship speedup. Every published speedup so far measured
     search_vector(), which ships at ef=40. The mode a user actually gets
     is search_hybrid() at depth=200/ef=600, and its speedup is a
     different number. Baseline and candidate are the SAME call — only
     enable_indexscan differs — so the window is identical by
     construction (paired_speedup.py's rule, applied one level up).

  2. Where the time goes. hybrid sql p50 has read 13.6, 15.8, 15.3 and
     18.2 ms across four sessions. Cross-session comparison is exactly
     what the noise-floor finding says is unpublishable, so instead of
     comparing sessions this decomposes the current number: the fused
     statement against each CTE's workload run alone, all paired inside
     one query slot. bm25 at depth and vector at depth are what the CTEs
     do; whatever hybrid costs beyond their sum is fusion — the RRF join,
     the second sort, and the final k-row fetch.

The decomposition is an attribution, not an identity: Postgres may
overlap the two CTEs, so `fusion_overhead` is an upper bound on genuine
fusion cost and a lower bound on how much the CTEs share.
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
from api.search.fusion import HYBRID_DEFAULT_EF_SEARCH, search_hybrid
from api.search.vector import search_vector
from bench.harness import across_runs, db_state, load_ground_truth, method_record, paired_ratio

K = 20
DEPTH = 200
EF = HYBRID_DEFAULT_EF_SEARCH
N_RUNS = 3
WARMUP_RUNS = 1

VARIANTS = ("hybrid_exact", "hybrid_hnsw", "vector_depth", "bm25_depth")


def force_exact(conn: psycopg.Connection) -> None:
    conn.execute("SET enable_indexscan = off")
    conn.execute("SET enable_bitmapscan = off")


def allow_index(conn: psycopg.Connection) -> None:
    conn.execute("RESET enable_indexscan")
    conn.execute("RESET enable_bitmapscan")


def time_variant(conn: psycopg.Connection, variant: str, entry: dict[str, Any]) -> float:
    """One timed call. Planner GUCs are set before the clock starts."""
    if variant == "hybrid_exact":
        force_exact(conn)
    else:
        allow_index(conn)
    t0 = time.perf_counter()
    if variant in ("hybrid_exact", "hybrid_hnsw"):
        search_hybrid(
            conn,
            query=entry["query"],
            query_vec=entry["embedding"],
            k=K,
            depth=DEPTH,
            ef_search=EF,
        )
    elif variant == "vector_depth":
        search_vector(conn, query_vec=entry["embedding"], k=DEPTH, ef_search=EF)
    else:
        search_bm25(conn, query=entry["query"], k=DEPTH)
    elapsed = (time.perf_counter() - t0) * 1000
    allow_index(conn)
    return elapsed


def main() -> None:
    out_dir = Path(__file__).parent
    labels, gt_method = load_ground_truth(out_dir / "labels" / "exact_top200_wide.json")
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

        per_run: dict[str, list[list[float]]] = {v: [] for v in VARIANTS}
        for run_index in range(WARMUP_RUNS + N_RUNS):
            run: dict[str, list[float]] = {v: [] for v in VARIANTS}
            for i, entry in enumerate(labels):
                rot = i % len(VARIANTS)  # no fixed second-mover advantage
                for variant in VARIANTS[rot:] + VARIANTS[:rot]:
                    run[variant].append(time_variant(conn, variant, entry))
            if run_index >= WARMUP_RUNS:
                for variant in VARIANTS:
                    per_run[variant].append(run[variant])

    def per_query(variant: str) -> list[float]:
        runs = per_run[variant]
        return [statistics.median([run[i] for run in runs]) for i in range(len(runs[0]))]

    med = {v: per_query(v) for v in VARIANTS}
    retrieval = list(zip(med["hybrid_exact"], med["hybrid_hnsw"], strict=True))
    e2e = [(b + e, c + e) for (b, c), e in zip(retrieval, embed_ms, strict=True)]

    # Attribution of the shipped hybrid latency, per query then aggregated.
    overhead = [
        h - (v + b)
        for h, v, b in zip(med["hybrid_hnsw"], med["vector_depth"], med["bm25_depth"], strict=True)
    ]
    parts = {
        "hybrid_p50_ms": round(statistics.median(med["hybrid_hnsw"]), 2),
        "vector_at_depth_p50_ms": round(statistics.median(med["vector_depth"]), 2),
        "bm25_at_depth_p50_ms": round(statistics.median(med["bm25_depth"]), 2),
        "fusion_overhead_p50_ms": round(statistics.median(overhead), 2),
        "vector_share_of_hybrid": round(
            statistics.median(med["vector_depth"]) / statistics.median(med["hybrid_hnsw"]), 3
        ),
        "reads_as": "components are what each CTE's workload costs run alone; "
        "fusion_overhead is hybrid minus their sum, an UPPER bound on genuine "
        "fusion cost (Postgres may overlap the CTEs, and any sharing shows up "
        "here as a negative contribution)",
    }

    results: dict[str, Any] = {
        "measured_at": datetime.now(UTC).isoformat(),
        "method": method_record(
            timing_window="search_hybrid(k=20, depth=200, ef=600) round trip, "
            "identical on both sides of the speedup: same call, same rows. The "
            "baseline differs only by enable_indexscan/bitmapscan = off, which "
            "forces the vector CTE to an exact scan. Component variants time "
            "search_vector(k=200) and search_bm25(k=200) — the CTEs' workloads.",
            protocol=f"pg_prewarm; {len(labels)} queries; {WARMUP_RUNS} warmup + "
            f"{N_RUNS} measured runs; four variants back to back inside each "
            "query's slot, order rotated; repetitions collapsed per query first",
            settings={"k": K, "depth": DEPTH, "ef_search": EF},
            db_state=state,
            ground_truth_corpus=gt_method.get("corpus_size"),
            embed_component={"p50_ms": round(statistics.median(embed_ms), 1)},
            known_item_caveat="500/520 queries are corpus titles",
        ),
        "levels": {v: across_runs(per_run[v]) for v in VARIANTS},
        "retrieval_only": paired_ratio(
            retrieval, window="sql only: search_hybrid(k=20, depth=200, ef=600)"
        ),
        "end_to_end": paired_ratio(
            e2e, window="end-to-end: query embed + search_hybrid(k=20, depth=200, ef=600)"
        ),
        "decomposition": parts,
    }
    (out_dir / "results_paired_hybrid.json").write_text(json.dumps(results, indent=2))
    levels = results["levels"]
    print(json.dumps({k: results[k] for k in ("retrieval_only", "end_to_end")}, indent=2))
    print(json.dumps(parts, indent=2))
    print(json.dumps({v: levels[v]["p50_ms"] for v in VARIANTS}, indent=2))


if __name__ == "__main__":
    main()
