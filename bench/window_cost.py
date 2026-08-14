"""Does count(*) OVER () in the fusion query cost anything?

The pool size used to be a separate ef=600 vector search (11 ms). It now
rides along in HYBRID_SQL as a window function. The claim is that this is
free, because the query already materializes and sorts every candidate to
find the top k — the window adds a WindowAgg over rows already in hand.

Cross-session p50s cannot settle an effect this small: hybrid's own p50 has
moved 13.6 -> 18.2 -> 14.3 across sessions on an unchanged query. So this is
PAIRED (DECISION-3d): both variants timed back to back on the SAME query,
same connection, same session, alternating order to cancel drift; the
statistic is the per-query ratio, not a difference of medians.
"""

import argparse
import json
import os
import pathlib
import statistics
import time
from datetime import UTC, datetime

import psycopg

from api.embed.onnx_encoder import OnnxEncoder
from api.embed.texts import query_text
from api.search.fusion import HYBRID_SQL, RRF_K
from api.search.vector import vector_literal
from bench.harness import db_state, load_ground_truth

# The identical statement with the window column removed. Everything else --
# both CTEs, the join, the sort, the limit -- is byte-identical, so the only
# difference between the two timings is the WindowAgg.
NO_WINDOW_SQL = HYBRID_SQL.replace("""       count(*) OVER () AS pool_total,\n""", "")
assert "count(*) OVER ()" not in NO_WINDOW_SQL
assert len(NO_WINDOW_SQL) < len(HYBRID_SQL)


def time_one(cur: psycopg.Cursor, sql: str, params: dict) -> float:  # type: ignore[type-arg]
    t = time.perf_counter()
    cur.execute(sql, params)
    cur.fetchall()
    return (time.perf_counter() - t) * 1000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--depth", type=int, default=200)
    ap.add_argument("--k", type=int, default=20)
    args = ap.parse_args()

    encoder = OnnxEncoder(os.environ["EMBED_MODEL_DIR"])
    labels, _ = load_ground_truth(
        pathlib.Path(__file__).parent / "labels" / "exact_top200_wide.json"
    )
    queries = [r["query"] for r in labels][: args.n]
    ratios, with_ms, without_ms = [], [], []

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm")
        conn.execute("SELECT pg_prewarm('papers_embed_idx', 'read'), pg_prewarm('papers', 'read')")
        state = db_state(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('hnsw.ef_search', '600', false)")
            cur.execute("SELECT set_config('hnsw.iterative_scan', 'strict_order', false)")
            for i, q in enumerate(queries):
                p = {
                    "query": q,
                    "qv": vector_literal(encoder.encode([query_text(q)])[0]),
                    "k": args.k,
                    "depth": args.depth,
                    "rrf_k": RRF_K,
                    "year_from": None,
                    "year_to": None,
                }
                time_one(cur, HYBRID_SQL, p)  # warm this query's pages for BOTH
                # Alternate which variant goes first so any within-pair drift
                # cancels instead of always favouring the second.
                if i % 2 == 0:
                    a = time_one(cur, HYBRID_SQL, p)
                    b = time_one(cur, NO_WINDOW_SQL, p)
                else:
                    b = time_one(cur, NO_WINDOW_SQL, p)
                    a = time_one(cur, HYBRID_SQL, p)
                with_ms.append(a)
                without_ms.append(b)
                ratios.append(a / b)

    ratios.sort()
    n = len(ratios)
    out = {
        "measured_at": datetime.now(UTC).isoformat(),
        "method": {
            "design": "paired, same connection/session/query, alternating order",
            "statistic": "per-query ratio with_window / without_window",
            "n_queries": n,
            "settings": {"depth": args.depth, "k": args.k, "ef_search": 600},
            "prepared_statements": (
                "both variants are distinct SQL texts, so each gets its OWN "
                "prepared statement and generic plan after psycopg's threshold "
                "-- no plan is shared or reused across the two arms, which is "
                "the trap that voided an earlier measurement (findings.md "
                "2026-08-12)"
            ),
            "db_state": state,
        },
        "ratio": {
            "p50": round(statistics.median(ratios), 4),
            "p05": round(ratios[int(0.05 * n)], 4),
            "p95": round(ratios[int(0.95 * n)], 4),
            "mean": round(statistics.fmean(ratios), 4),
        },
        "with_window_ms": {"p50": round(statistics.median(with_ms), 2)},
        "without_window_ms": {"p50": round(statistics.median(without_ms), 2)},
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
