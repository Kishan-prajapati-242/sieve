"""How much of the corpus fits in a free-tier Postgres, measured not guessed.

The deploy constraint is storage, and it is decided by three facts about this
corpus (measured 2026-08-15 on 183,167 papers):

    papers heap                276 MB
    papers_embed_idx (HNSW)    204 MB
    papers_fts_idx (GIN)        83 MB
    dedup-only indexes          49 MB   <- not needed to SERVE queries
    source_records             820 MB   <- ingestion provenance, not needed
    ---------------------------------
    total database            2067 MB

A deployment has to answer queries, not re-run ingestion. Dropping
`source_records` and the three dedup-only indexes (title_trgm, abstract_md5,
doi_key is kept for integrity) takes the serving footprint to roughly 590 MB
— which still does not fit the 0.5 GB free tiers, so the corpus is subset.

This script reports what actually fits, and it computes the per-paper cost
from live measurements rather than from an assumed row size, because the
abstract length distribution is the dominant term and it is not uniform.

    docker compose run --rm test python -m bench.deploy_sizing --budget-mb 450
"""

from __future__ import annotations

import argparse
import json
import os

import psycopg

# Indexes a query-serving deployment needs. Everything else exists for the
# dedup cascade, which runs before deployment and not during it.
SERVING_INDEXES = ("papers_pkey", "papers_embed_idx", "papers_fts_idx", "papers_title_year_idx")
DROPPABLE = ("papers_title_trgm_idx", "papers_abstract_md5_idx")

SIZES_SQL = """
SELECT indexrelname, pg_relation_size(indexrelid)
FROM pg_stat_user_indexes WHERE relname = 'papers'
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--budget-mb",
        type=float,
        default=450.0,
        help="usable storage for the papers table; leave headroom under the tier limit",
    )
    args = ap.parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        row = conn.execute("SELECT count(*), pg_relation_size('papers') FROM papers").fetchone()
        assert row is not None
        n_papers, heap = int(row[0]), int(row[1])
        idx = {name: int(size) for name, size in conn.execute(SIZES_SQL).fetchall()}

    mb = 1024 * 1024
    serving_idx = sum(v for k, v in idx.items() if k in SERVING_INDEXES)
    dropped = sum(v for k, v in idx.items() if k in DROPPABLE)
    serving_total = heap + serving_idx

    per_paper = serving_total / n_papers
    fits = int((args.budget_mb * mb) / per_paper)

    out = {
        "measured_at_corpus": n_papers,
        "heap_mb": round(heap / mb, 1),
        "serving_indexes_mb": round(serving_idx / mb, 1),
        "dropped_indexes_mb": round(dropped / mb, 1),
        "serving_total_mb": round(serving_total / mb, 1),
        "bytes_per_paper": round(per_paper),
        "budget_mb": args.budget_mb,
        "papers_that_fit": fits,
        "fraction_of_corpus": round(fits / n_papers, 3),
        "note": (
            "Per-paper cost is derived from the live heap and index sizes, so it "
            "already reflects this corpus's abstract-length distribution. HNSW is "
            "the largest single term and scales with row count, so the subset "
            "shrinks it proportionally."
        ),
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
