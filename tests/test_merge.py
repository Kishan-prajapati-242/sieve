"""Merge execution and rollback, against a real database.

The round-trip test is the one that matters: merge, then rollback, then
assert the database is byte-identical to its pre-merge state. Without it,
"reversible" is a claim rather than a property.
"""

from typing import Any

import psycopg
import pytest

from api.db.migrate import migrate
from api.dedup.merge import choose_survivor, merge_group, merged_fields, rollback

PREPRINT = {
    "id": 100,
    "title": "Ascle: A Toolkit for Medical Text (Preprint)",
    "abstract": "preprint abstract",
    "year": 2024,
    "venue": "arXiv (Cornell University)",
    "citation_count": 1,
    "doi": "10.2196/preprints.60601",
    "arxiv_id": "2311.16588",
    "pubmed_id": None,
}
PUBLISHED = {
    "id": 101,
    "title": "Ascle: A Toolkit for Medical Text: Development and Evaluation",
    "abstract": "published abstract, rewritten",
    "year": 2024,
    "venue": "Journal of Medical Internet Research",
    "citation_count": 14,
    "doi": "10.2196/60601",
    "arxiv_id": None,
    "pubmed_id": "38345678",
}


def seed(conn: psycopg.Connection, rows: list[dict[str, Any]]) -> None:
    for r in rows:
        conn.execute(
            """
            INSERT INTO papers (id, title, title_norm, abstract, year, venue, citation_count,
                                doi, arxiv_id, pubmed_id, authors, embedding)
            VALUES (%(id)s, %(title)s, lower(%(title)s), %(abstract)s, %(year)s, %(venue)s,
                    %(citation_count)s, %(doi)s, %(arxiv_id)s, %(pubmed_id)s,
                    %(authors)s, %(embedding)s)
            """,
            {**r, "authors": ["Rui Yang"], "embedding": "[" + ",".join(["0.1"] * 384) + "]"},
        )
        conn.execute(
            "INSERT INTO source_records (source, source_id, raw, paper_id)"
            " VALUES ('openalex', %s, '{}', %s)",
            (f"W{r['id']}", r["id"]),
        )


@pytest.fixture
def db(scratch_db: str) -> str:
    migrate(scratch_db)
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        seed(conn, [PREPRINT, PUBLISHED])
    return scratch_db


def snapshot(conn: psycopg.Connection) -> dict[str, Any]:
    papers = conn.execute(
        "SELECT id, doi, title, abstract, venue, citation_count, arxiv_id, pubmed_id,"
        " is_retracted, authors FROM papers ORDER BY id"
    ).fetchall()
    records = conn.execute("SELECT id, paper_id FROM source_records ORDER BY id").fetchall()
    return {"papers": papers, "records": records}


def test_published_wins_and_citations_take_max(db: str) -> None:
    """DECISION-3b, the field-level assertions."""
    with psycopg.connect(db, autocommit=True) as conn:
        with conn.transaction():
            result = merge_group(conn, [100, 101], "preprint_trgm", 0.694)
        row = conn.execute(
            "SELECT id, title, abstract, venue, citation_count, arxiv_id, pubmed_id, doi"
            " FROM papers"
        ).fetchall()
        records = conn.execute(
            "SELECT count(*) FROM source_records WHERE paper_id = 101"
        ).fetchone()

    assert result["survivor_id"] == 101  # published (venue present, not a preprint server)
    assert result["deleted"] == [100]
    assert len(row) == 1
    _id, title, abstract, venue, citations, arxiv_id, pubmed_id, doi = row[0]
    assert "Development and Evaluation" in title  # published title
    assert abstract == "published abstract, rewritten"
    assert venue == "Journal of Medical Internet Research"
    assert citations == 14  # MAX, not 15 — summing double-counts
    assert arxiv_id == "2311.16588"  # kept from the preprint side
    assert pubmed_id == "38345678"
    assert doi == "10.2196/60601"
    assert records == (2,)  # both records now point at the survivor


def test_merge_then_rollback_restores_everything(db: str) -> None:
    """The property that makes execution safe: reversibility, verified, not
    asserted. Papers come back WITH THEIR ORIGINAL IDS, records point where
    they used to, and the survivor's overwritten fields are restored."""
    with psycopg.connect(db, autocommit=True) as conn:
        before = snapshot(conn)
        with conn.transaction():
            result = merge_group(conn, [100, 101], "preprint_trgm", 0.694)
        assert snapshot(conn) != before  # the merge really changed something
        with conn.transaction():
            undone = rollback(conn, result["merge_id"])
        after = snapshot(conn)
        merges_left = conn.execute("SELECT count(*) FROM merges").fetchone()

    assert undone["restored"] == [100]
    assert after["papers"] == before["papers"], "papers must round-trip exactly"
    assert after["records"] == before["records"], "record links must round-trip"
    assert merges_left == (0,)


def test_rollback_refuses_a_snapshotless_merge(db: str) -> None:
    """Merges written before snapshots existed cannot be rolled back, and
    the code says so instead of silently doing half a job."""
    with psycopg.connect(db, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO merges (kept_paper_id, merged_from, strategy)"
            " VALUES (101, '{\"source_record_ids\": [1]}', 'doi_exact') RETURNING id"
        ).fetchone()
        assert row is not None
        with pytest.raises(ValueError, match="cannot be rolled back"):
            rollback(conn, row[0])


def test_survivor_choice_prefers_published_then_lowest_id() -> None:
    a = {"id": 5, "publication_rank": 0}
    b = {"id": 9, "publication_rank": 2}
    c = {"id": 3, "publication_rank": 2}
    assert choose_survivor([a, b, c])["id"] == 3  # published, lowest id among equals
    assert choose_survivor([a])["id"] == 5


def test_is_retracted_survives_from_any_member() -> None:
    """A retraction anywhere in the group must survive the merge — DECISION-1c
    exists so a reviewer sees it."""
    members = [
        {
            **PREPRINT,
            "is_retracted": True,
            "authors": None,
            "title_norm": "x",
            "publication_rank": 0,
        },
        {
            **PUBLISHED,
            "is_retracted": False,
            "authors": None,
            "title_norm": "y",
            "publication_rank": 2,
        },
    ]
    survivor = choose_survivor(members)
    assert merged_fields(survivor, members)["is_retracted"] is True
