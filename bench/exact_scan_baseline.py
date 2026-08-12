"""Exact-scan latency baseline + top-50 ground truth for the recall sweep.

v2 (2026-07-31): the first version computed p95/p99 from 20 samples — a
max labeled as a percentile — and blended cold- and warm-cache runs
(findings.md). This version measures on bench/harness.py rules:

  Warm run: 120 distinct queries (Kishan's 20 eval queries + 100 paper
  titles sampled deterministically across the corpus — repeated identical
  queries measure cache, not latency) x 5 interleaved repetitions = 600
  samples, after 3 discarded warmup scans. 600 puts 6 samples beyond the
  nearest-rank p99 (harness rule: >=5), 30 beyond p95.

  Cold run: cannot be forced from inside the container (dropping the VM
  page cache and restarting postgres are host operations), so cold cycles
  are orchestrated from the host — per cycle: restart postgres, drop the
  VM page cache, then `--cold-single N` runs ONE query with no warmup.
  Few samples by nature: reported as raw values + median, never
  percentiles.

The HNSW index now exists, so every run forces the sequential scan
(enable_indexscan/enable_bitmapscan off), verifies it via EXPLAIN, and
records scan_forced in the method block. --capture-labels regenerates
bench/labels/exact_top50.json (forced seq scan = still exact).

Host-side runbook:

  cold cycles (from repo root):
    for i in 0 1 2 3 4 5; do
      docker compose restart postgres && sleep 5
      podman machine ssh 'sudo sh -c "sync; echo 3 > /proc/sys/vm/drop_caches"'
      docker compose run --rm --no-deps -v ./bench:/app/bench \
        -v ./models/bge-small-en-v1.5:/models -e EMBED_MODEL_DIR=/models \
        -e DATABASE_URL=postgresql://sieve:sieve@postgres:5432/sieve \
        test python -m bench.exact_scan_baseline --cold-single $i >> cold.jsonl
    done

  warm run (embeds --cold-samples into the results file):
    docker compose run ... test python -m bench.exact_scan_baseline \
        --cold-samples cold.jsonl
"""

import argparse
import json
import os
import re
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from api.embed.onnx_encoder import OnnxEncoder
from api.embed.texts import query_text
from bench.harness import (
    across_runs,
    carry_superseded,
    db_state,
    interleaved,
    method_record,
    state_key,
)

# Kishan's 20 eval queries (4 x 5 domains, 2026-07-31). Also the ground-truth
# query set; these double as the seed of the Phase 4 eval set.
EVAL_QUERIES: list[tuple[str, str]] = [
    ("clinical-text-simplification", "simplifying clinical notes for patients"),
    ("clinical-text-simplification", "text simplification of electronic health records"),
    ("clinical-text-simplification", "automatic simplification of medical documents"),
    ("clinical-text-simplification", "generating patient-friendly discharge summaries"),
    ("mental-health-nlp", "detecting depression from social media posts"),
    ("mental-health-nlp", "suicide risk prediction from clinical text"),
    ("mental-health-nlp", "emotion detection in counseling conversations"),
    ("mental-health-nlp", "language markers of anxiety disorders"),
    ("biomedical-ner", "biomedical named entity recognition"),
    ("biomedical-ner", "drug and disease entity extraction from literature"),
    ("biomedical-ner", "chemical entity recognition in scientific text"),
    ("biomedical-ner", "gene mention normalization"),
    ("patient-education-readability", "readability of patient education materials"),
    ("patient-education-readability", "health literacy assessment of online medical information"),
    ("patient-education-readability", "plain language summaries of medical research"),
    ("patient-education-readability", "readability formulas for health texts"),
    ("general-nlp", "transformer language models for text classification"),
    ("general-nlp", "cross-lingual transfer learning"),
    ("general-nlp", "abstractive summarization of scientific papers"),
    ("general-nlp", "question answering over long documents"),
]

N_TITLE_QUERIES = 100
REPS = 5
WARMUP = 3
N_RUNS = 3

EXACT_SQL = """
SELECT id, (embedding <=> %(q)s::halfvec)::float8 AS distance
FROM papers
ORDER BY embedding <=> %(q)s::halfvec
LIMIT 50
"""

# Deterministic spread of real titles across the id range: same 100 titles
# every run, no randomness to explain away.
TITLE_SAMPLE_SQL = """
SELECT title FROM (
    SELECT title, row_number() OVER (ORDER BY id) AS rn,
           count(*) OVER () AS total
    FROM papers
) t
WHERE rn %% (total / %(n)s) = 1
LIMIT %(n)s
"""


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in vec) + "]"


def force_seq_scan(conn: psycopg.Connection, probe_vec: str) -> str:
    """Disable index paths for this session and prove it with EXPLAIN."""
    conn.execute("SET enable_indexscan = off")
    conn.execute("SET enable_bitmapscan = off")
    plan = "\n".join(
        r[0]
        for r in conn.execute(f"EXPLAIN (ANALYZE, BUFFERS) {EXACT_SQL}", {"q": probe_vec})  # noqa: S608
    )
    assert "Seq Scan on papers" in plan, f"expected forced seq scan, got:\n{plan}"
    assert "papers_embed_idx" not in plan
    return plan


def timed(conn: psycopg.Connection, q: str) -> float:
    start = time.perf_counter()
    conn.execute(EXACT_SQL, {"q": q}).fetchall()
    return (time.perf_counter() - start) * 1000


def cold_single(index: int) -> None:
    """One query, no warmup, straight after a host-side cache flush."""
    encoder = OnnxEncoder(os.environ["EMBED_MODEL_DIR"])
    domain, text = EVAL_QUERIES[index % len(EVAL_QUERIES)]
    emb = encoder.encode([query_text(text)])[0]
    q = vector_literal([float(x) for x in emb])
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("SET enable_indexscan = off")
        conn.execute("SET enable_bitmapscan = off")
        ms = timed(conn, q)
        plan = "\n".join(
            r[0]
            for r in conn.execute(f"EXPLAIN {EXACT_SQL}", {"q": q})  # noqa: S608
        )
    assert "Seq Scan on papers" in plan
    print(json.dumps({"query": text, "domain": domain, "cold_ms": round(ms, 1)}))


def capture_labels(conn: psycopg.Connection, encoder: OnnxEncoder, out_dir: Path) -> None:
    labels = []
    for (domain, text), emb in zip(
        EVAL_QUERIES, encoder.encode([query_text(t) for _, t in EVAL_QUERIES]), strict=True
    ):
        top = conn.execute(EXACT_SQL, {"q": vector_literal([float(x) for x in emb])}).fetchall()
        labels.append(
            {
                "domain": domain,
                "query": text,
                "embedding": [float(x) for x in emb],
                "top50": [{"id": pid, "distance": dist} for pid, dist in top],
            }
        )
    (out_dir / "labels").mkdir(exist_ok=True)
    (out_dir / "labels" / "exact_top50.json").write_text(json.dumps(labels, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cold-single", type=int, default=None, metavar="I")
    parser.add_argument("--cold-samples", type=Path, default=None, help="jsonl from cold cycles")
    parser.add_argument("--capture-labels", action="store_true")
    args = parser.parse_args()

    if args.cold_single is not None:
        cold_single(args.cold_single)
        return

    out_dir = Path(__file__).parent
    encoder = OnnxEncoder(os.environ["EMBED_MODEL_DIR"])

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        titles = [t for (t,) in conn.execute(TITLE_SAMPLE_SQL, {"n": N_TITLE_QUERIES}).fetchall()]
        texts = [text for _, text in EVAL_QUERIES] + titles
        vecs = [
            vector_literal([float(x) for x in e])
            for e in encoder.encode([query_text(t) for t in texts])
        ]

        explain = force_seq_scan(conn, vecs[0])
        state = db_state(conn)
        workers = re.search(r"Workers Launched: (\d+)", explain)

        if args.capture_labels:
            capture_labels(conn, encoder, out_dir)

        for _ in range(WARMUP):
            timed(conn, vecs[0])

        # Multi-run (2026-07-31): a single run's tail is one draw from this
        # VM's environmental noise; across_runs gates any percentile that
        # doesn't reproduce to a range instead of a point estimate.
        runs = [[timed(conn, vecs[i]) for i in interleaved(len(vecs), REPS)] for _ in range(N_RUNS)]

    warm = across_runs(runs)
    cold: dict[str, object] = {"note": "no cold cycles supplied"}
    if args.cold_samples and args.cold_samples.exists():
        cold_values = [
            json.loads(line)["cold_ms"] for line in args.cold_samples.read_text().splitlines()
        ]
        cold = {
            "n_samples": len(cold_values),
            "values_ms": cold_values,
            "median_ms": round(statistics.median(cold_values), 1),
            "note": (
                "one query per cycle: postgres restarted + VM page cache dropped "
                "(host-orchestrated; not forceable from inside the container). "
                "Too few samples for percentiles, by design."
            ),
        }

    # Prior published numbers stay visible, marked superseded. The v1 and v2
    # blocks were format corrections; from 2026-08-02 a corpus change also
    # supersedes, since latency measured against a different number of rows
    # is a different measurement (dedup removed 13,821 papers).
    prior: dict[str, object] = {}
    results_path = out_dir / "results_exact_scan.json"
    if results_path.exists():
        old = json.loads(results_path.read_text())
        old_state = old.get("method", {}).get("db_state")
        if "method" not in old:  # v1 format
            old.pop("explain_analyze_example", None)
            prior = carry_superseded(
                old,
                key="superseded_v1",
                why="p95/p99 computed from 20 samples are the max wearing a "
                "percentile's name, and cold- and warm-cache samples were blended "
                "(1.6x spread on identical work) — findings.md 2026-07-31",
                keep=tuple(k for k in old if not k.startswith("superseded_")),
            )
        elif "n_runs" not in old.get("warm", {}):  # v2: single-run percentiles
            prior = carry_superseded(
                old,
                key="superseded_v2",
                why="single-run p99 (95.8) published from the favorable end of "
                "an observed 95.8-406.9 spread across same-day runs — same species "
                "as the 20-sample p99. Percentiles now need to reproduce across "
                "runs or they report as a range (findings.md 2026-07-31)",
                keep=("measured_at", "warm", "cold"),
            )
        elif old_state != state:
            prior = carry_superseded(
                old,
                key=state_key(old_state) if old_state else "superseded_unrecorded_state",
                why=f"measured against {old_state or 'an unrecorded database state'}; "
                f"this run measured {state}",
                keep=("measured_at", "warm", "cold"),
            )
        else:
            prior = carry_superseded(old)

    results = {
        "measured_at": datetime.now(UTC).isoformat(),
        "method": method_record(
            timing_window="psycopg execute()+fetchall() round trip over the compose "
            "network: SQL execution (forced parallel seq scan) + transfer of 50 "
            "(id, distance) rows. Query embedding and vector-literal formatting "
            "happen BEFORE the window — precomputed for all queries up front.",
            queries=f"{len(EVAL_QUERIES)} eval queries + {len(titles)} corpus titles "
            "(deterministic id-spread), embedded via query_text() prefix",
            n_distinct_queries=len(vecs),
            repetitions=REPS,
            interleaved=True,
            warmup_discarded=WARMUP,
            cache_state="warm (see cold block for cold-cache values)",
            scan_forced="enable_indexscan=off, enable_bitmapscan=off "
            "(HNSW index exists; verified Seq Scan via EXPLAIN)",
            p99_stability="p99 does not reproduce across runs on this hardware "
            "(fanless VM: environmental tail noise; see warm.per_run and "
            "findings.md 2026-07-31). Before/after latency claims should target "
            "p50/p95; any p99 claim needs the multi-run range, never a point.",
            parallel_seq_scan_workers=int(workers.group(1)) if workers else 0,
            db_state=state,
        ),
        "warm": warm,
        "cold": cold,
        "explain_analyze_example": explain,
        **prior,
    }

    results_path.write_text(json.dumps(results, indent=2))
    summary = {k: v for k, v in results.items() if k != "explain_analyze_example"}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
