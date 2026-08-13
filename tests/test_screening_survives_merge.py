"""Does a screening decision survive dedup merging its paper away?

Written before Kishan spends a session screening, because the answer
determines whether that session's output is durable. Three outcomes were
possible: the judgment repoints to the survivor, it vanishes, or the merge
refuses. This pins which.
"""

import psycopg

from api.db.migrate import migrate
from api.dedup.merge import merge_group


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
            outcome = "refused"

        survivors = [r[0] for r in conn.execute("SELECT id FROM papers ORDER BY id").fetchall()]
        screenings = conn.execute("SELECT paper_id, decision FROM screenings").fetchall()

    # MEASURED 2026-08-13: the merge is REFUSED. Neither of the two outcomes
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
    assert outcome == "refused"
    assert len(survivors) == 2, "nothing merged"
    assert screenings == [(b, "include")], "the judgment is intact and un-moved"
