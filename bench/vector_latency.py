"""Vector-mode latency by component, under the HNSW measurement protocol
recorded in docs/progress.md (2026-07-31):

  - pg_prewarm ('read' mode: OS page cache) of papers_embed_idx AND the
    heap before measuring — HNSW queries traverse different graph regions,
    so unlike a seq scan, interleaved repetition does NOT homogenize cache
    state; prewarming does, by construction.
  - Distinct-query-heavy: 520 distinct queries (20 eval + 500 corpus
    titles), each ONCE per run, so percentiles describe the query
    distribution rather than one query's cache luck.
  - Multi-run (3) with the across_runs stability gate.

Components measured per sample, separately:
  embed_ms — one single-text encode through the REAL request path
             (runtime-style single encode, prefix applied), not batched.
  sql_ms   — search_vector() as the API calls it (SET LOCALs included).
  end-to-end is their sum; the HTTP layer adds ~0.1ms serialize on top
  (measured separately by the API's own timings decomposition).

Run inside compose:
    docker compose run --rm --no-deps -v ./bench:/app/bench \
        -v ./models/bge-small-en-v1.5:/models -e EMBED_MODEL_DIR=/models \
        -e DATABASE_URL=postgresql://sieve:sieve@postgres:5432/sieve \
        test python -m bench.vector_latency
"""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from api.embed.onnx_encoder import OnnxEncoder
from api.embed.texts import query_text
from api.search.vector import DEFAULT_EF_SEARCH, search_vector
from bench.exact_scan_baseline import EVAL_QUERIES, TITLE_SAMPLE_SQL
from bench.harness import across_runs, method_record

N_TITLE_QUERIES = 500
N_RUNS = 3
# The first pass over freshly-prewarmed pages still pays shared_buffers
# fill and allocator warmup — observed as monotone settling (run p50s
# 15.6 -> 9.9 -> 9.1 before this existed). Same warmup-discard principle
# as within-run warmup, applied at run granularity.
WARMUP_RUNS = 1
K = 10


def main() -> None:
    out_dir = Path(__file__).parent
    encoder = OnnxEncoder(os.environ["EMBED_MODEL_DIR"])

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        titles = [t for (t,) in conn.execute(TITLE_SAMPLE_SQL, {"n": N_TITLE_QUERIES}).fetchall()]
        texts = [text for _, text in EVAL_QUERIES] + titles

        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm")
        prewarmed = conn.execute(
            "SELECT pg_prewarm('papers_embed_idx', 'read'), pg_prewarm('papers', 'read')"
        ).fetchone()
        assert prewarmed is not None

        # Encoder warmup: session initialization is a one-time cost per
        # process, not per-query latency.
        for _ in range(3):
            encoder.encode([query_text("warmup")])

        embed_runs: list[list[float]] = []
        sql_runs: list[list[float]] = []
        e2e_runs: list[list[float]] = []
        for run_index in range(WARMUP_RUNS + N_RUNS):
            embed_ms: list[float] = []
            sql_ms: list[float] = []
            for text in texts:
                t0 = time.perf_counter()
                vec = [float(x) for x in encoder.encode([query_text(text)])[0]]
                t1 = time.perf_counter()
                search_vector(conn, query_vec=vec, k=K, ef_search=DEFAULT_EF_SEARCH)
                t2 = time.perf_counter()
                embed_ms.append((t1 - t0) * 1000)
                sql_ms.append((t2 - t1) * 1000)
            if run_index < WARMUP_RUNS:
                continue  # discarded: see WARMUP_RUNS comment
            embed_runs.append(embed_ms)
            sql_runs.append(sql_ms)
            e2e_runs.append([a + b for a, b in zip(embed_ms, sql_ms, strict=True)])

    embed, sql, e2e = across_runs(embed_runs), across_runs(sql_runs), across_runs(e2e_runs)
    embed_fraction = (
        round(embed["p50_ms"] / e2e["p50_ms"], 2)
        if embed["p50_ms"] is not None and e2e["p50_ms"] is not None
        else None
    )

    results = {
        "measured_at": datetime.now(UTC).isoformat(),
        "method": method_record(
            timing_window="embed_ms: single-text encode incl. tokenization "
            "(prefix applied), the real per-request path, NOT batched. sql_ms: "
            "search_vector() round trip as the API calls it, SET LOCALs "
            "included. end_to_end: their sum; excludes HTTP framework "
            "(~0.1ms serialize per the API's own decomposition).",
            protocol="pg_prewarm('read') of papers_embed_idx + papers heap before "
            "measuring; 520 distinct queries once per run (no repetition), "
            f"{WARMUP_RUNS} discarded warmup run (first pass pays shared_buffers "
            f"fill even over prewarmed OS-cache pages), then {N_RUNS} measured "
            "runs, across_runs stability gate",
            queries=f"{len(EVAL_QUERIES)} eval + {N_TITLE_QUERIES} corpus titles "
            "(deterministic id-spread)",
            k=K,
            ef_search=DEFAULT_EF_SEARCH,
            iterative_scan="strict_order (the production path)",
            encoder="bge-small-en-v1.5 fp32 ONNX (DECISION-2b/2d)",
        ),
        "embed_ms": embed,
        "sql_ms": sql,
        "end_to_end_ms": e2e,
        "embed_fraction_of_e2e_p50": embed_fraction,
    }
    (out_dir / "results_vector_latency.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
