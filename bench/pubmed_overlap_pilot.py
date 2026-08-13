"""How much of a PubMed pull would already be in the corpus?

The embed column of any PubMed cost estimate is driven entirely by the
SURVIVAL rate — papers that dedup merges away are never embedded — and that
rate was being assumed at 70%. This measures it instead, following the
project's own pattern of pinning a rate down with a pilot before paying for
the full run (the 1,000-paper encode benchmark, the 100-record arXiv
idempotency check).

The pilot is esearch-ONLY for the id_exact arm, which is both cheap and
exact: OpenAlex already carries PMIDs for 44,517 of the 183,167 papers, so
membership is a set lookup against a column, not a similarity judgment. One
esearch request returns 200 PMIDs, so a 10,000-PMID pilot costs 50 requests
= 20 seconds at the 2.5/s bucket, and writes NOTHING.

A smaller efetch subsample then measures the INCREMENTAL overlap the PMID
arm cannot see: a PubMed record whose paper is in the corpus under a DOI
but without a PMID recorded. That is the gap between the id_exact lower
bound and the true merge rate.

Nothing is stored and nothing is merged. The output is a rate with a
binomial interval, which is what the cost estimate needs and all it needs.
"""

import argparse
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from api.dedup.normalize import normalize_doi, normalize_title
from api.ingest.pubmed import (
    PUBMED_RATE,
    QUERIES,
    PubmedStats,
    fetch_articles,
    iter_pmids,
    make_client,
)
from api.ingest.ratelimit import TokenBucket
from bench.harness import db_state, method_record


def wilson(hits: int, n: int) -> list[float]:
    """Wilson interval — the normal approximation misbehaves near 0 and 1,
    and an overlap rate can legitimately sit near either end."""
    if n == 0:
        return [0.0, 0.0]
    z, p = 1.96, hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-query", type=int, default=2000, help="PMIDs per query term")
    ap.add_argument("--efetch-sample", type=int, default=600, help="records to fetch for DOIs")
    args = ap.parse_args()

    out_dir = Path(__file__).parent
    bucket = TokenBucket(rate=PUBMED_RATE, capacity=1.0)
    stats = PubmedStats()

    per_query: dict[str, list[str]] = {}
    with make_client() as client:
        for name, term, _w in QUERIES:
            per_query[name] = list(
                iter_pmids(client, bucket, term, stats, per_page=200, limit=args.per_query)
            )
            print(f"  {name}: {len(per_query[name])} pmids", flush=True)

        pmids = sorted({p for ids in per_query.values() for p in ids})
        # Subsample for the efetch arm, evenly spread so it is not the head
        # of one query's result set.
        step = max(1, len(pmids) // args.efetch_sample)
        sample = pmids[::step][: args.efetch_sample]
        articles = []
        for i in range(0, len(sample), 200):
            articles.extend(fetch_articles(client, bucket, sample[i : i + 200], stats))
        print(f"  efetch subsample: {len(articles)} records", flush=True)

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        state = db_state(conn)
        known_pmids = {
            r[0]
            for r in conn.execute(
                "SELECT pubmed_id FROM papers WHERE pubmed_id = ANY(%s)", (pmids,)
            ).fetchall()
        }
        # The efetch arm: which sampled records match by DOI or title+year
        # WITHOUT already matching by PMID.
        dois = [normalize_doi(a["doi"]) for a in articles if a.get("doi")]
        known_dois = {
            r[0]
            for r in conn.execute("SELECT doi FROM papers WHERE doi = ANY(%s)", (dois,)).fetchall()
        }
        titles = [normalize_title(a["title"]) for a in articles if a.get("title")]
        known_titles = {
            r[0]
            for r in conn.execute(
                "SELECT title_norm FROM papers WHERE title_norm = ANY(%s)", (titles,)
            ).fetchall()
        }

    by_pmid = sum(1 for a in articles if a["pmid"] in known_pmids)
    by_doi_only = sum(
        1
        for a in articles
        if a["pmid"] not in known_pmids and a.get("doi") and normalize_doi(a["doi"]) in known_dois
    )
    by_title_only = sum(
        1
        for a in articles
        if a["pmid"] not in known_pmids
        and not (a.get("doi") and normalize_doi(a["doi"]) in known_dois)
        and a.get("title")
        and normalize_title(a["title"]) in known_titles
    )
    n = len(articles)
    overlap = by_pmid + by_doi_only + by_title_only

    report: dict[str, Any] = {
        "measured_at": datetime.now(UTC).isoformat(),
        "method": method_record(
            timing_window="n/a — this is an overlap rate, not a latency",
            db_state=state,
            protocol=f"esearch-only for {len(pmids)} distinct PMIDs across "
            f"{len(QUERIES)} query terms (nothing written); efetch subsample of "
            f"{n} records, evenly spread, for the DOI and title arms",
            requests={"esearch": stats.esearch_requests, "efetch": stats.efetch_requests},
            caveat="id_exact overlap is EXACT (a column lookup). The DOI and title "
            "arms are measured on the subsample and carry its sampling error. "
            "Title+year here is title_norm equality only, which is a lower bound "
            "on what title_trgm would catch.",
        ),
        "pmid_arm_full_pilot": {
            "distinct_pmids": len(pmids),
            "already_in_corpus": len(known_pmids),
            "rate": round(len(known_pmids) / len(pmids), 4) if pmids else None,
            "ci95": wilson(len(known_pmids), len(pmids)),
        },
        "subsample_arms": {
            "n": n,
            "by_pmid": by_pmid,
            "by_doi_not_pmid": by_doi_only,
            "by_title_not_doi_or_pmid": by_title_only,
            "total_overlap": overlap,
            "overlap_rate": round(overlap / n, 4) if n else None,
            "ci95": wilson(overlap, n),
            "implied_survival_rate": round(1 - overlap / n, 4) if n else None,
        },
        "per_query_pmids": {k: len(v) for k, v in per_query.items()},
    }
    (out_dir / "results_pubmed_overlap_pilot.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "method"}, indent=2))


if __name__ == "__main__":
    main()
