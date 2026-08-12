"""PubMed E-utilities client. Third source, and the one that gives dedup
its real workload.

Shape differences that drive this module:

  Two calls per batch, not one. esearch returns PMIDs only; efetch turns
  PMIDs into records. So a pull is 1 esearch page + 1 efetch per batch of
  EFETCH_BATCH ids, and the id list is the unit of pagination.

  MEDLINE XML, parsed with stdlib ElementTree for the same reason arXiv is
  — the subset we read is small and stable. Stored as a dict so
  source_records.raw stays queryable JSONB.

  Structured abstracts. Clinical papers carry <AbstractText Label="METHODS">
  sections; concatenating the text alone loses the labels a reader needs,
  and keeping the labels inline is what the embedding should see, so
  sections are joined as "LABEL: text".

  is_retracted is derived here, not inferred later: PublicationType
  "Retracted Publication" means THIS article was retracted. "Retraction of
  Publication" is the opposite — a retraction notice ABOUT another paper —
  and treating the two alike would flag the notices and miss the papers.

  3 requests/second is NCBI's documented ceiling without an API key, and
  exceeding it gets an IP blocked rather than throttled. The bucket runs
  at 2.5/s: the margin covers the gap between our clock and theirs, and
  costs about 7 minutes on a 10,000-record pull.

  No citation counts (PubMed has none), so citation_count is 0 — absent,
  not measured, exactly as for arXiv.

Ordering matches arXiv: ingest never embeds, so papers the cascade merges
away are never embedded at all.
"""

import argparse
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree

import httpx
import psycopg

from api.ingest.http import get_json, get_text
from api.ingest.ratelimit import TokenBucket
from api.ingest.store import (
    PaperFields,
    create_or_link_paper,
    owns_paper,
    refresh_paper,
    upsert_record,
)

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "sieve"
EMAIL = "prajapati.kish@northeastern.edu"

# NCBI documents 3 req/s without a key and blocks rather than throttles.
# capacity=1 so no burst can front-load the very first requests.
PUBMED_RATE = 2.5
ESEARCH_PAGE = 200
EFETCH_BATCH = 200

# The corpus domain (DECISION-2 domains in PubMed's field syntax). [tiab]
# is title-or-abstract; the date bound keeps the pull inside the era the
# other two sources cover.
QUERIES: list[tuple[str, str, float]] = [
    (
        "pubmed-clinical-nlp",
        '("natural language processing"[tiab] OR "text mining"[tiab])'
        ' AND ("clinical"[tiab] OR "electronic health record"[tiab])'
        " AND 2015:2026[dp]",
        0.40,
    ),
    (
        "pubmed-simplification",
        '("text simplification"[tiab] OR "readability"[tiab] OR "health literacy"[tiab])'
        ' AND ("patient"[tiab] OR "consumer health"[tiab]) AND 2015:2026[dp]',
        0.25,
    ),
    (
        "pubmed-mental-health-nlp",
        '("natural language processing"[tiab] OR "machine learning"[tiab])'
        ' AND ("depression"[tiab] OR "suicide"[tiab] OR "mental health"[tiab])'
        " AND 2015:2026[dp]",
        0.20,
    ),
    (
        "pubmed-biomedical-ner",
        '("named entity recognition"[tiab] OR "entity extraction"[tiab]'
        ' OR "concept normalization"[tiab]) AND 2015:2026[dp]',
        0.15,
    ),
]


@dataclass
class PubmedStats:
    fetched: int = 0
    new_papers: int = 0
    linked_by_doi: int = 0
    refreshed: int = 0
    refreshed_text_changed: int = 0
    skipped_no_title: int = 0
    esearch_requests: int = 0
    efetch_requests: int = 0
    per_query: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"fetched={self.fetched} new_papers={self.new_papers}"
            f" linked_by_doi={self.linked_by_doi} refreshed={self.refreshed}"
            f" refreshed_text_changed={self.refreshed_text_changed}"
            f" skipped_no_title={self.skipped_no_title}"
            f" esearch={self.esearch_requests} efetch={self.efetch_requests}"
        )


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"User-Agent": f"{TOOL}/0.1 (mailto:{EMAIL})"},
        timeout=httpx.Timeout(30.0, connect=5.0),
        transport=transport,
    )


def _abstract(article: ElementTree.Element) -> str | None:
    """Structured abstracts keep their section labels: a clinical abstract
    without "METHODS:" reads as one undifferentiated paragraph, and the
    label is signal for both the reader and the embedding."""
    parts = []
    for node in article.findall("Abstract/AbstractText"):
        text = "".join(node.itertext()).strip()
        if not text:
            continue
        label = node.get("Label")
        parts.append(f"{label}: {text}" if label else text)
    return " ".join(parts) or None


def _year(article: ElementTree.Element) -> int | None:
    """PubDate is Year, or a MedlineDate free-text range ("2019 Nov-Dec")."""
    year = article.findtext("Journal/JournalIssue/PubDate/Year")
    if year and year[:4].isdigit():
        return int(year[:4])
    medline = article.findtext("Journal/JournalIssue/PubDate/MedlineDate") or ""
    return int(medline[:4]) if medline[:4].isdigit() else None


def _authors(article: ElementTree.Element) -> list[str]:
    names = []
    for author in article.findall("AuthorList/Author"):
        collective = author.findtext("CollectiveName")
        if collective:
            names.append(collective.strip())
            continue
        last, fore = author.findtext("LastName"), author.findtext("ForeName")
        if last:
            names.append(f"{fore} {last}".strip() if fore else last.strip())
    return names


def parse_article(node: ElementTree.Element) -> dict[str, Any]:
    """One <PubmedArticle> -> a plain dict, stored verbatim as the raw record."""
    citation = node.find("MedlineCitation")
    assert citation is not None
    article = citation.find("Article")
    assert article is not None

    ids = {
        (aid.get("IdType") or ""): (aid.text or "").strip()
        for aid in node.findall("PubmedData/ArticleIdList/ArticleId")
    }
    doi = ids.get("doi") or article.findtext("ELocationID[@EIdType='doi']")
    pub_types = [
        (t.text or "").strip() for t in article.findall("PublicationTypeList/PublicationType")
    ]
    return {
        "pmid": (citation.findtext("PMID") or "").strip(),
        "title": " ".join("".join(article.find("ArticleTitle").itertext()).split())  # type: ignore[union-attr]
        if article.find("ArticleTitle") is not None
        else "",
        "abstract": _abstract(article),
        "year": _year(article),
        "journal": article.findtext("Journal/Title") or article.findtext("Journal/ISOAbbreviation"),
        "doi": doi.strip() if doi else None,
        "authors": _authors(article),
        "publication_types": pub_types,
        # "Retracted Publication" = this paper was retracted. "Retraction of
        # Publication" = this IS the retraction notice about another paper.
        "is_retracted": "Retracted Publication" in pub_types,
    }


def iter_pmids(
    client: httpx.Client,
    bucket: TokenBucket,
    term: str,
    stats: PubmedStats,
    *,
    per_page: int = ESEARCH_PAGE,
    limit: int | None = None,
) -> Iterator[str]:
    """PMIDs for a query, newest first, page by page."""
    retstart, seen = 0, 0
    while True:
        stats.esearch_requests += 1
        data = get_json(
            client,
            "/esearch.fcgi",
            params={
                "db": "pubmed",
                "term": term,
                "retmode": "json",
                "retstart": retstart,
                "retmax": per_page,
                "sort": "date",
                "tool": TOOL,
                "email": EMAIL,
            },
            bucket=bucket,
        )
        idlist = data.get("esearchresult", {}).get("idlist", [])
        if not idlist:
            return
        for pmid in idlist:
            yield pmid
            seen += 1
            if limit is not None and seen >= limit:
                return
        if len(idlist) < per_page:
            return  # short page: result set exhausted
        retstart += per_page


def fetch_articles(
    client: httpx.Client, bucket: TokenBucket, pmids: list[str], stats: PubmedStats
) -> list[dict[str, Any]]:
    """One efetch for a batch of PMIDs."""
    if not pmids:
        return []
    stats.efetch_requests += 1
    body = get_text(
        client,
        "/efetch.fcgi",
        params={
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "tool": TOOL,
            "email": EMAIL,
        },
        bucket=bucket,
    )
    root = ElementTree.fromstring(body)
    return [parse_article(node) for node in root.findall("PubmedArticle")]


def paper_fields(entry: dict[str, Any]) -> PaperFields:
    return PaperFields(
        title=str(entry["title"]),
        abstract=entry.get("abstract"),
        year=entry.get("year"),
        venue=entry.get("journal"),
        # PubMed publishes no citation counts: absent, not zero-as-measured.
        citation_count=0,
        doi=entry.get("doi"),
        is_retracted=bool(entry.get("is_retracted")),
        authors=entry.get("authors") or None,
        pubmed_id=entry.get("pmid"),
    )


def store_entry(conn: psycopg.Connection, entry: dict[str, Any], query_name: str | None) -> str:
    """Upsert one article through the shared store layer (DECISION-3a lives
    there, so this client cannot forget to null a stale embedding)."""
    record_id, paper_id = upsert_record(
        conn,
        source="pubmed",
        source_id=str(entry["pmid"]),
        raw=entry,
        query_name=query_name,
    )
    if not entry.get("title"):
        return "skipped_no_title"

    fields = paper_fields(entry)
    if paper_id is not None:
        if not owns_paper(conn, record_id, paper_id):
            return "refreshed"
        return "refreshed_text_changed" if refresh_paper(conn, paper_id, fields) else "refreshed"

    _, outcome = create_or_link_paper(conn, record_id, fields)
    return outcome


def ingest(
    conn: psycopg.Connection,
    client: httpx.Client,
    bucket: TokenBucket,
    *,
    limit: int | None = None,
    queries: list[tuple[str, str, float]] | None = None,
) -> PubmedStats:
    """Crawl each query within its share of --limit. One transaction per
    article: a crash loses at most one article and a rerun converges."""
    if not conn.autocommit:
        raise ValueError(
            "ingest requires an autocommit connection; per-article commits are the point"
        )
    queries = list(queries if queries is not None else QUERIES)
    from api.ingest.openalex import split_budget

    budgets: list[int | None]
    budgets = (
        [None] * len(queries)
        if limit is None
        else list(split_budget(limit, [w for _, _, w in queries]))
    )

    stats = PubmedStats()
    for (name, term, _), budget in zip(queries, budgets, strict=True):
        stats.per_query[name] = 0
        if budget == 0:
            continue
        print(f"query {name}: starting (budget {'unlimited' if budget is None else budget})")
        # Page size never exceeds the budget: an esearch page costs the same
        # wall clock whether it returns 200 ids or 5, but efetching ids
        # nobody stores is a request spent on nothing (the overfetch bug the
        # OpenAlex credit meter caught, findings.md 2026-07-29).
        page = ESEARCH_PAGE if budget is None else min(ESEARCH_PAGE, budget)
        batch: list[str] = []
        taken = 0
        for pmid in iter_pmids(client, bucket, term, stats, per_page=page, limit=budget):
            batch.append(pmid)
            if len(batch) < min(EFETCH_BATCH, budget or EFETCH_BATCH):
                continue
            taken += _store_batch(conn, client, bucket, batch, name, stats)
            batch = []
        if batch:
            taken += _store_batch(conn, client, bucket, batch, name, stats)
        if budget is not None and taken < budget:
            print(f"  {name}: exhausted at {taken} of {budget}")
    return stats


def _store_batch(
    conn: psycopg.Connection,
    client: httpx.Client,
    bucket: TokenBucket,
    pmids: list[str],
    query_name: str,
    stats: PubmedStats,
) -> int:
    stored = 0
    for entry in fetch_articles(client, bucket, pmids, stats):
        with conn.transaction():
            outcome = store_entry(conn, entry, query_name)
        stored += 1
        stats.fetched += 1
        stats.per_query[query_name] += 1
        setattr(stats, outcome, getattr(stats, outcome) + 1)
    return stored


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest PubMed articles.")
    parser.add_argument("--limit", type=int, default=None, help="total articles across queries")
    args = parser.parse_args()

    conninfo = os.environ.get("DATABASE_URL")
    if not conninfo:
        print("DATABASE_URL is required", file=sys.stderr)
        raise SystemExit(2)

    start = time.perf_counter()
    with make_client() as client, psycopg.connect(conninfo, autocommit=True) as conn:
        stats = ingest(conn, client, TokenBucket(rate=PUBMED_RATE, capacity=1.0), limit=args.limit)
    print(stats.summary())
    for name, count in stats.per_query.items():
        print(f"  {name}: {count} articles")
    print(f"elapsed: {time.perf_counter() - start:.0f}s (rate-limited to {PUBMED_RATE} req/s)")


if __name__ == "__main__":
    main()
