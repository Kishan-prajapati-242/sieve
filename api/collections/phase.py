"""What a collection's phase permits.

Phase and role are now BOTH inputs to every visibility question, and keeping
them in one module is the same discipline the read audit already imposes: a
route that computes `blind` itself will compute it differently.

  screening   blinding on. A screener sees nothing from anyone until they have
              decided. Conflicts are scoped to papers they have decided.
  review      blinding off, collection-wide. Every decision visible, the whole
              conflict queue open, agreement over everything.
  closed      review finished. No new screenings, no new resolutions. The
              record is fixed so an export is a claim about a final state.

TWO DECISIONS, both of which could have gone the other way.

REOPENING IS ALLOWED, not blocked.

Blocking review -> screening is tempting because reopening lets people change
calls that were already adjudicated, leaving a resolution that ruled on a
disagreement which may no longer exist. But blocking it means an owner who
advanced the phase by mistake — one click, no undo — has permanently lost blind
screening for that review, and the only recovery is recreating the collection
and losing every decision. Punishing a misclick with data loss is worse than
the state it prevents.

So reopening is permitted and STALENESS IS DERIVED. A resolution is stale when
any screening on that paper was decided after the resolution was recorded:

    stale := EXISTS (screening on this paper with decided_at > resolved_at)

No new column, no flag to maintain, and exactly the right definition — a call
that changed after the ruling means the ruling was made on different
information. It follows the same principle as conflicts being derived: state
that can be computed is not stored, because stored state drifts.

CLOSED BLOCKS WRITES, and that is the whole reason it exists. Without it
"finished" is a social convention, and a stray click months later edits a
published record with no trace that anything was ever final.
"""

from __future__ import annotations

from typing import Any, Literal

import psycopg

Phase = Literal["screening", "review", "closed"]

PHASES: tuple[Phase, ...] = ("screening", "review", "closed")

# Phases in which a screening or resolution may be written.
WRITABLE: frozenset[str] = frozenset({"screening", "review"})


def phase_of(conn: psycopg.Connection, collection_id: int) -> tuple[str, str]:
    """(screening_mode, phase) for a collection, in one round trip.

    Returned together because no caller needs one without the other: blinding
    is the conjunction of them.
    """
    row = conn.execute(
        "SELECT screening_mode, phase FROM collections WHERE id = %s", (collection_id,)
    ).fetchone()
    return (str(row[0]), str(row[1])) if row else ("solo", "screening")


def is_blind(mode: str, phase: str) -> bool:
    """Blinding requires BOTH a blind collection and the screening phase.

    This is the single definition. It used to be `mode == "blind"` computed at
    each route, which silently ignored phase — and a phase change is precisely
    a blinding change, so every one of those sites would have been wrong the
    moment phases shipped.
    """
    return mode == "blind" and phase == "screening"


def sees_all_conflicts(role: str, phase: str, can_resolve: frozenset[str]) -> bool:
    """Whether the whole conflict queue is visible.

    Resolvers always: adjudicating a queue you cannot see is not a job. Anyone,
    once the phase leaves screening: that is what lifting the blind means, and
    scoping the queue afterwards would withhold the thing the phase exists to
    reveal.
    """
    return role in can_resolve or phase != "screening"


def reveal_preview(conn: psycopg.Connection, collection_id: int) -> dict[str, Any]:
    """What advancing to `review` would expose.

    Shown to the owner before they flip it, because lifting the blind
    collection-wide is irreversible in the sense that matters: people cannot
    un-see a colleague's call. A confirmation dialog with no numbers in it is
    not informed consent.
    """
    row = conn.execute(
        """
        SELECT count(DISTINCT s.paper_id),
               count(*),
               count(DISTINCT s.user_id)
        FROM screenings s WHERE s.collection_id = %s
        """,
        (collection_id,),
    ).fetchone()
    papers, decisions, screeners = (int(row[0]), int(row[1]), int(row[2])) if row else (0, 0, 0)
    conflicts = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT paper_id FROM screenings WHERE collection_id = %s
            GROUP BY paper_id HAVING count(DISTINCT decision) > 1
        ) t
        """,
        (collection_id,),
    ).fetchone()
    return {
        "papers": papers,
        "decisions": decisions,
        "screeners": screeners,
        "conflicts": int(conflicts[0]) if conflicts else 0,
    }


def set_phase(
    conn: psycopg.Connection, collection_id: int, to_phase: str, user_id: int
) -> dict[str, Any]:
    """Change phase and record who did it, atomically.

    The event row carries the reveal counts as they stood at the moment of the
    change rather than recomputing them later, because they move afterwards and
    the interesting quantity is what the owner was looking at when they decided.
    """
    if to_phase not in PHASES:
        raise ValueError(f"unknown phase {to_phase!r}")
    preview = reveal_preview(conn, collection_id)
    # Read-then-write, under a row lock, rather than trying to get the old
    # value out of RETURNING — a CTE referenced from an UPDATE's RETURNING
    # clause does not resolve the way it reads, and it silently produced NULL.
    # FOR UPDATE serialises concurrent flips so the event log cannot record a
    # transition that never happened.
    prior = conn.execute(
        "SELECT phase FROM collections WHERE id = %s FOR UPDATE", (collection_id,)
    ).fetchone()
    from_phase = str(prior[0]) if prior else "screening"
    conn.execute("UPDATE collections SET phase = %s WHERE id = %s", (to_phase, collection_id))
    conn.execute(
        """
        INSERT INTO collection_phase_events
            (collection_id, from_phase, to_phase, changed_by,
             papers_revealed, decisions_revealed)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            collection_id,
            from_phase,
            to_phase,
            user_id,
            preview["papers"],
            preview["decisions"],
        ),
    )
    return {**preview, "from_phase": from_phase, "to_phase": to_phase}


def history(conn: psycopg.Connection, collection_id: int) -> list[dict[str, Any]]:
    """Every phase change, newest first. "When did this stop being blind" is
    the question a reader asks months later."""
    rows = conn.execute(
        """
        SELECT e.from_phase, e.to_phase, u.email, e.changed_at,
               e.papers_revealed, e.decisions_revealed
        FROM collection_phase_events e JOIN users u ON u.id = e.changed_by
        WHERE e.collection_id = %s ORDER BY e.changed_at DESC
        """,
        (collection_id,),
    ).fetchall()
    return [
        {
            "from_phase": r[0],
            "to_phase": r[1],
            "changed_by": r[2],
            "changed_at": r[3],
            "papers_revealed": r[4],
            "decisions_revealed": r[5],
        }
        for r in rows
    ]
