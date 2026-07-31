"""Recall/latency tradeoff of papers_embed_idx across hnsw.ef_search.

Phase 1 — ground truth: exact top-50 for 520 distinct queries (the 20 eval
queries + 500 corpus titles), computed with the seq scan FORCED and
EXPLAIN-verified, written to bench/labels/exact_top50_wide.json with query
vectors inline. 20 queries is too thin for a recall mean; at n=520 the
standard error of the mean is reported alongside every recall figure.

Phase 2 — sweep: ef_search from below k to 16x the default, under BOTH
iterative_scan settings, since the production config (strict_order)
affects what a scan returns when ef < LIMIT:

  strict_order (production): the scan keeps traversing until LIMIT rows.
  off: the scan yields at most ~ef_search rows — at ef=5 a LIMIT 50 query
  under-returns, and recall@50 measures that honestly.

Distance ties are handled explicitly: halfvec quantizes to fp16, so exact
distance ties are common (duplicate-abstract twins tie by construction). A
retrieved id counts as a hit if it is in the exact top-k OR its distance
<= the k-th exact distance + 1e-9 — papers tied at the boundary are
interchangeable, and set intersection alone would punish the index for
returning a different-but-equally-near tie member.

Timing windows (stated per number in the output): sql_ms is a lean
id+distance LIMIT 50 round trip (NOT the full-row production fetch — that
end-to-end lives in bench/vector_latency.py); embed_ms is a single-text
encode; e2e is their per-sample sum. Latencies here are single-pass
comparative curves across ef within one session — headline numbers for
the chosen operating point come from bench/vector_latency.py rerun at
that ef.

Run inside compose (same mounts as the other bench scripts):
    docker compose run --rm --no-deps -v ./bench:/app/bench \
        -v ./models/bge-small-en-v1.5:/models -e EMBED_MODEL_DIR=/models \
        -e DATABASE_URL=postgresql://sieve:sieve@postgres:5432/sieve \
        test python -m bench.hnsw_recall_sweep
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
from bench.exact_scan_baseline import EVAL_QUERIES, TITLE_SAMPLE_SQL
from bench.harness import method_record, summarize

N_TITLE_QUERIES = 500
EF_POINTS = [5, 10, 20, 40, 80, 160, 320, 640]
FETCH_K = 50
TIE_EPS = 1e-9

EXACT_SQL = """
SELECT id, (embedding <=> %(q)s::halfvec)::float8 AS distance
FROM papers
ORDER BY embedding <=> %(q)s::halfvec
LIMIT %(k)s
"""


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in vec) + "]"


def build_ground_truth(conn: psycopg.Connection, encoder: OnnxEncoder, path: Path) -> None:
    titles = [t for (t,) in conn.execute(TITLE_SAMPLE_SQL, {"n": N_TITLE_QUERIES}).fetchall()]
    texts = [text for _, text in EVAL_QUERIES] + titles
    conn.execute("SET enable_indexscan = off")
    conn.execute("SET enable_bitmapscan = off")
    plan = "\n".join(
        r[0]
        for r in conn.execute(
            f"EXPLAIN {EXACT_SQL}",  # noqa: S608
            {"q": vector_literal([1.0] + [0.0] * 383), "k": FETCH_K},
        )
    )
    assert "Seq Scan on papers" in plan and "papers_embed_idx" not in plan

    labels = []
    for text in texts:
        emb = [float(x) for x in encoder.encode([query_text(text)])[0]]
        top = conn.execute(EXACT_SQL, {"q": vector_literal(emb), "k": FETCH_K}).fetchall()
        labels.append(
            {
                "query": text,
                "embedding": emb,
                "top50": [{"id": pid, "distance": d} for pid, d in top],
            }
        )
    path.write_text(json.dumps(labels))
    conn.execute("SET enable_indexscan = on")
    conn.execute("SET enable_bitmapscan = on")


def hnsw_fetch(
    conn: psycopg.Connection, q: str, ef: int, iterative: str
) -> list[tuple[int, float]]:
    with conn.transaction():
        conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(ef),))
        conn.execute("SELECT set_config('hnsw.iterative_scan', %s, true)", (iterative,))
        return conn.execute(EXACT_SQL, {"q": q, "k": FETCH_K}).fetchall()


def tie_aware_recall(
    exact_top: list[dict[str, Any]], retrieved: list[tuple[int, float]], k: int
) -> float:
    """Hits = retrieved[:k] ids in exact top-k, OR tied with the boundary."""
    exact_ids = {e["id"] for e in exact_top[:k]}
    boundary = exact_top[k - 1]["distance"] if len(exact_top) >= k else float("inf")
    hits = sum(1 for pid, d in retrieved[:k] if pid in exact_ids or d <= boundary + TIE_EPS)
    return hits / k


def main() -> None:
    out_dir = Path(__file__).parent
    labels_path = out_dir / "labels" / "exact_top50_wide.json"
    encoder = OnnxEncoder(os.environ["EMBED_MODEL_DIR"])

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        if not labels_path.exists():
            print("building wide ground truth (forced seq scan, 520 queries)...", flush=True)
            build_ground_truth(conn, encoder, labels_path)
        labels = json.loads(labels_path.read_text())
        vec_lits = [vector_literal(entry["embedding"]) for entry in labels]

        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm")
        conn.execute("SELECT pg_prewarm('papers_embed_idx', 'read'), pg_prewarm('papers', 'read')")
        plan = "\n".join(
            r[0]
            for r in conn.execute(f"EXPLAIN {EXACT_SQL}", {"q": vec_lits[0], "k": FETCH_K})  # noqa: S608
        )
        assert "papers_embed_idx" in plan  # natural planner choice, no forcing

        # Embed timing: one single-text encode per query, this session, after
        # warmup. Reused for every ef point's e2e (the embed cost does not
        # depend on ef; stated in the method record).
        for _ in range(3):
            encoder.encode([query_text("warmup")])
        embed_ms: list[float] = []
        for entry in labels:
            t0 = time.perf_counter()
            encoder.encode([query_text(entry["query"])])
            embed_ms.append((time.perf_counter() - t0) * 1000)

        # Sweep warmup: one pass at the default so first-touch costs don't
        # land on the first ef point.
        for q in vec_lits[:100]:
            hnsw_fetch(conn, q, 40, "strict_order")

        curves: dict[str, list[dict[str, Any]]] = {}
        for iterative in ("strict_order", "off"):
            points = []
            for ef in EF_POINTS:
                sql_ms: list[float] = []
                r10: list[float] = []
                r50: list[float] = []
                rows_returned: list[int] = []
                for entry, q in zip(labels, vec_lits, strict=True):
                    t0 = time.perf_counter()
                    retrieved = hnsw_fetch(conn, q, ef, iterative)
                    sql_ms.append((time.perf_counter() - t0) * 1000)
                    rows_returned.append(len(retrieved))
                    r10.append(tie_aware_recall(entry["top50"], retrieved, 10))
                    r50.append(tie_aware_recall(entry["top50"], retrieved, 50))
                lat = summarize(sql_ms)
                e2e = summarize([a + b for a, b in zip(embed_ms, sql_ms, strict=True)])
                points.append(
                    {
                        "ef_search": ef,
                        "recall_at_10": round(statistics.mean(r10), 4),
                        "recall_at_10_se": round(statistics.stdev(r10) / math.sqrt(len(r10)), 4),
                        "recall_at_50": round(statistics.mean(r50), 4),
                        "recall_at_50_se": round(statistics.stdev(r50) / math.sqrt(len(r50)), 4),
                        "mean_rows_returned": round(statistics.mean(rows_returned), 1),
                        "sql_p50_ms": lat["p50_ms"],
                        "sql_p95_ms": lat["p95_ms"],
                        "e2e_p50_ms": e2e["p50_ms"],
                    }
                )
                print(f"{iterative} ef={ef}: {points[-1]}", flush=True)
            curves[iterative] = points

    results = {
        "measured_at": datetime.now(UTC).isoformat(),
        "method": method_record(
            timing_window="sql_ms: id+distance LIMIT 50 round trip incl. per-query "
            "SET LOCALs (lean fetch, not the full-row production path); embed_ms: "
            "single-text encode, sampled once per query this session and reused "
            "across ef points; e2e: per-sample sum",
            protocol="pg_prewarm('read') of index+heap; 520 distinct queries once "
            "per (iterative, ef) point; single-pass comparative curves — headline "
            "operating-point numbers come from bench/vector_latency.py",
            n_queries=len(labels),
            ground_truth="bench/labels/exact_top50_wide.json (forced seq scan)",
            tie_handling=f"hit if id in exact top-k OR distance <= boundary + {TIE_EPS}",
            k_fetch=FETCH_K,
        ),
        "curves": curves,
    }
    (out_dir / "results_hnsw_recall_sweep.json").write_text(json.dumps(results, indent=2))

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax1 = plt.subplots(figsize=(9, 5.5))
    strict = curves["strict_order"]
    off = curves["off"]
    efs = [p["ef_search"] for p in strict]
    ax1.errorbar(
        efs,
        [p["recall_at_10"] for p in strict],
        yerr=[p["recall_at_10_se"] for p in strict],
        marker="o",
        label="recall@10 (strict_order)",
    )
    ax1.errorbar(
        efs,
        [p["recall_at_50"] for p in strict],
        yerr=[p["recall_at_50_se"] for p in strict],
        marker="s",
        label="recall@50 (strict_order)",
    )
    ax1.plot(
        efs,
        [p["recall_at_10"] for p in off],
        linestyle="--",
        marker="x",
        label="recall@10 (iterative off)",
        alpha=0.6,
    )
    ax1.plot(
        efs,
        [p["recall_at_50"] for p in off],
        linestyle="--",
        marker="+",
        label="recall@50 (iterative off)",
        alpha=0.6,
    )
    ax1.set_xscale("log")
    ax1.set_xticks(efs, [str(e) for e in efs])
    ax1.set_xlabel("hnsw.ef_search (log scale)")
    ax1.set_ylabel("recall vs exact scan")
    ax1.axvline(40, color="gray", linestyle=":", alpha=0.7)
    ax1.grid(alpha=0.3)
    ax2 = ax1.twinx()
    ax2.plot(
        efs,
        [p["sql_p50_ms"] for p in strict],
        color="tab:red",
        marker="d",
        label="SQL p50 ms (strict_order)",
    )
    ax2.set_ylabel("SQL p50 (ms)", color="tab:red")
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="center right", fontsize=8)
    ax1.set_title("HNSW recall/latency vs ef_search — 196,893 papers, m=16, efc=64, n=520 queries")
    (out_dir / "plots").mkdir(exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_dir / "plots" / "hnsw_recall_sweep.png", dpi=150)
    print("plot: bench/plots/hnsw_recall_sweep.png")


if __name__ == "__main__":
    main()
