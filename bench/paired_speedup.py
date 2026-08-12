"""Exact scan vs HNSW, paired within a run — the replacement for the
cross-run speedup ratios retired on 2026-08-12.

Every previous speedup in this project divided a number from one session
by a number from another. That held until the exact-scan denominator
drifted ~15% slower for unexplained reasons and pushed the published
end-to-end ratio from 6.3x to 7.2x: the claim improved because the
baseline got worse. Pairing removes the failure mode rather than
correcting it after the fact.

Protocol:

  Both sides call search_vector() — same projection, same k, same row
  transfer. The ONLY difference is whether the planner may use
  papers_embed_idx: the baseline runs with enable_indexscan/bitmapscan
  off (verified Seq Scan via EXPLAIN), the candidate runs untouched. The
  window is therefore identical by construction. The retired ratio
  compared a 50-row (id, distance) scan against a 20-full-row search and
  passed harness.speedup() one hand-written window string for both,
  which satisfied the guard without the windows actually matching.

  Three variants are timed back to back inside each query's slot —
  exact, HNSW at ef=40 (vector-mode default), HNSW at ef=600 (hybrid
  default) — with the order ROTATED by query index, so no variant
  systematically benefits from being second on a warm buffer.

  SET/RESET of the planner GUCs happens outside every timing window.

  e2e adds the per-query embed cost to BOTH sides: an exact-scan
  implementation of the endpoint would embed the query too.

Ratios are computed per query from that query's own adjacent
measurements, then aggregated by harness.paired_ratio().
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
from api.search.vector import search_vector
from bench.harness import (
    across_runs,
    db_state,
    load_ground_truth,
    method_record,
    paired_ratio,
)

K = 20
EF_VECTOR = 40  # vector-mode shipped default
EF_HYBRID = 600  # DECISION-2e hybrid default
N_RUNS = 3
WARMUP_RUNS = 1

VARIANTS = ("exact", "hnsw_ef40", "hnsw_ef600")


def force_exact(conn: psycopg.Connection) -> None:
    conn.execute("SET enable_indexscan = off")
    conn.execute("SET enable_bitmapscan = off")


def allow_index(conn: psycopg.Connection) -> None:
    conn.execute("RESET enable_indexscan")
    conn.execute("RESET enable_bitmapscan")


def verify_plans(conn: psycopg.Connection, vec: list[float]) -> dict[str, str]:
    """Prove each side gets the plan it claims before timing anything."""
    from api.search.vector import VECTOR_SQL, vector_literal

    params = {"q": vector_literal(vec), "k": K, "year_from": None, "year_to": None}
    plans = {}
    for name, setup in (("exact", force_exact), ("hnsw", allow_index)):
        setup(conn)
        plans[name] = "\n".join(
            r[0]
            for r in conn.execute(f"EXPLAIN {VECTOR_SQL}", params)  # noqa: S608
        )
    allow_index(conn)
    assert "Seq Scan on papers" in plans["exact"], plans["exact"]
    assert "papers_embed_idx" not in plans["exact"], plans["exact"]
    assert "papers_embed_idx" in plans["hnsw"], plans["hnsw"]
    return plans


def time_variant(conn: psycopg.Connection, variant: str, vec: list[float]) -> float:
    """One timed call. GUC changes happen before the clock starts."""
    if variant == "exact":
        force_exact(conn)
        ef = EF_VECTOR
    else:
        allow_index(conn)
        ef = EF_VECTOR if variant == "hnsw_ef40" else EF_HYBRID
    t0 = time.perf_counter()
    search_vector(conn, query_vec=vec, k=K, ef_search=ef)
    elapsed = (time.perf_counter() - t0) * 1000
    if variant == "exact":
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
        plans = verify_plans(conn, labels[0]["embedding"])

        for _ in range(3):
            encoder.encode([query_text("warmup")])
        embed_ms = []
        for entry in labels:
            t0 = time.perf_counter()
            encoder.encode([query_text(entry["query"])])
            embed_ms.append((time.perf_counter() - t0) * 1000)

        # per_run[variant] = list of runs, each a list of 520 samples
        per_run: dict[str, list[list[float]]] = {v: [] for v in VARIANTS}
        for run_index in range(WARMUP_RUNS + N_RUNS):
            run: dict[str, list[float]] = {v: [] for v in VARIANTS}
            for i, entry in enumerate(labels):
                rot = i % len(VARIANTS)  # rotate order: no fixed second-mover advantage
                for variant in VARIANTS[rot:] + VARIANTS[:rot]:
                    run[variant].append(time_variant(conn, variant, entry["embedding"]))
            if run_index >= WARMUP_RUNS:
                for variant in VARIANTS:
                    per_run[variant].append(run[variant])

    # Collapse repetitions per query first: repeats of one query are
    # correlated, so the bootstrap resamples queries, not measurements.
    def per_query(variant: str) -> list[float]:
        runs = per_run[variant]
        return [statistics.median([run[i] for run in runs]) for i in range(len(runs[0]))]

    med = {v: per_query(v) for v in VARIANTS}
    results: dict[str, Any] = {
        "measured_at": datetime.now(UTC).isoformat(),
        "method": method_record(
            timing_window="search_vector(query_vec, k=20) round trip, identical on "
            "both sides: same SQL, same projection, same 20 full rows. Baseline "
            "differs only by enable_indexscan/bitmapscan = off (Seq Scan verified "
            "via EXPLAIN). e2e adds the same per-query embed cost to both sides.",
            protocol=f"pg_prewarm; {len(labels)} distinct queries; {WARMUP_RUNS} warmup "
            f"+ {N_RUNS} measured runs; the three variants timed back to back inside "
            "each query's slot with the order rotated by query index; repetitions "
            "collapsed per query (median) before ratios",
            settings={"k": K, "ef_vector": EF_VECTOR, "ef_hybrid": EF_HYBRID},
            db_state=state,
            ground_truth_corpus=gt_method.get("corpus_size"),
            embed_component={"p50_ms": round(statistics.median(embed_ms), 1)},
            known_item_caveat="500/520 queries are corpus titles (see recall sweep caveats)",
            plans=plans,
        ),
        "levels": {v: across_runs(per_run[v]) for v in VARIANTS},
    }

    for name, ef_variant in (("ef40", "hnsw_ef40"), ("ef600", "hnsw_ef600")):
        retrieval = list(zip(med["exact"], med[ef_variant], strict=True))
        e2e = [(b + e, c + e) for (b, c), e in zip(retrieval, embed_ms, strict=True)]
        results[f"retrieval_only_{name}"] = paired_ratio(
            retrieval, window="sql only: search_vector(k=20), full rows"
        )
        results[f"end_to_end_{name}"] = paired_ratio(
            e2e, window="end-to-end: query embed + search_vector(k=20), full rows"
        )

    (out_dir / "results_paired_speedup.json").write_text(json.dumps(results, indent=2))
    summary = {k: v for k, v in results.items() if k.startswith(("retrieval", "end_to"))}
    levels = {v: results["levels"][v]["p50_ms"] for v in VARIANTS}
    print(json.dumps({"levels_p50": levels}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
