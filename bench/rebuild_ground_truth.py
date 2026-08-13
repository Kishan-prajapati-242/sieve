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
import pathlib
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


# Refreshing the query set draws new corpus titles the same deterministic
# way the original did: evenly across the id range, so a post-pull draw
# includes the new sources in proportion to their share.
REFRESH_SQL = """
SELECT title FROM (
    SELECT title, row_number() OVER (ORDER BY id) AS rn, count(*) OVER () AS total
    FROM papers WHERE title_norm <> '' AND length(title) >= 20
) t
WHERE rn %% (total / %(n)s) = 1
LIMIT %(n)s
"""


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--refresh-queries",
        action="store_true",
        help="draw a NEW query set from the current corpus instead of reusing the "
        "stored one. Run BOTH after a corpus change: the reused set isolates the "
        "corpus effect, the refreshed set represents the corpus as it now is, and "
        "comparing only one of them confounds the two (findings.md 2026-08-13).",
    )
    ap.add_argument("--out", default=None, help="write somewhere other than the default")
    args = ap.parse_args()

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

        if args.refresh_queries:
            # A refreshed set needs its own embeddings; reuse is the point of
            # the other mode, so this branch is only for the second run.
            from api.embed.onnx_encoder import OnnxEncoder
            from api.embed.texts import query_text

            encoder = OnnxEncoder(os.environ["EMBED_MODEL_DIR"])
            titles = [t for (t,) in conn.execute(REFRESH_SQL, {"n": len(old_queries)}).fetchall()]
            vecs = encoder.encode([query_text(t) for t in titles])
            source_entries = [
                {"query": t, "embedding": [float(x) for x in v]}
                for t, v in zip(titles, vecs, strict=True)
            ]
            print(f"refreshed query set: {len(source_entries)} titles from the current corpus")
        else:
            source_entries = old_queries

        start = time.perf_counter()
        queries = []
        for entry in source_entries:
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
    out_path = pathlib.Path(args.out) if args.out else OUT
    out_path.write_text(json.dumps(payload))
    print(f"rebuilt {len(queries)} queries against {corpus[0]:,} papers in {elapsed:.0f}s")
    print(f"superseded file kept at {SUPERSEDED.name}")


if __name__ == "__main__":
    main()
