"""Does a screening decision survive dedup merging its paper away?

Written before Kishan spends a session screening, because the answer
determines whether that session's output is durable. Three outcomes were
possible: the judgment repoints to the survivor, it vanishes, or the merge
refuses. This pins which.
"""

import psycopg
import pytest

from api.db.migrate import migrate
from api.dedup.merge import ScreeningConflict, merge_group, rollback


def seed(conn: psycopg.Connection) -> tuple[int, int, int]:
    ids = []
    for i in (1, 2):
        row = conn.execute(
            "INSERT INTO papers (title, title_norm, year, doi) VALUES (%s,%s,%s,%s) RETURNING id",
            (f"Dup {i}", "dup", 2020, f"10.1/{i}"),
        ).fetchone()
        assert row is not None
        ids.append(int(row[0]))
        conn.execute(
            "INSERT INTO source_records (source, source_id, raw, paper_id)"
            " VALUES (%s,%s,'{}'::jsonb,%s)",
            ("openalex", f"W{i}", ids[-1]),
        )
    cid = conn.execute("INSERT INTO collections (name) VALUES ('Q') RETURNING id").fetchone()
    assert cid is not None
    return ids[0], ids[1], int(cid[0])


def test_merging_a_screened_paper(scratch_db: str) -> None:
    migrate(scratch_db)
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        a, b, cid = seed(conn)
        # Screen the paper that the merge will consume (the higher id loses:
        # choose_survivor prefers the richer/earlier record).
        conn.execute(
            "INSERT INTO screenings (collection_id, paper_id, decision, note)"
            " VALUES (%s,%s,'include','kept for the outcome measure')",
            (cid, b),
        )

        outcome: str
        try:
            with conn.transaction():
                merge_group(conn, [a, b], "title_exact", 1.0)
            outcome = "merged"
        except psycopg.errors.ForeignKeyViolation:
            outcome = "refused"  # the pre-fix behaviour

        survivors = [r[0] for r in conn.execute("SELECT id FROM papers ORDER BY id").fetchall()]
        screenings = conn.execute("SELECT paper_id, decision FROM screenings").fetchall()

    # PRE-FIX BASELINE, kept deliberately. Before 2026-08-13 this raised
    # ForeignKeyViolation for EVERY screened paper. It now collapses when the
    # decisions agree (see the branch tests below); this case has one screened
    # member and therefore no conflict, so it merges.
    # HISTORICAL NOTE, the behaviour this replaced: Neither of the two outcomes
    # anticipated (repoint, or vanish) — a third one. merge_group repoints
    # source_records and merges but not screenings, and screenings.paper_id
    # has no ON DELETE, so DELETE FROM papers raises.
    #
    # Safe for the judgment, blocking for dedup: dedup_execute catches
    # per-group exceptions, so screened papers would accumulate silently as
    # merge errors. Pinned here as current behaviour, NOT as desired
    # behaviour — repointing to the survivor needs a rule for the case where
    # both papers are screened in the same collection (the PK collides), and
    # that rule is Kishan's call.
    assert outcome == "merged", "the FK no longer blocks a lone screening"
    assert len(survivors) == 1
    assert screenings == [(survivors[0], "include")], "the judgment moved to the survivor"


def test_same_decision_on_both_collapses_silently(scratch_db: str) -> None:
    """Branch 1 of Kishan's rule: no conflict exists, so do not manufacture one."""
    migrate(scratch_db)
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        a, b, cid = seed(conn)
        for pid in (a, b):
            conn.execute(
                "INSERT INTO screenings (collection_id, paper_id, decision)"
                " VALUES (%s,%s,'include')",
                (cid, pid),
            )
        with conn.transaction():
            merge_group(conn, [a, b], "title_exact", 1.0)
        rows = conn.execute("SELECT paper_id, decision FROM screenings").fetchall()
        papers = [r[0] for r in conn.execute("SELECT id FROM papers").fetchall()]
    assert len(papers) == 1
    assert rows == [(papers[0], "include")], "two rows collapsed into one on the survivor"


def test_different_decisions_refuse_the_merge(scratch_db: str) -> None:
    """Branch 2: a human answered the same question twice, differently. The
    machine does not pick a winner — the group goes to review."""
    migrate(scratch_db)
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        a, b, cid = seed(conn)
        conn.execute(
            "INSERT INTO screenings (collection_id, paper_id, decision) VALUES (%s,%s,'include')",
            (cid, a),
        )
        conn.execute(
            "INSERT INTO screenings (collection_id, paper_id, decision) VALUES (%s,%s,'exclude')",
            (cid, b),
        )
        with pytest.raises(ScreeningConflict) as exc, conn.transaction():
            merge_group(conn, [a, b], "title_exact", 1.0)
        assert exc.value.collection_ids == [cid]
        assert sorted(exc.value.member_ids) == sorted([a, b])
        papers = conn.execute("SELECT count(*) FROM papers").fetchone()
        rows = conn.execute("SELECT count(*) FROM screenings").fetchone()
    assert papers == (2,), "nothing merged"
    assert rows == (2,), "both judgments intact"


def test_rollback_restores_a_collapsed_screening(scratch_db: str) -> None:
    """Reversibility has to cover the collapse, or the unwind path silently
    loses a decision it moved."""
    migrate(scratch_db)
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        a, b, cid = seed(conn)
        conn.execute(
            "INSERT INTO screenings (collection_id, paper_id, decision, note)"
            " VALUES (%s,%s,'include','on the loser')",
            (cid, b),
        )
        with conn.transaction():
            res = merge_group(conn, [a, b], "title_exact", 1.0)
        merge_id = conn.execute("SELECT id FROM merges ORDER BY id DESC LIMIT 1").fetchone()
        assert merge_id is not None
        with conn.transaction():
            rollback(conn, int(merge_id[0]))
        rows = conn.execute(
            "SELECT paper_id, decision, note FROM screenings ORDER BY paper_id"
        ).fetchall()
        papers = conn.execute("SELECT count(*) FROM papers").fetchone()
    assert res
    assert papers == (2,), "both papers back"
    assert rows == [(b, "include", "on the loser")], "the decision is back on its own paper"
