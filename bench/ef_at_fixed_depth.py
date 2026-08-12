"""Search breadth isolated from candidate depth — the sweep the joint sweep
couldn't do because it glued ef to N in every row (Kishan, 2026-07-31).

Depth is FIXED at N=200; ef_search alone sweeps 200/400/600/800. Reported
per point: vector recall@200 against the exact top-200 denominator
(tie-aware), hybrid SQL p50/p95, e2e p50 with embed and with embed at
zero (Phase 4 query-cache hit). Where recall saturates and what it costs
is the ef half of the coupled decision.

Also instruments the p95 hypothesis: the bm25 CTE ranks EVERY matching
document before LIMIT N, so its cost should track matched-document count,
not N. Per-query mean SQL latency (at ef=200) is correlated against the
query's bm25 match count; the widest and narrowest queries are reported.

Windows and protocol as in fusion_depth_sweep (pg_prewarm, 520 distinct
queries, 1 discarded warmup run + 3 measured, across_runs gate); inherits
the known-item caveat.
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
from bench.harness import across_runs, load_ground_truth, method_record
from bench.hnsw_recall_sweep import EXACT_SQL, tie_aware_recall, vector_literal

DEPTH = 200
EF_POINTS = [200, 400, 600, 800]
K = 20
N_RUNS = 3
WARMUP_RUNS = 1

MATCH_COUNT_SQL = """
SELECT count(*) FROM papers, websearch_to_tsquery('english', %(q)s) AS q
WHERE fts @@ q
"""


def pearson(xs: list[float], ys: list[float]) -> float:
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return cov / (sx * sy) if sx and sy else 0.0


def spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for rank, i in enumerate(order):
            r[i] = float(rank)
        return r

    return pearson(ranks(xs), ranks(ys))


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

        match_counts = [
            conn.execute(MATCH_COUNT_SQL, {"q": entry["query"]}).fetchone()[0]  # type: ignore[index]
            for entry in labels
        ]

        points: list[dict[str, Any]] = []
        per_query_at_200: list[float] = []
        for ef in EF_POINTS:
            recalls = []
            with conn.transaction():
                conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(ef),))
                conn.execute("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)")
                for entry in labels:
                    got = conn.execute(
                        EXACT_SQL, {"q": vector_literal(entry["embedding"]), "k": DEPTH}
                    ).fetchall()
                    recalls.append(tie_aware_recall(entry["top200"], got, DEPTH))

            sql_runs: list[list[float]] = []
            for run_index in range(WARMUP_RUNS + N_RUNS):
                run_ms: list[float] = []
                for entry in labels:
                    t0 = time.perf_counter()
                    search_hybrid(
                        conn,
                        query=entry["query"],
                        query_vec=entry["embedding"],
                        k=K,
                        depth=DEPTH,
                        ef_search=ef,
                    )
                    run_ms.append((time.perf_counter() - t0) * 1000)
                if run_index >= WARMUP_RUNS:
                    sql_runs.append(run_ms)
            if ef == 200:
                per_query_at_200 = [statistics.mean(vals) for vals in zip(*sql_runs, strict=True)]

            sql = across_runs(sql_runs)
            e2e = across_runs(
                [[a + b for a, b in zip(embed_ms, run, strict=True)] for run in sql_runs]
            )
            points.append(
                {
                    "ef_search": ef,
                    "depth": DEPTH,
                    "vector_recall_at_200": round(statistics.mean(recalls), 4),
                    "vector_recall_se": round(
                        statistics.stdev(recalls) / math.sqrt(len(recalls)), 4
                    ),
                    "sql": sql,
                    "e2e_with_embed_p50_ms": e2e["p50_ms"],
                    "e2e_embed_cached_p50_ms": sql["p50_ms"],
                }
            )
            print(json.dumps(points[-1]), flush=True)

    order = sorted(range(len(labels)), key=lambda i: match_counts[i])
    narrowest, widest = order[0], order[-1]
    tail = {
        "pearson_latency_vs_match_count": round(
            pearson([float(c) for c in match_counts], per_query_at_200), 3
        ),
        "spearman_latency_vs_match_count": round(
            spearman([float(c) for c in match_counts], per_query_at_200), 3
        ),
        "narrowest_query": {
            "query": labels[narrowest]["query"][:80],
            "bm25_matches": match_counts[narrowest],
            "mean_sql_ms": round(per_query_at_200[narrowest], 1),
        },
        "widest_query": {
            "query": labels[widest]["query"][:80],
            "bm25_matches": match_counts[widest],
            "mean_sql_ms": round(per_query_at_200[widest], 1),
        },
        "match_count_p50": int(statistics.median(match_counts)),
        "match_count_max": max(match_counts),
    }

    results = {
        "measured_at": datetime.now(UTC).isoformat(),
        "method": method_record(
            timing_window="sql: search_hybrid() round trip (SET LOCALs + fused "
            "statement, k=20 full rows) at FIXED depth=200. embed: single-text "
            "encode sampled once per query. e2e_embed_cached: sql alone (embed=0).",
            protocol="pg_prewarm; 520 distinct queries; per ef: 1 warmup + 3 "
            "measured runs, across_runs gate. Depth fixed at 200 — this isolates "
            "search breadth from candidate depth.",
            tail_diagnosis="per-query mean SQL latency at ef=200 correlated with "
            "bm25 tsquery match count (the bm25 CTE ranks every match before "
            "LIMIT), Pearson + Spearman",
            known_item_caveat="500/520 queries are corpus titles (see recall sweep caveats)",
            k=K,
        ),
        "points": points,
        "bm25_tail_diagnosis": tail,
    }
    (out_dir / "results_ef_at_fixed_depth.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(tail, indent=2))


if __name__ == "__main__":
    main()
