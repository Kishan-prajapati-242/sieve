"""Collections, screening, and BibTeX export.

BibTeX correctness is tested on the escaping and the keys, because that is
where exports actually break: a title with "R&D" produces a file that will
not compile, and unstable keys make the file useless in version control.
"""

import psycopg
import pytest
from fastapi.testclient import TestClient

from api.collections.bibtex import entry_key, escape, protect_caps, to_bibtex, unique_keys
from api.db.migrate import migrate


@pytest.fixture
def client(scratch_db: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    migrate(scratch_db)
    monkeypatch.setenv("DATABASE_URL", scratch_db)
    from api.db import pool

    pool.close_pool()
    from api.main import app

    with TestClient(app) as c:
        # Collections belong to a user (migration 0014), so every one of these
        # routes now requires a session. Signing in here keeps each test about
        # collection behaviour; the ownership boundary itself is tested in
        # test_auth.py::TestCollectionIsolation.
        # Verification is a real gate now (signup grants no usable session),
        # so the fixture completes it. The gate itself is covered in
        # test_auth.py::TestVerificationActuallyGates.
        from api.auth import codes

        c.post(
            "/api/auth/signup",
            json={"email": "reviewer@example.com", "password": "a-long-test-password"},
        ).raise_for_status()
        with psycopg.connect(scratch_db, autocommit=True) as conn:
            row = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
            assert row is not None
            code = codes.issue(conn, int(row[0]))
        c.post("/api/auth/verify", json={"code": code}).raise_for_status()
        yield c
    pool.close_pool()


def seed_papers(dsn: str, n: int = 3) -> list[int]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        return [
            row[0]
            for i in range(n)
            if (
                row := conn.execute(
                    "INSERT INTO papers (title, title_norm, year, venue, doi, authors)"
                    " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (
                        f"Paper {i}",
                        f"paper {i}",
                        2020 + i,
                        "Journal of Testing" if i else None,
                        f"10.1/{i}",
                        ["Ada Lovelace", "Grace Hopper"],
                    ),
                ).fetchone()
            )
            is not None
        ]


# ---------------------------------------------------------------- bibtex


def test_escape_makes_tex_special_characters_safe() -> None:
    """ "R&D" is the canonical broken export: & is TeX syntax."""
    assert escape("R&D at 50% cost") == r"R\&D at 50\% cost"
    assert escape("a_b {c} #d $e") == r"a\_b \{c\} \#d \$e"


def test_backslash_becomes_textbackslash_not_a_line_break() -> None:
    r"""Escaping \ as \\ means "line break" in TeX — worse than unescaped."""
    assert escape("a\\b") == r"a\textbackslash{}b"
    assert "\\\\" not in escape("a\\b")


def test_protect_caps_braces_acronyms_but_not_ordinary_words() -> None:
    """BibTeX lowercases unbraced title words: "DNA" would render "Dna"."""
    assert protect_caps("DNA sequencing with BERT") == "{DNA} sequencing with {BERT}"
    assert protect_caps("pyTorch models") == "{pyTorch} models"
    # Ordinary leading capitals are left alone; bracing them all is unreadable.
    assert protect_caps("A study of things") == "A study of things"


def test_entry_keys_are_stable_and_collisions_are_suffixed() -> None:
    """Same collection, same file, byte for byte — these live in git."""
    papers = [
        {"authors": ["Ada Lovelace"], "year": 2020, "title": "Retrieval systems"},
        {"authors": ["Ada Lovelace"], "year": 2020, "title": "Retrieval reconsidered"},
        {"authors": [], "year": None, "title": ""},
    ]
    assert entry_key(papers[0]) == "lovelace2020retrieval"
    assert unique_keys(papers) == [
        "lovelace2020retrieval",
        "lovelace2020retrievala",
        "anonnduntitled",
    ]
    assert unique_keys(papers) == unique_keys(papers)


def test_a_paper_without_a_venue_is_misc_not_an_empty_article() -> None:
    """An arXiv preprint is not an article with a blank journal field."""
    preprint = to_bibtex([{"title": "T", "year": 2020, "arxiv_id": "2301.001"}])
    published = to_bibtex([{"title": "T", "year": 2020, "venue": "Nature"}])
    assert preprint.startswith("@misc{")
    assert "journal" not in preprint
    assert published.startswith("@article{")
    assert "journal = {Nature}" in published


def test_absent_fields_are_omitted_not_emitted_empty() -> None:
    """`journal = {}` looks like data. Absence should look like absence."""
    out = to_bibtex([{"title": "T"}])
    for field in ("year", "doi", "author", "journal"):
        assert f"{field} =" not in out


def test_authors_are_joined_with_and() -> None:
    out = to_bibtex([{"title": "T", "authors": ["Ada Lovelace", "Grace Hopper"]}])
    assert "author = {Ada Lovelace and Grace Hopper}" in out


def test_empty_collection_exports_empty_not_malformed() -> None:
    assert to_bibtex([]) == ""


# ------------------------------------------------------------- workflow


def test_screening_is_an_idempotent_upsert(client: TestClient, scratch_db: str) -> None:
    """A reviewer clicking include then exclude leaves ONE row with the
    later decision, not two rows and a tiebreak."""
    paper_ids = seed_papers(scratch_db, 1)
    cid = client.post("/api/collections", json={"name": "Q", "question": "does it?"}).json()["id"]
    url = f"/api/collections/{cid}/screenings/{paper_ids[0]}"

    assert client.put(url, json={"decision": "include"}).json()["decision"] == "include"
    assert (
        client.put(url, json={"decision": "exclude", "note": "wrong population"}).status_code == 200
    )

    body = client.get(f"/api/collections/{cid}").json()
    assert len(body["papers"]) == 1
    assert body["papers"][0]["decision"] == "exclude"
    assert body["papers"][0]["note"] == "wrong population"


def test_collection_list_reports_decision_counts(client: TestClient, scratch_db: str) -> None:
    paper_ids = seed_papers(scratch_db, 3)
    cid = client.post("/api/collections", json={"name": "Q"}).json()["id"]
    for pid, decision in zip(paper_ids, ("include", "exclude", "maybe"), strict=True):
        client.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": decision})

    summary = next(c for c in client.get("/api/collections").json() if c["id"] == cid)
    assert (summary["screened"], summary["included"], summary["excluded"], summary["maybe"]) == (
        3,
        1,
        1,
        1,
    )


def test_export_defaults_to_included_papers(client: TestClient, scratch_db: str) -> None:
    """ "Export my collection" means the ones that made the cut."""
    paper_ids = seed_papers(scratch_db, 3)
    cid = client.post("/api/collections", json={"name": "Q"}).json()["id"]
    for pid, decision in zip(paper_ids, ("include", "exclude", "include"), strict=True):
        client.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": decision})

    resp = client.get(f"/api/collections/{cid}/export.bib")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-bibtex")
    assert resp.text.count("@") == 2
    everything = client.get(f"/api/collections/{cid}/export.bib?decision=exclude")
    assert everything.text.count("@") == 1


def test_export_is_byte_identical_across_calls(client: TestClient, scratch_db: str) -> None:
    paper_ids = seed_papers(scratch_db, 3)
    cid = client.post("/api/collections", json={"name": "Q"}).json()["id"]
    for pid in paper_ids:
        client.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})
    first = client.get(f"/api/collections/{cid}/export.bib").text
    second = client.get(f"/api/collections/{cid}/export.bib").text
    assert first == second and first != ""


def test_the_reviewers_note_travels_into_the_export(client: TestClient, scratch_db: str) -> None:
    """An export that loses why a paper was included is half an export."""
    pid = seed_papers(scratch_db, 1)[0]
    cid = client.post("/api/collections", json={"name": "Q"}).json()["id"]
    client.put(
        f"/api/collections/{cid}/screenings/{pid}",
        json={"decision": "include", "note": "primary outcome matches"},
    )
    assert (
        "note = {primary outcome matches}" in client.get(f"/api/collections/{cid}/export.bib").text
    )


def test_unscreening_removes_the_decision(client: TestClient, scratch_db: str) -> None:
    pid = seed_papers(scratch_db, 1)[0]
    cid = client.post("/api/collections", json={"name": "Q"}).json()["id"]
    url = f"/api/collections/{cid}/screenings/{pid}"
    client.put(url, json={"decision": "include"})
    assert client.delete(url).status_code == 204
    assert client.get(f"/api/collections/{cid}").json()["papers"] == []
    assert client.delete(url).status_code == 404


def test_unknown_ids_are_404_not_500(client: TestClient, scratch_db: str) -> None:
    pid = seed_papers(scratch_db, 1)[0]
    cid = client.post("/api/collections", json={"name": "Q"}).json()["id"]
    assert client.get("/api/collections/999999").status_code == 404
    assert (
        client.put(
            f"/api/collections/999999/screenings/{pid}", json={"decision": "include"}
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/collections/{cid}/screenings/999999", json={"decision": "include"}
        ).status_code
        == 404
    )


def test_an_invalid_decision_is_rejected_by_the_schema(client: TestClient, scratch_db: str) -> None:
    pid = seed_papers(scratch_db, 1)[0]
    cid = client.post("/api/collections", json={"name": "Q"}).json()["id"]
    resp = client.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "perhaps"})
    assert resp.status_code == 422


def test_deleting_a_collection_takes_its_screenings(client: TestClient, scratch_db: str) -> None:
    """ON DELETE CASCADE from collections: deleting the question takes its
    decisions."""
    pid = seed_papers(scratch_db, 1)[0]
    cid = client.post("/api/collections", json={"name": "Q"}).json()["id"]
    client.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        conn.execute("DELETE FROM collections WHERE id = %s", (cid,))
        assert conn.execute("SELECT count(*) FROM screenings").fetchone() == (0,)


def test_deleting_a_screened_paper_is_refused(client: TestClient, scratch_db: str) -> None:
    """Papers are NOT cascaded. Dedup deletes paper rows when it merges
    them, and a human's screening decision must not vanish because its
    paper was merged into its twin — the FK fails loudly instead."""
    pid = seed_papers(scratch_db, 1)[0]
    cid = client.post("/api/collections", json={"name": "Q"}).json()["id"]
    client.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})
    with (
        psycopg.connect(scratch_db, autocommit=True) as conn,
        pytest.raises(psycopg.errors.ForeignKeyViolation),
    ):
        conn.execute("DELETE FROM papers WHERE id = %s", (pid,))
