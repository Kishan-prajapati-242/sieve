"""Regenerate exact top-200 ground truth against the CURRENT corpus.

The previous file described 196,893 papers; dedup left 183,167. Measured
before regenerating: 5,528 of 67,769 referenced ids (8.2%) no longer
exist, 99.0% of queries had at least one dead id in their top-200, and
47.5% had one in their TOP-10 — so every recall number computed against it
was scored against a ruler with holes in the region that matters most.

Same 520 queries and the same stored query vectors as the superseded file,
so the new numbers are comparable to the old ones rather than merely
newer. Forced sequential scan, EXPLAIN-verified, recorded in the method
block. The old file is kept beside the new one, renamed, not deleted.
"""

import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from bench.harness import load_ground_truth, method_record

OUT = Path(__file__).parent / "labels" / "exact_top200_wide.json"
SUPERSEDED = Path(__file__).parent / "labels" / "exact_top200_wide.superseded_196893.json"

EXACT_SQL = """
SELECT id, (embedding <=> %(q)s::halfvec)::float8 AS distance
FROM papers
ORDER BY embedding <=> %(q)s::halfvec
LIMIT 200
"""


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in vec) + "]"


def main() -> None:
    old_queries, _ = load_ground_truth(OUT)
    if not SUPERSEDED.exists():
        shutil.copy(OUT, SUPERSEDED)

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SET statement_timeout='40min'")
        corpus = conn.execute("SELECT count(*) FROM papers").fetchone()
        assert corpus is not None

        conn.execute("SET enable_indexscan = off")
        conn.execute("SET enable_bitmapscan = off")
        probe = vector_literal(old_queries[0]["embedding"])
        plan = "\n".join(
            r[0]
            for r in conn.execute(f"EXPLAIN (ANALYZE) {EXACT_SQL}", {"q": probe})  # noqa: S608
        )
        assert "Seq Scan on papers" in plan, plan
        assert "papers_embed_idx" not in plan, plan

        start = time.perf_counter()
        queries = []
        for entry in old_queries:
            rows = conn.execute(EXACT_SQL, {"q": vector_literal(entry["embedding"])}).fetchall()
            queries.append(
                {
                    "query": entry["query"],
                    "embedding": entry["embedding"],
                    "top200": [{"id": pid, "distance": d} for pid, d in rows],
                }
            )
        elapsed = time.perf_counter() - start

    payload = {
        "method": method_record(
            timing_window="n/a — this file is ground truth, not a latency measurement",
            corpus_papers=corpus[0],
            built_at=datetime.now(UTC).isoformat(),
            queries=len(queries),
            depth=200,
            scan="forced sequential (enable_indexscan/bitmapscan off), EXPLAIN-verified",
            explain_head="\n".join(plan.splitlines()[:4]),
            build_seconds=round(elapsed),
            supersedes=(
                "exact_top200_wide.superseded_196893.json — built against 196,893 papers; "
                "8.2% of its referenced ids were deleted by the dedup cascade, and 47.5% "
                "of queries had a dead id in their top-10"
            ),
            query_vectors="reused verbatim from the superseded file, so the new numbers "
            "are comparable rather than merely newer",
        ),
        "queries": queries,
    }
    OUT.write_text(json.dumps(payload))
    print(f"rebuilt {len(queries)} queries against {corpus[0]:,} papers in {elapsed:.0f}s")
    print(f"superseded file kept at {SUPERSEDED.name}")


if __name__ == "__main__":
    main()
