"""Regression fixtures: real duplicates from the Phase 1 corpus that any
future rule change must keep merging, and real siblings it must keep apart.

Every row here was observed in the live 197K corpus (ids and DOIs are the
real ones), so these are not invented cases — they are the specific
records that motivated each rule.

The asthma family is the sharpest fixture available, because it contains
BOTH answers at once:
  * three records of one paper (BMC + two Figshare deposits) that MUST
    merge despite three distinct DOIs;
  * six "Additional file N" records that must merge in versioned PAIRS
    (file 1 with file 1) and must NOT merge across file numbers.
That is why distinct-DOI was rejected as a non-duplicate signal: it would
split the trio (findings.md 2026-08-01).
"""

import psycopg
import pytest

from api.db.migrate import migrate
from api.dedup.cascade import find_pairs
from api.dedup.rules import ABSTRACT_TITLE_SIM, TRGM_THRESHOLD, sibling_sql

# (id, title, year, doi, abstract_key) — abstract_key stands in for the
# real abstract: equal keys mean byte-identical abstracts upstream.
ASTHMA_TITLE = (
    "Evaluation of large Language models on pediatric asthma: a comparative study of "
    "Claude3 Opus, Gemini 2.0, ChatGPT-4o and DeepSeek"
)
CORPUS: list[tuple[int, str, int, str | None, str]] = [
    # The trio: one paper, three DOIs, two share an abstract.
    (561, ASTHMA_TITLE, 2026, "10.1186/s12911-026-03371-x", "bmc-abstract"),
    (23693, ASTHMA_TITLE, 2026, "10.6084/m9.figshare.c.8354879", "figshare-abstract"),
    (23694, ASTHMA_TITLE, 2026, "10.6084/m9.figshare.c.8354879.v1", "figshare-abstract"),
    # Supplementary files: versioned pairs, three different files.
    (174581, f"Additional file 1 of {ASTHMA_TITLE}", 2026, "10.6084/m9.figshare.31769588", "af1"),
    (
        174610,
        f"Additional file 1 of {ASTHMA_TITLE}",
        2026,
        "10.6084/m9.figshare.31769588.v1",
        "af1",
    ),
    (174600, f"Additional file 2 of {ASTHMA_TITLE}", 2026, "10.6084/m9.figshare.31769591", "af2"),
    (
        174607,
        f"Additional file 2 of {ASTHMA_TITLE}",
        2026,
        "10.6084/m9.figshare.31769591.v1",
        "af2",
    ),
    (174578, f"Additional file 3 of {ASTHMA_TITLE}", 2026, "10.6084/m9.figshare.31769594", "af3"),
    (
        174592,
        f"Additional file 3 of {ASTHMA_TITLE}",
        2026,
        "10.6084/m9.figshare.31769594.v1",
        "af3",
    ),
]

ASCLE_PREPRINT = "Ascle: A Python Natural Language Processing Toolkit for Medical Text Generation"
ASCLE = [
    (22960, ASCLE_PREPRINT, 2023, "10.48550/arxiv.2311.16588", "arxiv-abstract"),
    (23153, f"{ASCLE_PREPRINT} (Preprint)", 2024, "10.2196/preprints.60601", "preprint-abstract"),
    (
        23086,
        "Ascle—A Python Natural Language Processing Toolkit for Medical Text Generation: "
        "Development and Evaluation Study",
        2024,
        "10.2196/60601",
        "published-abstract",
    ),
]


@pytest.fixture
def db(scratch_db: str) -> str:
    migrate(scratch_db)
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        for pid, title, year, doi, akey in CORPUS + ASCLE:
            conn.execute(
                """
                INSERT INTO papers (id, title, title_norm, abstract, year, doi, authors)
                VALUES (%s, %s, lower(regexp_replace(%s, '[^a-zA-Z0-9 ]', '', 'g')), %s, %s, %s,
                        %s)
                """,
                (
                    pid,
                    title,
                    title,
                    f"abstract body :: {akey}",
                    year,
                    doi,
                    ["Ying-Qi Hang", "Jie Wu", "Li Bai"],
                ),
            )
    return scratch_db


def pairs_by_abstract(conn: psycopg.Connection) -> set[tuple[int, int]]:
    """abstract_hash WITH the sibling rule: same abstract AND similar title."""
    rows = conn.execute(
        f"""
        WITH grp AS (
          SELECT array_agg(id ORDER BY id) ids FROM papers
          GROUP BY md5(abstract) HAVING count(*) > 1
        ), cand AS (
          SELECT g.ids[i] a, g.ids[j] b FROM grp g,
               generate_subscripts(g.ids,1) i, generate_subscripts(g.ids,1) j WHERE i < j
        )
        SELECT c.a, c.b FROM cand c
        JOIN papers pa ON pa.id=c.a JOIN papers pb ON pb.id=c.b
        WHERE similarity(pa.title_norm, pb.title_norm) >= {ABSTRACT_TITLE_SIM}
          AND NOT {sibling_sql("pa.title_norm", "pb.title_norm")}
        """
    ).fetchall()
    return {(int(a), int(b)) for a, b in rows}


def pairs_by_title_trgm(conn: psycopg.Connection) -> set[tuple[int, int]]:
    rows = conn.execute(
        f"""
        SELECT a.id, b.id FROM papers a JOIN papers b
          ON a.id < b.id AND a.year = b.year
        WHERE similarity(a.title_norm, b.title_norm) >= {TRGM_THRESHOLD}
          AND a.title_norm <> b.title_norm
          AND NOT {sibling_sql("a.title_norm", "b.title_norm")}
        """
    ).fetchall()
    return {(int(a), int(b)) for a, b in rows}


def pairs_by_title_exact(conn: psycopg.Connection) -> set[tuple[int, int]]:
    rows = conn.execute(
        """
        SELECT ids[1], unnest(ids[2:]) FROM (
          SELECT array_agg(id ORDER BY id) ids FROM papers
          WHERE year IS NOT NULL AND length(title_norm) >= 20
          GROUP BY title_norm, year HAVING count(*) > 1) g
        """
    ).fetchall()
    return {(int(a), int(b)) for a, b in rows}


def test_asthma_trio_still_merges_despite_three_distinct_dois(db: str) -> None:
    """The fixture that vetoes distinct-DOI-means-different-paper."""
    with psycopg.connect(db, autocommit=True) as conn:
        exact = pairs_by_title_exact(conn)
        abstract = pairs_by_abstract(conn)

    # Identical titles, same year: exact-title links all three.
    assert (561, 23693) in exact and (561, 23694) in exact
    # And the two Figshare deposits also share an abstract, so the sibling
    # rule keeps them (same abstract AND same title = duplicate).
    assert (23693, 23694) in abstract
    # Three distinct DOIs, one paper — the reason DOI-difference is not a
    # non-duplicate signal.
    assert (
        len(
            {
                "10.1186/s12911-026-03371-x",
                "10.6084/m9.figshare.c.8354879",
                "10.6084/m9.figshare.c.8354879.v1",
            }
        )
        == 3
    )


def test_supplementary_files_merge_in_versioned_pairs_only(db: str) -> None:
    """The enumerator rule, both directions at once: 'Additional file 1'
    merges with its own .v1 deposit, and never with 'Additional file 2'."""
    with psycopg.connect(db, autocommit=True) as conn:
        exact = pairs_by_title_exact(conn)
        trgm = pairs_by_title_trgm(conn)
        abstract = pairs_by_abstract(conn)

    for a, b in ((174581, 174610), (174600, 174607), (174578, 174592)):
        assert (a, b) in exact, f"versioned pair {a}/{b} must still merge"
        assert (a, b) in abstract, f"{a}/{b} share an abstract and a title"

    across = [(174581, 174600), (174581, 174578), (174600, 174578)]
    for a, b in across:
        assert (a, b) not in trgm, f"{a}/{b} differ only by file number"
        assert (a, b) not in abstract
        assert (a, b) not in exact


def test_parent_paper_never_merges_with_its_supplementary_files(db: str) -> None:
    """The sibling rule's headline case: the supplementary titles CONTAIN
    the parent title, so trigram similarity is high, but they are parts of
    the paper, not copies of it."""
    with psycopg.connect(db, autocommit=True) as conn:
        trgm = pairs_by_title_trgm(conn)
        abstract = pairs_by_abstract(conn)
    for supp in (174581, 174600, 174578, 174610, 174607, 174592):
        for parent in (561, 23693, 23694):
            pair = (min(parent, supp), max(parent, supp))
            assert pair not in abstract
            assert pair not in trgm, f"parent {parent} must not merge with file {supp}"


def test_ascle_family_documents_a_known_recall_gap(db: str) -> None:
    """A known duplicate family the cascade currently MISSES, pinned so the
    gap cannot be forgotten or silently "fixed" by a threshold change nobody
    measured. Measured title similarities in the live corpus:

        arXiv 2023  <-> JMIR preprint 2024 : 0.914  (below the 0.92 gate)
        JMIR preprint <-> JMIR published   : 0.694  (the ": Development and
                                                     Evaluation Study" suffix)
        arXiv 2023  <-> JMIR published     : 0.725

    Phase 1 recorded this family as "trigram territory". It is not, at the
    shipped threshold. Two candidate closures, neither implemented: drop
    the PREPRINT pass to 0.90 (its curve is nearly flat, so the cost is
    ~79 pairs) which catches the 0.914 edge; and add the mechanical JMIR
    rule 10.2196/preprints.N -> 10.2196/N, which is deterministic and
    closes the 0.694 edge that no title threshold can reach safely.
    """
    with psycopg.connect(db, autocommit=True) as conn:
        trgm = pairs_by_title_trgm(conn)
        sims = {
            (a, b): float(
                conn.execute(
                    "SELECT similarity(x.title_norm, y.title_norm) FROM papers x, papers y"
                    " WHERE x.id=%s AND y.id=%s",
                    (a, b),
                ).fetchone()[0]  # type: ignore[index]
            )
            for a, b in ((22960, 23153), (23153, 23086), (22960, 23086))
        }

    # The gap, asserted as it actually is.
    assert sims[(22960, 23153)] < TRGM_THRESHOLD
    assert sims[(23153, 23086)] < TRGM_THRESHOLD
    assert (23086, 23153) not in trgm

    # ...but the arXiv/preprint edge is within reach of a 0.90 threshold,
    # which is the specific claim behind the proposed change.
    assert sims[(22960, 23153)] >= 0.90

    # The mechanical DOI relationship that no title rule needs to guess.
    assert "10.2196/preprints.60601".replace("/preprints.", "/") == "10.2196/60601"


def test_id_exact_actually_fires_on_a_shared_pmid(scratch_db: str) -> None:
    """Prove the arm CAN fire. It never has, on any corpus.

    id_exact has proposed zero pairs for the entire life of this project.
    doi_exact also proposes zero, but that zero has a MECHANISM — `doi` has a
    UNIQUE index, so a collision cannot survive insert to become a pair.
    id_exact had no such explanation: `papers_pubmed_id_idx` and
    `papers_arxiv_id_idx` are plain partial indexes, so collisions are
    structurally possible and simply do not occur in this corpus (verified
    2026-08-14: 0 colliding groups across 44,517 PMIDs and 95 arXiv ids).

    A zero from an arm never proven able to fire is the blind-instrument
    failure in another costume: it supports "I saw no duplicates" and cannot
    support "there are none". This test is the calibration.
    """
    migrate(scratch_db)
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        rows = [
            # Same PMID, different DOIs and titles: the cross-source glue case
            # the arm exists for — one record from OpenAlex, one from PubMed.
            (1, "Deep learning for clinical notes", "10.1/a", "38000001", None),
            (2, "Deep-learning for clinical notes.", "10.1/b", "38000001", None),
            # Same arXiv id, two versions.
            (3, "Attention is all you need", "10.2/a", None, "1706.03762"),
            (4, "Attention Is All You Need (v5)", "10.2/b", None, "1706.03762"),
            # Controls: distinct ids must NOT pair.
            (5, "Unrelated paper", "10.3/a", "38000002", None),
            (6, "Another unrelated paper", "10.3/b", None, "2101.00001"),
        ]
        for pid, title, doi, pmid, arxiv in rows:
            conn.execute(
                """
                INSERT INTO papers (id, title, title_norm, year, doi, pubmed_id, arxiv_id)
                VALUES (%s, %s, lower(%s), 2023, %s, %s, %s)
                """,
                (pid, title, title, doi, pmid, arxiv),
            )
        pairs = find_pairs(conn, strategies=["id_exact"])

    got = {(min(p.a, p.b), max(p.a, p.b)) for p in pairs}
    assert got == {(1, 2), (3, 4)}, f"id_exact did not fire as expected: {got}"
    assert all(p.strategy == "id_exact" for p in pairs)
    # The controls stayed apart — the arm is selective, not indiscriminate.
    assert not any(5 in (p.a, p.b) or 6 in (p.a, p.b) for p in pairs)
