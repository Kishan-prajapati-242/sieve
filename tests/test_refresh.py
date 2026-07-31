"""The refresh test the drift count could not be (findings.md 2026-07-31).

A drift count of 0 over one 24-minute pull cannot distinguish "refresh
propagates" from "refresh is a no-op and nothing moved upstream" — both
hypotheses predict the same observation. This suite creates the movement
itself: store a work, MUTATE the served payload (new title, new abstract,
higher citation count, retraction flag), ingest again, and assert on what
the papers row does. Every test here fails against the old no-op refresh.
"""

from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest

from api.db.migrate import migrate
from api.ingest.openalex import ingest, make_client
from api.ingest.ratelimit import TokenBucket
from tests.test_openalex import UNSLICED, make_work

QUERIES = [("test", "f", 1.0)]


def serving(work_box: list[dict[str, Any]]) -> httpx.MockTransport:
    """Serves whatever the box currently holds, so a test can mutate it
    between ingest runs the way OpenAlex mutates between crawls."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"meta": {"next_cursor": None}, "results": work_box})

    return httpx.MockTransport(handler)


@pytest.fixture
def db(scratch_db: str) -> Iterator[str]:
    migrate(scratch_db)
    yield scratch_db


def run(conn: psycopg.Connection, box: list[dict[str, Any]]) -> Any:
    with make_client(transport=serving(box)) as client:
        return ingest(
            conn,
            client,
            TokenBucket(rate=1e9, capacity=1e9),
            queries=QUERIES,
            slices=UNSLICED,
        )


def test_refresh_propagates_text_citations_and_retraction(db: str) -> None:
    box = [make_work(1, doi="10.1/a")]
    with psycopg.connect(db, autocommit=True) as conn:
        run(conn, box)
        conn.execute(
            "UPDATE papers SET embedding = %s::halfvec", ("[" + ",".join(["0.1"] * 384) + "]",)
        )

        # Upstream moves: new title, rewritten abstract, citations climb,
        # and the paper is retracted AFTER our crawl.
        box[0] = make_work(1, doi="10.1/a", is_retracted=True)
        box[0]["display_name"] = "Paper 1 (revised title)"
        box[0]["abstract_inverted_index"] = {"Completely": [0], "rewritten.": [1]}
        box[0]["cited_by_count"] = 999
        stats = run(conn, box)

        row = conn.execute(
            "SELECT title, title_norm, abstract, citation_count, is_retracted, embedding IS NULL"
            " FROM papers"
        ).fetchone()

    assert row is not None
    title, title_norm, abstract, citations, retracted, embedding_nulled = row
    assert title == "Paper 1 (revised title)"
    assert "revised" in title_norm  # the normalized dedup key follows the title
    assert abstract == "Completely rewritten."
    assert citations == 999
    assert retracted is True  # the DECISION-1c screening guarantee, kept live
    assert embedding_nulled is True  # DECISION-3a
    assert stats.refreshed_text_changed == 1 and stats.refreshed == 0


def test_refresh_without_text_change_keeps_the_embedding(db: str) -> None:
    """Citations move constantly; re-embedding on every crawl would be pure
    waste. Only text changes may null the vector."""
    box = [make_work(2, doi="10.1/b")]
    vec = "[" + ",".join(["0.1"] * 384) + "]"
    with psycopg.connect(db, autocommit=True) as conn:
        run(conn, box)
        conn.execute("UPDATE papers SET embedding = %s::halfvec", (vec,))
        box[0]["cited_by_count"] = 4242  # only the citation count moves
        stats = run(conn, box)
        row = conn.execute("SELECT citation_count, embedding IS NOT NULL FROM papers").fetchone()

    assert row == (4242, True)
    assert stats.refreshed == 1 and stats.refreshed_text_changed == 0


def test_secondary_doi_linked_record_does_not_fight_over_the_paper(db: str) -> None:
    """Two records, one paper (DOI collision). Without an owner, each crawl
    would overwrite the paper with the other record's title and null the
    embedding forever — reruns would never converge."""
    box = [make_work(1, doi="10.1/same"), make_work(2, doi="doi:10.1/SAME")]
    vec = "[" + ",".join(["0.2"] * 384) + "]"
    with psycopg.connect(db, autocommit=True) as conn:
        run(conn, box)
        conn.execute("UPDATE papers SET embedding = %s::halfvec", (vec,))
        first_title = conn.execute("SELECT title FROM papers").fetchone()
        stats = run(conn, box)
        after = conn.execute("SELECT title, embedding IS NOT NULL FROM papers").fetchone()
        papers = conn.execute("SELECT count(*) FROM papers").fetchone()

    assert first_title is not None and after is not None
    assert after[0] == first_title[0]  # the owning record's title stands
    assert after[1] is True  # nothing nulled the vector: text never moved
    assert papers == (1,)
    assert stats.refreshed_text_changed == 0
