"""PubMed client: MEDLINE XML parsing, the two-call esearch/efetch shape,
and idempotency against the real database.

HTTP is a mock transport serving canned esearch JSON and efetch XML; the
database side runs on a real migrated scratch database, no mocks, per the
working agreement.
"""

import json
from typing import Any
from xml.etree import ElementTree

import httpx
import psycopg
import pytest

from api.db.migrate import migrate
from api.ingest.pubmed import (
    PUBMED_RATE,
    PubmedStats,
    fetch_articles,
    ingest,
    iter_pmids,
    make_client,
    parse_article,
    store_entry,
)
from api.ingest.ratelimit import TokenBucket


def article_xml(
    pmid: int,
    *,
    title: str | None = None,
    abstract: str = "<AbstractText>We study things.</AbstractText>",
    doi: str | None = None,
    pub_types: tuple[str, ...] = ("Journal Article",),
    year: str = "<Year>2021</Year>",
) -> str:
    doi_el = f'<ArticleId IdType="doi">{doi}</ArticleId>' if doi else ""
    types = "".join(f"<PublicationType>{t}</PublicationType>" for t in pub_types)
    return f"""<PubmedArticle>
  <MedlineCitation>
    <PMID>{pmid}</PMID>
    <Article>
      <Journal>
        <JournalIssue><PubDate>{year}</PubDate></JournalIssue>
        <Title>Journal of Testing</Title>
      </Journal>
      <ArticleTitle>{title if title is not None else f"Study {pmid}"}</ArticleTitle>
      <Abstract>{abstract}</Abstract>
      <AuthorList>
        <Author><LastName>Lovelace</LastName><ForeName>Ada</ForeName></Author>
        <Author><CollectiveName>The Working Group</CollectiveName></Author>
      </AuthorList>
      <PublicationTypeList>{types}</PublicationTypeList>
    </Article>
  </MedlineCitation>
  <PubmedData><ArticleIdList>
    <ArticleId IdType="pubmed">{pmid}</ArticleId>{doi_el}
  </ArticleIdList></PubmedData>
</PubmedArticle>"""


def article_set(*articles: str) -> str:
    return "<PubmedArticleSet>" + "\n".join(articles) + "</PubmedArticleSet>"


def eutils_transport(  # type: ignore[no-untyped-def]
    pages: list[list[int]],
    articles: dict[int, str] | None = None,
    seen: list[dict[str, Any]] | None = None,
):
    """esearch serves PMID pages by retstart; efetch serves the ids asked for."""

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if seen is not None:
            seen.append({"path": request.url.path, **params})
        if request.url.path.endswith("esearch.fcgi"):
            retmax = int(params.get("retmax", 200))
            index = int(params.get("retstart", 0)) // retmax
            idlist = [str(p) for p in (pages[index] if index < len(pages) else [])]
            return httpx.Response(200, json={"esearchresult": {"idlist": idlist}})
        wanted = [int(p) for p in params["id"].split(",")]
        bodies = [(articles or {}).get(p) or article_xml(p) for p in wanted]
        return httpx.Response(200, text=article_set(*bodies))

    return httpx.MockTransport(handler)


def free_bucket() -> TokenBucket:
    return TokenBucket(rate=1e9, capacity=1e9)


def parse_one(xml: str) -> dict[str, Any]:
    return parse_article(ElementTree.fromstring(article_set(xml)).find("PubmedArticle"))  # type: ignore[arg-type]


def test_parse_article_extracts_the_fields_we_store() -> None:
    parsed = parse_one(article_xml(31234567, doi="10.1/x"))
    assert parsed["pmid"] == "31234567"
    assert parsed["title"] == "Study 31234567"
    assert parsed["abstract"] == "We study things."
    assert parsed["year"] == 2021
    assert parsed["journal"] == "Journal of Testing"
    assert parsed["doi"] == "10.1/x"
    assert parsed["authors"] == ["Ada Lovelace", "The Working Group"]
    assert parsed["is_retracted"] is False


def test_structured_abstract_keeps_its_section_labels() -> None:
    """A clinical abstract stripped of METHODS/RESULTS reads as one blob,
    and the labels are what the embedding should see."""
    parsed = parse_one(
        article_xml(
            1,
            abstract='<AbstractText Label="METHODS">We measured.</AbstractText>'
            '<AbstractText Label="RESULTS">It worked.</AbstractText>',
        )
    )
    assert parsed["abstract"] == "METHODS: We measured. RESULTS: It worked."


def test_retracted_publication_is_flagged_and_a_retraction_notice_is_not() -> None:
    """ "Retracted Publication" = this paper was retracted. "Retraction of
    Publication" = this IS the notice about another one. DECISION-1c keeps
    retracted papers, so the flag has to name the right one."""
    retracted = parse_one(article_xml(1, pub_types=("Journal Article", "Retracted Publication")))
    notice = parse_one(article_xml(2, pub_types=("Retraction of Publication",)))
    assert retracted["is_retracted"] is True
    assert notice["is_retracted"] is False


def test_medline_free_text_date_still_yields_a_year() -> None:
    parsed = parse_one(article_xml(1, year="<MedlineDate>2019 Nov-Dec</MedlineDate>"))
    assert parsed["year"] == 2019


def test_missing_abstract_is_none_not_empty_string() -> None:
    assert parse_one(article_xml(1, abstract=""))["abstract"] is None


def test_rate_stays_under_ncbis_documented_ceiling() -> None:
    """NCBI blocks rather than throttles above 3/s, so the bucket runs under."""
    assert PUBMED_RATE < 3.0


def test_iter_pmids_pages_until_a_short_page() -> None:
    seen: list[dict[str, Any]] = []
    stats = PubmedStats()
    with make_client(transport=eutils_transport([[1, 2], [3]], seen=seen)) as client:
        got = list(iter_pmids(client, free_bucket(), "nlp", stats, per_page=2))
    assert got == ["1", "2", "3"]
    assert [p["retstart"] for p in seen] == ["0", "2"]
    assert stats.esearch_requests == 2


def test_iter_pmids_stops_at_the_limit_without_fetching_another_page() -> None:
    """The overfetch lesson: stop before the generator asks for a page nobody
    reads (findings.md 2026-07-29)."""
    seen: list[dict[str, Any]] = []
    stats = PubmedStats()
    with make_client(transport=eutils_transport([[1, 2], [3, 4]], seen=seen)) as client:
        got = list(iter_pmids(client, free_bucket(), "nlp", stats, per_page=2, limit=2))
    assert got == ["1", "2"]
    assert stats.esearch_requests == 1


def test_fetch_articles_batches_ids_into_one_request() -> None:
    stats = PubmedStats()
    with make_client(transport=eutils_transport([[1, 2, 3]])) as client:
        got = fetch_articles(client, free_bucket(), ["1", "2", "3"], stats)
    assert [a["pmid"] for a in got] == ["1", "2", "3"]
    assert stats.efetch_requests == 1


def test_ingest_twice_is_idempotent(scratch_db: str) -> None:
    """The Phase 1 criterion, held for source three: rerunning changes no counts."""
    migrate(scratch_db)
    articles = {
        1: article_xml(1, doi="10.1/a"),
        2: article_xml(2),  # no DOI
        3: article_xml(3, title=""),  # untitled: audit row only
    }
    queries = [("test", "nlp", 1.0)]
    with (
        make_client(transport=eutils_transport([[1, 2, 3]], articles)) as client,
        psycopg.connect(scratch_db, autocommit=True) as conn,
    ):
        first = ingest(conn, client, free_bucket(), queries=queries)
        second = ingest(conn, client, free_bucket(), queries=queries)
        papers = conn.execute("SELECT count(*) FROM papers").fetchone()
        records = conn.execute("SELECT count(*) FROM source_records").fetchone()
        pubmed_ids = conn.execute(
            "SELECT pubmed_id FROM papers WHERE pubmed_id IS NOT NULL ORDER BY pubmed_id"
        ).fetchall()

    assert first.fetched == 3
    assert (first.new_papers, first.skipped_no_title) == (2, 1)
    assert (second.new_papers, second.refreshed, second.skipped_no_title) == (0, 2, 1)
    assert papers == (2,)
    assert records == (3,)
    # pubmed_id is populated, which is what the id_exact dedup strategy reads.
    assert pubmed_ids == [("1",), ("2",)]


def test_raw_record_is_queryable_jsonb(scratch_db: str) -> None:
    """Stored as a dict, not the XML string, so the cascade can read PubMed
    fields without an XML parser."""
    migrate(scratch_db)
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        entry = parse_one(article_xml(99, doi="10.1/z"))
        with conn.transaction():
            store_entry(conn, entry, "test")
        row = conn.execute(
            "SELECT raw->>'doi', raw->'authors'->>0, source FROM source_records"
            " WHERE source_id = '99'"
        ).fetchone()
    assert row == ("10.1/z", "Ada Lovelace", "pubmed")


def test_retraction_flag_reaches_the_paper_row(scratch_db: str) -> None:
    migrate(scratch_db)
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        with conn.transaction():
            store_entry(
                conn,
                parse_one(article_xml(7, pub_types=("Journal Article", "Retracted Publication"))),
                "test",
            )
        row = conn.execute("SELECT is_retracted FROM papers WHERE pubmed_id = '7'").fetchone()
    assert row == (True,)


def test_esearch_and_efetch_both_identify_the_tool(scratch_db: str) -> None:
    """NCBI asks every client to send tool and email; unidentified traffic is
    what gets an IP blocked."""
    seen: list[dict[str, Any]] = []
    stats = PubmedStats()
    with make_client(transport=eutils_transport([[1]], seen=seen)) as client:
        list(iter_pmids(client, free_bucket(), "nlp", stats, per_page=1, limit=1))
        fetch_articles(client, free_bucket(), ["1"], stats)
    assert len(seen) == 2
    assert all(p.get("tool") == "sieve" and "@" in p.get("email", "") for p in seen)


@pytest.mark.parametrize("payload", ['{"esearchresult": {"idlist": []}}'])
def test_empty_result_set_ends_the_crawl(payload: str) -> None:
    stats = PubmedStats()
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=json.loads(payload)))
    with make_client(transport=transport) as client:
        assert list(iter_pmids(client, free_bucket(), "nothing", stats)) == []
