"""The ef dial's exchange rate: what recall costs in latency, both ends.

Written because a speedup was being quoted at ef=40 for a system whose
flagship mode ships at ef=600 (findings.md 2026-08-12). A speedup without
its recall is a price tag with the price removed, so the two have to be
measured together, in one session, on one corpus.

Per ef setting, all from the same run so the pairing holds:

  recall@200 and recall@10 — tie-aware, against the rebuilt ground truth
  sql p50/p95/p99          — search_vector(k=20) as the API calls it
  e2e                      — the same plus the per-query embed
  embed_share              — embed p50 / e2e p50, the figure that decides
                             whether query caching is worth building

recall@200 at ef=40 is the number that did not exist before this script.
The published 0.943 belongs to ef=200, and ef=40 with LIMIT 200 leans on
iterative_scan to refill a candidate list five times too small — there
was no reason to assume it lands anywhere near 0.943.

The full ef ladder (40/200/400/600/800) is here rather than in
ef_at_fixed_depth.py because DECISION-2e's basis was measured 2026-07-31,
BEFORE the cascade ran, and only its ef=600 point was ever re-measured
against the rebuilt ground truth. A table with two corpora in adjacent
rows cannot support a "+4.3 points" claim, so the whole ladder is
re-measured here in one session against one corpus.
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
from api.search.fusion import HYBRID_DEFAULT_EF_SEARCH
from api.search.vector import DEFAULT_EF_SEARCH, search_vector
from bench.harness import across_runs, db_state, load_ground_truth, method_record
from bench.hnsw_recall_sweep import EXACT_SQL, tie_aware_recall, vector_literal

DEPTH = 200
K = 20
N_RUNS = 3
WARMUP_RUNS = 1
# Recall is measured at every rung of the dial; latency only at the two
# SHIPPED settings, because the decomposition question is "what does the
# configuration we run cost", not "what would every configuration cost".
RECALL_EF = (DEFAULT_EF_SEARCH, 160, 200, 400, HYBRID_DEFAULT_EF_SEARCH, 800)
# 160 is the never-decided vector-mode recommendation in progress.md; it is
# measured here so the choice can be made once instead of surviving
# another corpus change. Measuring it changes no default.
LATENCY_EF = (DEFAULT_EF_SEARCH, 160, HYBRID_DEFAULT_EF_SEARCH)


def stat(vals: list[float]) -> dict[str, float]:
    return {
        "mean": round(statistics.mean(vals), 4),
        "se": round(statistics.stdev(vals) / math.sqrt(len(vals)), 4),
    }


def main() -> None:
    out_dir = Path(__file__).parent
    all_labels, gt_method = load_ground_truth(out_dir / "labels" / "exact_top200_wide.json")
    # Distinct query strings only. The set holds "Occurrence Download" 28
    # times — a GBIF export title whose tied embeddings score ~0 at low ef —
    # and weighting it 28x moved the ef=200 mean five points and nearly
    # doubled a decision's apparent margin (findings.md 2026-08-12).
    by_query: dict[str, Any] = {}
    for entry in all_labels:
        by_query.setdefault(entry["query"], entry)
    labels = list(by_query.values())
    encoder = OnnxEncoder(os.environ["EMBED_MODEL_DIR"])

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SET statement_timeout='30min'")
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm")
        conn.execute("SELECT pg_prewarm('papers_embed_idx', 'read'), pg_prewarm('papers', 'read')")
        state = db_state(conn)
        # The ground-truth builder writes corpus_papers; db_state writes
        # corpus_size. Reading only one name is how this check printed
        # "None" instead of failing (2026-08-12). Accept both, then ASSERT:
        # a recall number measured against a different corpus than the one
        # it scores is the exact staleness that started this work.
        gt_corpus = gt_method.get("corpus_papers", gt_method.get("corpus_size"))
        assert gt_corpus == state["corpus_size"], (
            f"ground truth describes {gt_corpus} papers, live corpus has "
            f"{state['corpus_size']} — rebuild it before measuring recall"
        )

        for _ in range(3):
            encoder.encode([query_text("warmup")])
        embed_ms = []
        for entry in labels:
            t0 = time.perf_counter()
            encoder.encode([query_text(entry["query"])])
            embed_ms.append((time.perf_counter() - t0) * 1000)

        per_ef: dict[int, dict[str, Any]] = {}
        for ef in RECALL_EF:
            # Recall at depth 200: the retrieval quality this ef delivers.
            r200, r20, r10 = [], [], []
            with conn.transaction():
                conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(ef),))
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
                    r20.append(tie_aware_recall(entry["top200"], got, 20))
                    r10.append(tie_aware_recall(entry["top200"], got, 10))

            # recall@20 is the figure that belongs beside the VECTOR mode's
            # speedup: that mode serves k=20 and never requests depth 200.
            per_ef[ef] = {
                "recall_at_200": stat(r200),
                "recall_at_20": stat(r20),
                "recall_at_10": stat(r10),
            }
            if ef not in LATENCY_EF:
                continue

            # Latency at k=20: the shape the API actually serves.
            runs: list[list[float]] = []
            for run_index in range(WARMUP_RUNS + N_RUNS):
                ms = []
                for entry in labels:
                    t0 = time.perf_counter()
                    search_vector(conn, query_vec=entry["embedding"], k=K, ef_search=ef)
                    ms.append((time.perf_counter() - t0) * 1000)
                if run_index >= WARMUP_RUNS:
                    runs.append(ms)

            sql = across_runs(runs)
            e2e = across_runs([[a + b for a, b in zip(embed_ms, r, strict=True)] for r in runs])
            per_ef[ef].update(
                {
                    "sql": sql,
                    "e2e_with_embed": e2e,
                    "embed_share_of_e2e_p50": (
                        round(statistics.median(embed_ms) / e2e["p50_ms"], 3)
                        if e2e["p50_ms"]
                        else None
                    ),
                }
            )

    report = {
        "measured_at": datetime.now(UTC).isoformat(),
        "method": method_record(
            timing_window="sql: search_vector(k=20) round trip as the API calls it. "
            "e2e adds the per-query single-text encode. Recall is measured at "
            f"depth {DEPTH} in the same session but is not part of any timing window.",
            protocol=f"pg_prewarm; {len(labels)} distinct queries; per ef {WARMUP_RUNS} "
            f"warmup + {N_RUNS} measured runs, across_runs gate; both ef settings in "
            "one session so the comparison between them is paired",
            settings={
                "k": K,
                "queries": f"{len(labels)} distinct of {len(all_labels)} entries",
                "recall_depth": DEPTH,
                "recall_ef": list(RECALL_EF),
                "latency_ef": list(LATENCY_EF),
            },
            db_state=state,
            ground_truth_corpus=gt_method.get("corpus_size"),
            embed_component={"p50_ms": round(statistics.median(embed_ms), 1)},
            tie_handling="hit if id in exact top-k OR distance <= boundary + 1e-9",
            known_item_caveat="500/520 queries are corpus titles",
        ),
        "by_ef": {str(ef): per_ef[ef] for ef in RECALL_EF},
    }
    (out_dir / "results_ef_tradeoff.json").write_text(json.dumps(report, indent=2))
    print(
        f"ground truth: {gt_corpus} papers (built {gt_method.get('built_at')}), "
        f"live corpus: {state['corpus_size']} — asserted equal"
    )
    print(f"ground truth method block present: {bool(gt_method) and 'note' not in gt_method}")
    print(
        json.dumps(
            {
                str(ef): {
                    "recall@200": per_ef[ef]["recall_at_200"],
                    "recall@20": per_ef[ef]["recall_at_20"],
                    "recall@10": per_ef[ef]["recall_at_10"],
                    "sql_p50": per_ef[ef].get("sql", {}).get("p50_ms"),
                    "e2e_p50": per_ef[ef].get("e2e_with_embed", {}).get("p50_ms"),
                    "embed_share": per_ef[ef].get("embed_share_of_e2e_p50"),
                }
                for ef in RECALL_EF
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
