"""Exact-scan ground truth + latency baseline for the HNSW recall sweep.

Run BEFORE the HNSW index exists (Kishan, 2026-07-31): exact-scan latency
is the denominator of any HNSW speedup claim, and it is only cleanly
measurable pre-index — afterward you would be forcing the planner off the
index against a cache full of index pages. The top-50 ground truth feeds
bench/hnsw_recall_sweep.py regardless of when it runs.

Emits:
  bench/labels/exact_top50.json   — per query: text, domain, embedding,
                                    exact top-50 (paper id + cosine distance).
                                    Self-contained: stores the query vectors
                                    so the sweep never depends on re-embedding.
  bench/results_exact_scan.json   — latency stats + per-query timings +
                                    one EXPLAIN ANALYZE showing the seq scan.

Run inside the compose environment (host numbers don't transfer):
    docker compose run --rm --no-deps -v ./bench:/app/bench \
        -v ./models/bge-small-en-v1.5:/models -e EMBED_MODEL_DIR=/models \
        -e DATABASE_URL=postgresql://sieve:sieve@postgres:5432/sieve \
        test python bench/exact_scan_baseline.py
"""

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

# Four queries per domain, five domains (Kishan's eval domains, 2026-07-31).
# These double as the seed of the Phase 4 eval set.
QUERIES: list[tuple[str, str]] = [
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

EXACT_SQL = """
SELECT id, (embedding <=> %(q)s::halfvec)::float8 AS distance
FROM papers
ORDER BY embedding <=> %(q)s::halfvec
LIMIT 50
"""


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in vec) + "]"


def pct(sorted_xs: list[float], p: float) -> float:
    return sorted_xs[min(len(sorted_xs) - 1, int(p * len(sorted_xs)))]


def main() -> None:
    out_dir = Path(__file__).parent
    encoder = OnnxEncoder(os.environ["EMBED_MODEL_DIR"])
    embeddings = encoder.encode([query_text(q) for _, q in QUERIES])

    labels: list[dict[str, object]] = []
    timings_ms: list[float] = []
    rows_scanned: list[int] = []
    explain_text = ""

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        index_check = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename='papers' AND indexdef ILIKE '%hnsw%'"
        ).fetchall()
        if index_check:
            raise SystemExit(f"HNSW index already exists ({index_check}); baseline must run first")

        # Warm the cache with one throwaway scan so query 1 isn't measuring
        # cold disk reads that no other query pays.
        conn.execute(EXACT_SQL, {"q": vector_literal(list(embeddings[0]))}).fetchall()

        for (domain, text), emb in zip(QUERIES, embeddings, strict=True):
            q = vector_literal([float(x) for x in emb])
            start = time.perf_counter()
            top = conn.execute(EXACT_SQL, {"q": q}).fetchall()
            elapsed_ms = (time.perf_counter() - start) * 1000
            timings_ms.append(elapsed_ms)
            labels.append(
                {
                    "domain": domain,
                    "query": text,
                    "embedding": [float(x) for x in emb],
                    "top50": [{"id": pid, "distance": dist} for pid, dist in top],
                }
            )

            plan = "\n".join(
                r[0]
                for r in conn.execute(
                    f"EXPLAIN (ANALYZE, BUFFERS) {EXACT_SQL}",
                    {"q": q},  # noqa: S608
                ).fetchall()
            )
            assert "Seq Scan on papers" in plan, "baseline must be a sequential scan"
            # Postgres parallelizes the scan (2 workers + leader) and EXPLAIN
            # reports the Seq Scan node's rows PER PROCESS — total scanned is
            # rows x loops, not rows (65,631 x 3 = the whole table).
            m = re.search(
                r"Seq Scan on papers.*?actual time=[\d.]+\.\.[\d.]+ rows=(\d+)(?:\.\d+)? "
                r"loops=(\d+)",
                plan,
            )
            assert m is not None
            rows_scanned.append(int(m.group(1)) * int(m.group(2)))
            if not explain_text:
                explain_text = plan

    ordered = sorted(timings_ms)
    results = {
        "measured_at": datetime.now(UTC).isoformat(),
        "environment": "compose test container (podman VM), pgvector exact scan, no index",
        "model": "BAAI/bge-small-en-v1.5 fp32 ONNX, query_text() prefix applied",
        "n_queries": len(QUERIES),
        "note": "percentiles over 20 queries: p99 is effectively the max",
        "latency_ms": {
            "p50": round(pct(ordered, 0.50), 1),
            "p95": round(pct(ordered, 0.95), 1),
            "p99": round(pct(ordered, 0.99), 1),
            "mean": round(statistics.mean(ordered), 1),
        },
        "mean_rows_scanned": int(statistics.mean(rows_scanned)),
        "per_query_ms": {
            text: round(ms, 1) for (_, text), ms in zip(QUERIES, timings_ms, strict=True)
        },
        "explain_analyze_example": explain_text,
    }

    (out_dir / "labels").mkdir(exist_ok=True)
    (out_dir / "labels" / "exact_top50.json").write_text(json.dumps(labels, indent=1))
    (out_dir / "results_exact_scan.json").write_text(json.dumps(results, indent=2))
    summary = {k: v for k, v in results.items() if k != "explain_analyze_example"}
    print(json.dumps(summary, indent=2))
    print("\nground truth: bench/labels/exact_top50.json")


if __name__ == "__main__":
    main()
