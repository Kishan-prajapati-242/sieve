"""Stream the deployable subset into the target database.

Replaces the pg_dump approach in export_deploy_subset.py, which had a real
gap: it SELECTED buckets and then dumped the whole `papers` table anyway, so
the subset was reported but never applied. This does the filtering where it
has to happen — in the query that reads the rows.

Streaming with COPY rather than a dump file because the vectors are the bulk
of the payload and a 400 MB intermediate file on disk buys nothing. Batched
so a dropped connection costs one batch, not the whole load.

Indexes are created AFTER the data lands. Building HNSW incrementally as
183k rows arrive is dramatically slower than building it once at rest, and
the index is the single largest object in the deployment.

    docker compose run --rm -e NEON_URL=... test python -m bench.load_neon --papers 140000
"""

from __future__ import annotations

import argparse
import os
import time

import psycopg

BATCH = 2000

# Columns that exist to serve queries. `fts` is a generated column and is
# recomputed by the target's own DDL, so it is not copied.
COLS = (
    "id, doi, title, title_norm, abstract, year, venue, citation_count, "
    "is_retracted, authors, arxiv_id, pubmed_id, embedding"
)

BUCKET_SQL = """
SELECT coalesce(s.query_name, 'unattributed') AS q, count(*) AS n
FROM papers p
LEFT JOIN LATERAL (
    SELECT sr.query_name FROM source_records sr WHERE sr.paper_id = p.id LIMIT 1
) s ON true
GROUP BY 1 ORDER BY 2 DESC
"""

# Proportional slice WITHIN each kept bucket, most-cited first.
#
# Taking whole buckets was right when the budget held them; Neon's real
# ceiling is 512 MB INCLUDING WAL history, which is roughly half what a local
# measurement predicts, and the three demo-query buckets alone are 91,527
# papers. So each bucket contributes its share and the demo queries all keep
# representation, rather than one of them being dropped entirely.
#
# Ordered by citation_count so the slice keeps the papers a reviewer is
# likely to recognise, and papers WITH embeddings only — a row without one is
# invisible to vector and hybrid mode and would just consume budget.
IDS_SQL = """
WITH tagged AS (
    SELECT p.id,
           coalesce(s.query_name, 'unattributed') AS q,
           row_number() OVER (
               PARTITION BY coalesce(s.query_name, 'unattributed')
               ORDER BY p.citation_count DESC NULLS LAST, p.id
           ) AS rn
    FROM papers p
    LEFT JOIN LATERAL (
        SELECT sr.query_name FROM source_records sr WHERE sr.paper_id = p.id LIMIT 1
    ) s ON true
    WHERE p.embedding IS NOT NULL
)
SELECT id FROM tagged
WHERE q = ANY(%(buckets)s) AND rn <= %(per_bucket)s
"""

# Serving indexes only. The dedup cascade's indexes (title_trgm,
# abstract_md5) are 49 MB and are never touched by a query path.
INDEXES = [
    (
        "papers_embed_idx",
        "CREATE INDEX papers_embed_idx ON papers USING hnsw (embedding halfvec_cosine_ops)",
    ),
    ("papers_fts_idx", "CREATE INDEX papers_fts_idx ON papers USING gin (fts)"),
    ("papers_title_year_idx", "CREATE INDEX papers_title_year_idx ON papers (title_norm, year)"),
    ("papers_year_idx", "CREATE INDEX papers_year_idx ON papers (year)"),
    (
        "papers_pubmed_id_idx",
        "CREATE INDEX papers_pubmed_id_idx ON papers (pubmed_id) WHERE pubmed_id IS NOT NULL",
    ),
    (
        "papers_arxiv_id_idx",
        "CREATE INDEX papers_arxiv_id_idx ON papers (arxiv_id) WHERE arxiv_id IS NOT NULL",
    ),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--papers", type=int, default=140000)
    ap.add_argument(
        "--require", default="biomedical-clinical-text,text-simplification,clinical-informatics"
    )
    ap.add_argument("--skip-copy", action="store_true", help="only (re)build indexes")
    args = ap.parse_args()

    src_dsn, dst_dsn = os.environ["DATABASE_URL"], os.environ["NEON_URL"]

    with psycopg.connect(src_dsn) as src:
        buckets = src.execute(BUCKET_SQL).fetchall()
        sizes = dict(buckets)
        required = [q.strip() for q in args.require.split(",") if q.strip() and q.strip() in sizes]
        # Every bucket with real volume stays represented; tiny ones would
        # contribute noise, not coverage.
        keep = [b for b, n in buckets if n >= 200 or b in required]
        per_bucket = max(1, args.papers // max(1, len(keep)))
        print(
            f"{len(keep)} buckets, up to {per_bucket:,} each "
            f"(most-cited first, embedded only)",
            flush=True,
        )

        with psycopg.connect(dst_dsn) as dst:
            if not args.skip_copy:
                # Indexes are dropped before the load and rebuilt after: HNSW
                # built incrementally over 128k inserts is far slower than
                # built once at rest, and it is the largest object here.
                for name, _ in INDEXES:
                    dst.execute(f"DROP INDEX IF EXISTS {name}")
                dst.execute("TRUNCATE papers CASCADE")
                dst.commit()

                ids = [
                    r[0]
                    for r in src.execute(
                        IDS_SQL, {"buckets": keep, "per_bucket": per_bucket}
                    ).fetchall()
                ]
                print(f"{len(ids):,} ids selected", flush=True)

                t0 = time.perf_counter()
                done = 0
                for i in range(0, len(ids), BATCH):
                    chunk = ids[i : i + BATCH]
                    rows = src.execute(
                        f"SELECT {COLS} FROM papers WHERE id = ANY(%s)", (chunk,)
                    ).fetchall()
                    with dst.cursor().copy(f"COPY papers ({COLS}) FROM STDIN") as cp:
                        for r in rows:
                            cp.write_row(r)
                    dst.commit()
                    done += len(rows)
                    if (i // BATCH) % 10 == 0:
                        rate = done / (time.perf_counter() - t0)
                        print(f"  {done:,}/{len(ids):,}  {rate:.0f} rows/s", flush=True)
                print(f"copied {done:,} in {time.perf_counter() - t0:.0f}s", flush=True)

            for name, ddl in INDEXES:
                t = time.perf_counter()
                dst.execute("SET maintenance_work_mem = '1GB'")
                dst.execute(f"DROP INDEX IF EXISTS {name}")
                dst.execute(ddl)
                dst.commit()
                print(f"  built {name} in {time.perf_counter() - t:.0f}s", flush=True)

            dst.execute("ANALYZE papers")
            dst.commit()
            n = dst.execute("SELECT count(*) FROM papers").fetchone()
            size = dst.execute(
                "SELECT pg_size_pretty(pg_database_size(current_database()))"
            ).fetchone()
            assert n is not None and size is not None
            print(f"\nDONE: {n[0]:,} papers, database {size[0]}")


if __name__ == "__main__":
    main()
