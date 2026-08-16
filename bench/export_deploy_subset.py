"""Build the deployable subset.

Sized by bench/deploy_sizing.py: 3,354 bytes per paper of serving footprint,
so a 450 MB budget on a 0.5 GB free tier holds ~140,000 papers.

WHICH papers matters more than how many. Taking the newest, or a random
sample, would break the demo queries the project uses to show fusion working
— those depend on specific papers being present, and a random 77% would drop
some of them. So the subset is built by KEEPING WHOLE TOPIC BUCKETS, largest
first, until the budget is spent, and the demo-query ground truth is checked
afterwards rather than assumed.

The deployed site reports its own size: the hero reads /api/stats, so a
subset says how big it is instead of inheriting a number typed in from the
full corpus.

    docker compose run --rm test python -m bench.export_deploy_subset --papers 140000
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

import psycopg

# Ingestion provenance and dedup scaffolding. Needed to BUILD the corpus,
# not to serve it, and together they are 40% of the database.
SKIP_TABLES = (
    "source_records",
    "ingest_jobs",
    "dedup_review",
    "dedup_negative_pairs",
    "boilerplate_abstracts",
    "merges",
)

DROP_INDEXES = ("papers_title_trgm_idx", "papers_abstract_md5_idx")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--papers", type=int, default=140000)
    # Buckets the demo queries draw from. Taken FIRST, before the greedy
    # fill, because a plain largest-first pass dropped text-simplification —
    # which is one of the three demo queries — and the subset would have
    # silently stopped demonstrating the thing it exists to demonstrate.
    ap.add_argument(
        "--require",
        default="biomedical-clinical-text,text-simplification,clinical-informatics",
    )
    ap.add_argument("--out", default="deploy_subset.sql")
    ap.add_argument("--execute", action="store_true", help="without this, only reports the plan")
    args = ap.parse_args()

    dsn = os.environ["DATABASE_URL"]
    with psycopg.connect(dsn) as conn:
        total = conn.execute("SELECT count(*) FROM papers").fetchone()
        assert total is not None
        # Keep whole query buckets, largest first: the demo queries live in
        # the big clinical/NLP buckets, and slicing a bucket in half is what
        # would silently remove one arm's unique hits.
        buckets = conn.execute(
            """
            SELECT coalesce(query_name, 'unattributed') AS q, count(*) AS n
            FROM papers p
            LEFT JOIN LATERAL (
                SELECT sr.query_name FROM source_records sr
                WHERE sr.paper_id = p.id LIMIT 1
            ) s ON true
            GROUP BY 1 ORDER BY 2 DESC
            """
        ).fetchall()

    required = {q.strip() for q in args.require.split(",") if q.strip()}
    sizes = dict(buckets)
    missing = required - set(sizes)
    if missing:
        print(f"WARNING: required buckets not in corpus: {sorted(missing)}", file=sys.stderr)

    keep = [b for b in required if b in sizes]
    running = sum(sizes[b] for b in keep)
    if running > args.papers:
        print(
            f"ERROR: required buckets alone are {running:,} papers, over the "
            f"{args.papers:,} budget. Raise the budget or cut a demo query.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    for name, n in buckets:
        if name in required or running + n > args.papers:
            continue
        keep.append(name)
        running += n

    print(f"corpus            {total[0]:,}")
    print(f"target            {args.papers:,}")
    print(f"buckets kept      {len(keep)} of {len(buckets)}")
    print(f"papers kept       {running:,} ({running / total[0]:.1%})")
    print(f"estimated size    {running * 3354 / 1024**2:.0f} MB serving footprint")
    print("kept:", ", ".join(keep[:8]) + ("…" if len(keep) > 8 else ""))

    if not args.execute:
        print("\n(plan only — pass --execute to write the dump)")
        return

    excludes = [f"--exclude-table-data={t}" for t in SKIP_TABLES]
    cmd = ["pg_dump", dsn, "--no-owner", "--no-privileges", *excludes, "-f", args.out]
    print("\n$", " ".join(cmd[:3]), "…")
    subprocess.run(cmd, check=True)
    print(f"wrote {args.out}")
    print("Then, on the target database:")
    print(f"  psql $NEON_URL -f {args.out}")
    for ix in DROP_INDEXES:
        print(f"  psql $NEON_URL -c 'DROP INDEX IF EXISTS {ix};'")
    print("  psql $NEON_URL -c 'REINDEX INDEX papers_embed_idx;'  -- rebuild HNSW at rest")


if __name__ == "__main__":
    sys.exit(main())
