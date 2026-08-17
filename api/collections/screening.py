"""Reading screening state under blinding, and finding conflicts.

THE BLINDING RULE, and why it is what it is.

Kishan's observation drives this: a colleague's REASONING is more persuasive
than their label. Seeing "exclude" invites you to wonder; seeing "exclude —
this is a protocol paper, not a trial" hands you a conclusion you will find
hard to argue with, whether or not it is right. So notes are protected MORE
strictly than decisions, in three stages:

  before you decide      you see nothing from anyone — no decisions, no notes,
                         not even how many others have screened it. A count is
                         itself a signal ("three people already looked at this
                         one") and blinding that leaks a hint is not blinding.

  after you decide       you see other members' DECISIONS but not their notes.
                         Your own call is committed and cannot be anchored
                         retroactively, and knowing a disagreement exists is
                         what makes reconciliation possible. Their reasoning
                         stays sealed, because there is nothing you should do
                         with it yet.

  at reconciliation      notes become visible to everyone who screened the
                         paper. The goal has inverted: resolving a conflict
                         REQUIRES understanding why it happened, and the notes
                         are the most valuable thing on the screen.

In solo mode none of this applies — there is nobody to be blinded from.

CONFLICT IS DERIVED, never stored. It is "more than one distinct decision on a
paper, with no resolution", which is a query over rows that already exist.
Storing it would create a second source of truth that every screening write
would have to maintain, and it would drift the first time a write path forgot.
"""

from __future__ import annotations

from typing import Any

import psycopg

# What the caller may see about a paper, given their own screening state.
MY_SCREENING_SQL = """
SELECT decision, note, decided_at
FROM screenings
WHERE collection_id = %(collection_id)s AND paper_id = %(paper_id)s AND user_id = %(user_id)s
"""

# Others' decisions WITHOUT their notes. The note column is deliberately absent
# from the projection rather than filtered in Python — a field that is never
# selected cannot be leaked by a later refactor that forgets to strip it.
OTHERS_DECISIONS_SQL = """
SELECT s.user_id, u.email, s.decision, s.decided_at
FROM screenings s JOIN users u ON u.id = s.user_id
WHERE s.collection_id = %(collection_id)s
  AND s.paper_id = %(paper_id)s
  AND s.user_id <> %(user_id)s
ORDER BY s.decided_at
"""

# At reconciliation the notes come too.
OTHERS_FULL_SQL = """
SELECT s.user_id, u.email, s.decision, s.note, s.decided_at
FROM screenings s JOIN users u ON u.id = s.user_id
WHERE s.collection_id = %(collection_id)s AND s.paper_id = %(paper_id)s
ORDER BY s.decided_at
"""

# Papers where screeners disagree and nobody has resolved it.
#
# `count(DISTINCT decision) > 1` is the definition of conflict, computed on
# read. A paper three people agreed on is not a conflict no matter how many
# screened it; a paper two people split IS one even if a third has not looked.
#
# THE LIST ITSELF IS A SIGNAL. "This paper is contested" tells you two people
# already looked and could not agree — which is precisely the hint blinding
# withholds, and arguably a stronger one than seeing a single decision, because
# it says the paper is hard. So a screener sees conflicts only on papers they
# have already decided.
#
# Resolvers see everything, because adjudicating a queue you cannot see is not
# a job. That is an accepted asymmetry and the reason the `resolver` role is
# separable from `screener`: someone who only arbitrates is never blinded by
# the queue, having no judgement of their own to bias.
CONFLICTS_SQL = """
SELECT s.paper_id,
       p.title,
       count(*)                        AS screener_count,
       count(DISTINCT s.decision)      AS distinct_decisions,
       array_agg(s.decision ORDER BY s.decided_at) AS decisions
FROM screenings s
JOIN papers p ON p.id = s.paper_id
WHERE s.collection_id = %(collection_id)s
  AND NOT EXISTS (
      SELECT 1 FROM screening_resolutions r
      WHERE r.collection_id = s.collection_id AND r.paper_id = s.paper_id
  )
  AND (
      %(see_all)s
      OR EXISTS (
          SELECT 1 FROM screenings mine
          WHERE mine.collection_id = s.collection_id
            AND mine.paper_id = s.paper_id
            AND mine.user_id = %(user_id)s
      )
  )
GROUP BY s.paper_id, p.title
HAVING count(DISTINCT s.decision) > 1
ORDER BY count(DISTINCT s.decision) DESC, s.paper_id
"""

# Progress, per member, so an owner can see who is behind without seeing calls.
PROGRESS_SQL = """
SELECT m.user_id, u.email, m.role,
       count(s.paper_id) AS screened
FROM collection_members m
JOIN users u ON u.id = m.user_id
LEFT JOIN screenings s
       ON s.collection_id = m.collection_id AND s.user_id = m.user_id
WHERE m.collection_id = %(collection_id)s
GROUP BY m.user_id, u.email, m.role
ORDER BY (m.role = 'owner') DESC, u.email
"""

AGREEMENT_ROWS_SQL = """
SELECT paper_id, user_id, decision FROM screenings WHERE collection_id = %(collection_id)s
"""


def paper_view(
    conn: psycopg.Connection,
    collection_id: int,
    paper_id: int,
    user_id: int,
    *,
    blind: bool,
    reconciling: bool = False,
) -> dict[str, Any]:
    """What this caller is allowed to know about one paper's screening.

    `reconciling` is passed by the conflict-resolution route only, and it is
    the single place notes become visible — so the rule lives at one call site
    rather than being reimplemented per view.
    """
    params = {"collection_id": collection_id, "paper_id": paper_id, "user_id": user_id}
    mine_row = conn.execute(MY_SCREENING_SQL, params).fetchone()
    mine = (
        {"decision": mine_row[0], "note": mine_row[1], "decided_at": mine_row[2]}
        if mine_row
        else None
    )

    if reconciling:
        rows = conn.execute(OTHERS_FULL_SQL, params).fetchall()
        others = [
            {
                "user_id": r[0],
                "email": r[1],
                "decision": r[2],
                "note": r[3],
                "decided_at": r[4],
            }
            for r in rows
        ]
        return {"mine": mine, "others": others, "notes_visible": True}

    # Blind AND undecided: nothing about anyone else, not even a count.
    if blind and mine is None:
        return {"mine": None, "others": [], "notes_visible": False, "blinded": True}

    rows = conn.execute(OTHERS_DECISIONS_SQL, params).fetchall()
    others = [
        {"user_id": r[0], "email": r[1], "decision": r[2], "decided_at": r[3]} for r in rows
    ]
    return {"mine": mine, "others": others, "notes_visible": False, "blinded": False}


def conflicts(
    conn: psycopg.Connection, collection_id: int, *, user_id: int, see_all: bool
) -> list[dict[str, Any]]:
    """Contested papers this caller may know about.

    Keyword-only, like `_fetch_papers`, so a new caller cannot inherit the
    permissive case by omission — there is no default to inherit.
    """
    rows = conn.execute(
        CONFLICTS_SQL,
        {"collection_id": collection_id, "user_id": user_id, "see_all": see_all},
    ).fetchall()
    return [
        {
            "paper_id": r[0],
            "title": r[1],
            "screener_count": r[2],
            "distinct_decisions": r[3],
            "decisions": r[4],
        }
        for r in rows
    ]


def progress(conn: psycopg.Connection, collection_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(PROGRESS_SQL, {"collection_id": collection_id}).fetchall()
    return [
        {"user_id": r[0], "email": r[1], "role": r[2], "screened": r[3]} for r in rows
    ]


def agreement_rows(conn: psycopg.Connection, collection_id: int) -> list[tuple[int, int, str]]:
    return [
        (int(r[0]), int(r[1]), str(r[2]))
        for r in conn.execute(AGREEMENT_ROWS_SQL, {"collection_id": collection_id}).fetchall()
    ]
