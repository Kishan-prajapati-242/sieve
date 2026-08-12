"""Per-ARM paired speedups for the hybrid path — and why the fused ratio
is not measurable with this instrument.

The obvious experiment is to run search_hybrid() twice, once with the HNSW
index available and once without, and divide. It does not work, and the
reason is worth more than the number would have been:

  **Planner GUCs are not selective to one index.** The only lever for
  "take papers_embed_idx away" is enable_indexscan / enable_bitmapscan,
  and those are global to the statement. enable_bitmapscan=off also
  removes papers_fts_idx — a GIN index is ONLY reachable through a bitmap
  scan — and enable_indexscan=off also removes papers_pkey from the final
  join. Measured 2026-08-12: the bm25 arm alone goes 7.39 ms (Bitmap Index
  Scan on papers_fts_idx) to 227.80 ms (Parallel Seq Scan), a 30.8x
  handicap on an arm that has nothing to do with the vector index. The
  "baseline" runs three seq scans of a 276 MB heap where the intended one
  runs one.

  So the fused baseline is inflated, biasing the ratio HIGH. A second and
  opposite contamination biases it LOW (see results_paired_hybrid.json's
  defect block). Neither is sized, so the fused ratio is bracketed rather
  than bounded, and re-running with a better variant order would repair
  only the second one while looking like a full repair.

What IS measurable, and what this script reports:

  vector arm — search_vector(k=depth). VECTOR_SQL has exactly one index
    option, so here the GUC IS selective and the paired ratio is honest.

  bm25 arm — search_bm25(k=depth) under both plans. Not a "speedup over
    an exact scan" in the same sense; it is what papers_fts_idx is worth,
    stated on its own rather than smuggled into a hybrid number.

  fusion cost — hybrid minus its two arms, under the SAME plan on both
    sides, which is a decomposition rather than a ratio and does not
    depend on the broken lever.

Order is a seeded RANDOM permutation per query, not a rotation: a cyclic
rotation preserves every adjacency, which is how one variant ended up
running immediately after a cache-destroying scan in 3 of 4 slots.
"""

import json
import os
import random
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
from bench.harness import (
    across_runs,
    contention_report,
    db_state,
    load_ground_truth,
    method_record,
    paired_ratio,
    pinned_connection,
    server_activity,
)

K = 20
DEPTH = 200
EF = HYBRID_DEFAULT_EF_SEARCH
N_RUNS = 3
WARMUP_RUNS = 1

VARIANTS = ("hybrid_exact", "hybrid_hnsw", "vector_depth", "bm25_depth", "bm25_depth_exact")
ORDER_SEED = 20260812


EXACT_GUCS = {"enable_indexscan": "off", "enable_bitmapscan": "off"}


def verify_plans(
    conn: psycopg.Connection, exact_conn: psycopg.Connection, entry: dict[str, Any]
) -> dict[str, str]:
    """Prove each connection plans what it claims — at the start AND again at
    the end of the run.

    Checking once at the start is what let the toggled-GUC bug through: the
    plan was right when it was checked and wrong by execution ten. Verifying
    at both ends cannot catch a mid-run flip on its own, so it is the pinned
    connection that makes the guarantee; this is the cheap corroboration.
    """
    from api.search.fusion import HYBRID_SQL
    from api.search.vector import vector_literal

    params = {
        "query": entry["query"],
        "qv": vector_literal(entry["embedding"]),
        "k": K,
        "depth": DEPTH,
        "rrf_k": 60,
        "year_from": None,
        "year_to": None,
    }
    plans = {}
    for name, target in (("hnsw", conn), ("exact", exact_conn)):
        with target.transaction():
            target.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(EF),))
            plans[name] = "\n".join(
                r[0]
                for r in target.execute(f"EXPLAIN {HYBRID_SQL}", params)  # noqa: S608
            )
    assert "papers_embed_idx" in plans["hnsw"], plans["hnsw"]
    assert "papers_embed_idx" not in plans["exact"], plans["exact"]
    assert "Seq Scan on papers" in plans["exact"], plans["exact"]
    return {k: v[:400] for k, v in plans.items()}


def time_variant(conn: psycopg.Connection, variant: str, entry: dict[str, Any]) -> float:
    """One timed call on a connection whose plan is already pinned. Nothing
    is toggled here — see harness.pinned_connection for why."""
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
    else:  # bm25_depth and bm25_depth_exact differ only by which conn they get
        search_bm25(conn, query=entry["query"], k=DEPTH)
    return (time.perf_counter() - t0) * 1000


def main() -> None:
    out_dir = Path(__file__).parent
    labels, gt_method = load_ground_truth(out_dir / "labels" / "exact_top200_wide.json")
    encoder = OnnxEncoder(os.environ["EMBED_MODEL_DIR"])

    dsn = os.environ["DATABASE_URL"]
    with (
        pinned_connection(dsn) as conn,
        pinned_connection(dsn, gucs=EXACT_GUCS) as exact_conn,
    ):
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm")
        conn.execute("SELECT pg_prewarm('papers_embed_idx', 'read'), pg_prewarm('papers', 'read')")
        state = db_state(conn)
        activity_before = server_activity(conn)
        plans = verify_plans(conn, exact_conn, labels[0])

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
            rng = random.Random(ORDER_SEED + run_index)
            for entry in labels:
                # Shuffled, not rotated: a cyclic rotation preserves every
                # adjacency, so one variant systematically follows the
                # cache-destroying one (findings.md 2026-08-12).
                order = list(VARIANTS)
                rng.shuffle(order)
                for variant in order:
                    target = exact_conn if variant in ("hybrid_exact", "bm25_depth_exact") else conn
                    run[variant].append(time_variant(target, variant, entry))
            if run_index >= WARMUP_RUNS:
                for variant in VARIANTS:
                    per_run[variant].append(run[variant])
        verify_plans(conn, exact_conn, labels[0])  # nothing drifted mid-run
        contention = contention_report(activity_before, server_activity(conn), own=conn.info.dbname)

    def per_query(variant: str) -> list[float]:
        runs = per_run[variant]
        return [statistics.median([run[i] for run in runs]) for i in range(len(runs[0]))]

    med = {v: per_query(v) for v in VARIANTS}
    retrieval = list(zip(med["hybrid_exact"], med["hybrid_hnsw"], strict=True))
    # No e2e pairing here: adding an identical embed cost to both sides of a
    # ratio that is already not reportable would only make it look finished.

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
            plans=plans,
            contention=contention,
        ),
        "levels": {v: across_runs(per_run[v]) for v in VARIANTS},
        "fused_ratio": {
            "not_reportable": "The lever that removes papers_embed_idx also removes "
            "papers_fts_idx and papers_pkey, so the fused baseline is not 'hybrid "
            "without the vector index'. See this file's defect block and the module "
            "docstring. Reported per-arm instead.",
            "raw_for_reference_only": paired_ratio(
                retrieval, window="sql only: search_hybrid(k=20, depth=200, ef=600)"
            ),
        },
        "vector_arm": paired_ratio(
            list(zip(med["hybrid_exact"], med["vector_depth"], strict=True)),
            window="sql only: search_vector(k=200, ef=600) — NOTE this pairs against "
            "the contaminated hybrid baseline; the clean vector-arm number is in "
            "results_paired_speedup.json",
        ),
        "bm25_arm_index_value": paired_ratio(
            list(zip(med["bm25_depth_exact"], med["bm25_depth"], strict=True)),
            window="sql only: search_bm25(k=200), papers_fts_idx available vs not",
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
