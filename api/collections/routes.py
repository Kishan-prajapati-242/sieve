"""Collections and screening: the reviewer's workflow over search results.

Six endpoints, no more than the brief's acceptance needs:

    POST   /api/collections                       create a question
    GET    /api/collections                       list, with decision counts
    GET    /api/collections/{id}                  the collection + its papers
    PUT    /api/collections/{id}/screenings/{pid} decide (idempotent upsert)
    DELETE /api/collections/{id}/screenings/{pid} undecide
    GET    /api/collections/{id}/export.bib       BibTeX
    GET    /api/collections/{id}/export.csv       CSV, with the decisions

Screening is an UPSERT on the (collection_id, paper_id) primary key, so
changing a decision is the same request as making it. That matters for a
UI: a reviewer clicking "include" then "exclude" produces one row with the
later decision, not two rows and a tiebreak.

Export defaults to the included papers, since "give me my BibTeX" means
the ones that made the cut; ?decision=... overrides it. Raw SQL, like every
other reporting path here.
"""

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from api.auth.routes import CurrentUser
from api.collections import screening as screening_view
from api.collections.agreement import agreement_report
from api.collections.bibtex import to_bibtex
from api.collections.members import (
    CAN_INVITE,
    CAN_RESOLVE,
    CAN_SCREEN,
    CAN_VIEW,
    InviteError,
    accept_invite,
    create_invite,
    list_members,
    remove_member,
    role_for,
)
from api.collections.spreadsheet import to_csv
from api.db.pool import get_pool

logger = logging.getLogger("sieve.collections")

router = APIRouter(prefix="/api/collections")

Decision = Literal["include", "exclude", "maybe"]
# Annotated rather than a Query() default: a call in a default argument is
# evaluated once at import and shared by every request (ruff B008).
DecisionFilter = Annotated[Decision | None, Query()]

LIST_SQL = """
SELECT c.id, c.name, c.question, c.created_at,
       -- YOUR counts, not the team's.
       --
       -- These aggregated every screener's decisions, which leaks judgement in
       -- bulk: if you have screened 5 papers and the card says 12 included,
       -- you have learned that colleagues included 7 more — before you have
       -- looked at them. Same class of bug as the export leak, one level up.
       count(*) FILTER (WHERE s.user_id = %(user_id)s)                AS screened,
       count(*) FILTER (WHERE s.user_id = %(user_id)s
                        AND s.decision = 'include')                   AS included,
       count(*) FILTER (WHERE s.user_id = %(user_id)s
                        AND s.decision = 'exclude')                   AS excluded,
       count(*) FILTER (WHERE s.user_id = %(user_id)s
                        AND s.decision = 'maybe')                     AS maybe,
       -- Team VOLUME is fine and useful: it says how much work has been done,
       -- never what anyone concluded. Progress is not a judgement.
       count(s.paper_id)                              AS team_screened,
       count(DISTINCT s.user_id)                      AS screener_count,
       c.screening_mode
FROM collections c
JOIN collection_members m ON m.collection_id = c.id AND m.user_id = %(user_id)s
LEFT JOIN screenings s ON s.collection_id = c.id
GROUP BY c.id
ORDER BY c.created_at DESC, c.id DESC
"""

# BLINDING SURVIVES THE EXPORT PATH.
#
# This query previously joined screenings with no user filter, which was
# correct when a paper had exactly one decision. Post-0016 it would return
# EVERY screener's row — including their notes — to anyone who could reach the
# collection, and a CSV download would have been a way to read a co-screener's
# reasoning before deciding. That defeats blinding more thoroughly than the UI
# ever could, because it does not even require being subtle about it.
#
# `see_all` is true only for roles that may already read everything at
# reconciliation (owners). For everyone else the join is scoped to their own
# user_id, so their own notes come back and nobody else's ever do.
#
# The RESOLUTION is visible to every member regardless: it is the collection's
# official answer, not a private judgement, and hiding it from the people doing
# the work would be perverse.
PAPERS_SQL = """
SELECT p.id, p.doi, p.title, p.abstract, p.year, p.venue, p.citation_count,
       p.is_retracted, p.authors, p.arxiv_id, p.pubmed_id,
       s.decision, s.note, s.decided_at,
       su.email        AS screener,
       r.decision      AS resolved_decision,
       r.note          AS resolved_note,
       ru.email        AS resolved_by
FROM screenings s
JOIN papers p ON p.id = s.paper_id
JOIN users su ON su.id = s.user_id
LEFT JOIN screening_resolutions r
       ON r.collection_id = s.collection_id AND r.paper_id = s.paper_id
LEFT JOIN users ru ON ru.id = r.resolved_by
WHERE s.collection_id = %(collection_id)s
  AND (%(see_all)s OR s.user_id = %(user_id)s)
  AND (%(decision)s::text IS NULL OR s.decision = %(decision)s)
ORDER BY s.decided_at DESC, p.id, su.email
"""

# Per-screener now. Two members screening the same paper at the same instant
# are two DIFFERENT primary keys, so they cannot collide — concurrent screening
# is safe by schema rather than by locking, which is the point of the key
# change. The conflict target is the full key: a reviewer changing their own
# mind still updates in place.
UPSERT_SQL = """
INSERT INTO screenings (collection_id, paper_id, user_id, decision, note)
VALUES (%(collection_id)s, %(paper_id)s, %(user_id)s, %(decision)s, %(note)s)
ON CONFLICT (collection_id, paper_id, user_id)
DO UPDATE SET decision = EXCLUDED.decision, note = EXCLUDED.note, decided_at = now()
RETURNING decision, note, decided_at
"""


class CollectionCreate(BaseModel):
    # Fixed at creation on purpose — switching mid-review leaves most papers
    # screened once where the mode expects several, and "partially
    # double-screened" is a state nobody wants to design around.
    screening_mode: Literal["solo", "blind"] = "solo"
    name: str = Field(min_length=1, max_length=200)
    question: str | None = Field(default=None, max_length=2000)


class ScreeningDecision(BaseModel):
    decision: Decision
    note: str | None = Field(default=None, max_length=2000)


class CollectionSummary(BaseModel):
    id: int
    name: str
    question: str | None
    created_at: Any
    screened: int = 0
    included: int = 0
    excluded: int = 0
    maybe: int = 0
    # Volume, not judgement — safe to show under blinding.
    team_screened: int = 0
    screener_count: int = 0
    screening_mode: str = "solo"


@router.post("", status_code=201)
def create_collection(
    body: CollectionCreate, user: CurrentUser
) -> CollectionSummary:
    with get_pool().connection() as conn:
        row = conn.execute(
            "INSERT INTO collections (name, question, user_id, screening_mode)"
            " VALUES (%s, %s, %s, %s) RETURNING id, name, question, created_at",
            (body.name, body.question, user["id"], body.screening_mode),
        ).fetchone()
        assert row is not None
        # The creator is the first member. collections.user_id is kept as the
        # original-owner record, but every permission check goes through
        # membership from here.
        conn.execute(
            "INSERT INTO collection_members (collection_id, user_id, role)"
            " VALUES (%s, %s, 'owner')",
            (row[0], user["id"]),
        )
    assert row is not None
    return CollectionSummary(id=row[0], name=row[1], question=row[2], created_at=row[3])


@router.get("")
def list_collections(user: CurrentUser) -> list[CollectionSummary]:
    with get_pool().connection() as conn:
        rows = conn.execute(LIST_SQL, {"user_id": user["id"]}).fetchall()
    return [
        CollectionSummary(
            id=r[0],
            name=r[1],
            question=r[2],
            created_at=r[3],
            screened=r[4],
            included=r[5],
            excluded=r[6],
            maybe=r[7],
            team_screened=r[8],
            screener_count=r[9],
            screening_mode=r[10],
        )
        for r in rows
    ]


def _fetch_papers(
    conn: Any,
    collection_id: int,
    decision: str | None,
    *,
    user_id: int,
    see_all: bool,
) -> list[dict[str, Any]]:
    """Papers with screening, scoped to what this caller may read.

    `see_all` is not a convenience flag — it is the blinding boundary, and it
    is passed explicitly at every call site so that adding a new export cannot
    accidentally default to leaking.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            PAPERS_SQL,
            {
                "collection_id": collection_id,
                "decision": decision,
                "user_id": user_id,
                "see_all": see_all,
            },
        )
        rows: list[dict[str, Any]] = cur.fetchall()
        return rows


def _require_role(conn: Any, collection_id: int, user_id: int, allowed: frozenset[str]) -> str:
    """The caller's role, or 404 if they lack the permission.

    404 rather than 403 throughout: a 403 confirms the collection exists, which
    tells an attacker enumerating ids exactly what they wanted to know. The
    same reasoning the single-owner check already used, extended to roles.
    """
    role = role_for(conn, collection_id, user_id)
    if role is None or role not in allowed:
        raise HTTPException(status_code=404, detail=f"no collection {collection_id}")
    return role


def _require_collection(conn: Any, collection_id: int, user_id: int) -> tuple[Any, ...]:
    """Fetch a collection the caller owns.

    The ownership predicate is in the WHERE clause, not an `if` after the
    fetch: a row the caller does not own must never be loaded, and a 404 (not
    a 403) is returned so the API does not confirm that someone else's
    collection id exists. Legacy rows with user_id IS NULL match no caller.
    """
    _require_role(conn, collection_id, user_id, CAN_VIEW)
    row = conn.execute(
        "SELECT id, name, question, created_at, screening_mode FROM collections WHERE id = %s",
        (collection_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no collection {collection_id}")
    return tuple(row)


@router.get("/{collection_id}")
def get_collection(
    collection_id: int,
    user: CurrentUser,
    decision: DecisionFilter = None,
) -> dict[str, Any]:
    with get_pool().connection() as conn:
        row = _require_collection(conn, collection_id, user["id"])
        role = role_for(conn, collection_id, user["id"])
        papers = _fetch_papers(
            conn,
            collection_id,
            decision,
            user_id=user["id"],
            see_all=role in CAN_RESOLVE,
        )
    return {
        "id": row[0],
        "name": row[1],
        "question": row[2],
        "created_at": row[3],
        "papers": papers,
    }


@router.put("/{collection_id}/screenings/{paper_id}")
def screen(
    collection_id: int,
    paper_id: int,
    body: ScreeningDecision,
    user: CurrentUser,
) -> dict[str, Any]:
    """Record or change a decision. Idempotent: the same PUT twice leaves
    one row, and a different decision replaces the old one in place."""
    with get_pool().connection() as conn:
        _require_role(conn, collection_id, user["id"], CAN_SCREEN)
        if conn.execute("SELECT 1 FROM papers WHERE id = %s", (paper_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail=f"no paper {paper_id}")
        row = conn.execute(
            UPSERT_SQL,
            {
                "collection_id": collection_id,
                "paper_id": paper_id,
                "user_id": user["id"],
                "decision": body.decision,
                "note": body.note,
            },
        ).fetchone()
    assert row is not None
    return {
        "collection_id": collection_id,
        "paper_id": paper_id,
        "decision": row[0],
        "note": row[1],
        "decided_at": row[2],
    }


@router.delete("/{collection_id}/screenings/{paper_id}", status_code=204)
def unscreen(
    collection_id: int,
    paper_id: int,
    user: CurrentUser,
) -> Response:
    with get_pool().connection() as conn:
        # Membership first, and the DELETE is scoped to the caller's own row:
        # withdrawing your judgement is yours to do, removing someone else's
        # is not.
        _require_role(conn, collection_id, user["id"], CAN_SCREEN)
        deleted = conn.execute(
            "DELETE FROM screenings WHERE collection_id = %s AND paper_id = %s"
            " AND user_id = %s RETURNING paper_id",
            (collection_id, paper_id, user["id"]),
        ).fetchone()
    if deleted is None:
        raise HTTPException(status_code=404, detail="no such screening")
    return Response(status_code=204)


@router.get("/{collection_id}/export.bib")
def export_bibtex(
    collection_id: int,
    user: CurrentUser,
    decision: DecisionFilter = "include",
) -> Response:
    """BibTeX for the collection. Defaults to the included papers, because
    that is what "export my collection" means to a reviewer."""
    with get_pool().connection() as conn:
        row = _require_collection(conn, collection_id, user["id"])
        role = role_for(conn, collection_id, user["id"])
        papers = _fetch_papers(
            conn,
            collection_id,
            decision,
            user_id=user["id"],
            see_all=role in CAN_RESOLVE,
        )
    name = str(row[1]).replace('"', "")
    return Response(
        content=to_bibtex(papers),
        media_type="application/x-bibtex",
        headers={"content-disposition": f'attachment; filename="{name}.bib"'},
    )


@router.get("/{collection_id}/export.csv")
def export_csv(
    collection_id: int,
    user: CurrentUser,
    decision: DecisionFilter = None,
) -> Response:
    """The collection as a spreadsheet.

    Defaults to ALL papers rather than only the included ones, unlike the
    BibTeX export. The two exports answer different questions: BibTeX is "give
    me the citations that made the cut", CSV is "show someone the screening",
    and a screening record with the exclusions removed is not a screening
    record.
    """
    with get_pool().connection() as conn:
        row = _require_collection(conn, collection_id, user["id"])
        role = role_for(conn, collection_id, user["id"])
        papers = _fetch_papers(
            conn,
            collection_id,
            decision,
            user_id=user["id"],
            see_all=role in CAN_RESOLVE,
        )
    name = str(row[1]).replace('"', "")
    return Response(
        content=to_csv(papers),
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="{name}.csv"'},
    )


# ============================================================ COLLABORATION ==


class InviteCreate(BaseModel):
    # 'resolver' can adjudicate but not administer — the shape a supervisor
    # needs. Owners are not invitable: ownership is transferred deliberately,
    # not handed out through a link.
    role: Literal["resolver", "screener", "viewer"] = "screener"


class ResolutionBody(BaseModel):
    decision: Decision
    note: str | None = Field(default=None, max_length=4000)


@router.get("/{collection_id}/members")
def get_members(collection_id: int, user: CurrentUser) -> dict[str, Any]:
    with get_pool().connection() as conn:
        role = _require_role(conn, collection_id, user["id"], CAN_VIEW)
        return {
            "members": list_members(conn, collection_id),
            "your_role": role,
            # Progress is visible to everyone: knowing a colleague has screened
            # 200 papers reveals no judgement, and hiding it makes coordination
            # impossible for no privacy gain.
            "progress": screening_view.progress(conn, collection_id),
        }


@router.post("/{collection_id}/invites", status_code=201)
def post_invite(collection_id: int, body: InviteCreate, user: CurrentUser) -> dict[str, Any]:
    """Mint a single-use invite link.

    The plaintext token is returned exactly once and never stored — only its
    hash is. Losing it means minting another, which is the correct tradeoff for
    something that grants access to a collection.
    """
    with get_pool().connection() as conn:
        _require_role(conn, collection_id, user["id"], CAN_INVITE)
        token = create_invite(conn, collection_id, body.role, user["id"])
    return {"token": token, "role": body.role, "collection_id": collection_id}


@router.post("/invites/{token}/accept")
def post_accept_invite(token: str, user: CurrentUser) -> dict[str, Any]:
    with get_pool().connection() as conn:
        try:
            collection_id = accept_invite(conn, token, user["id"])
        except InviteError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"collection_id": collection_id}


@router.delete("/{collection_id}/members/{member_id}", status_code=204)
def delete_member(collection_id: int, member_id: int, user: CurrentUser) -> Response:
    with get_pool().connection() as conn:
        # Leaving is always allowed; removing someone else needs ownership.
        if member_id != user["id"]:
            _require_role(conn, collection_id, user["id"], CAN_INVITE)
        else:
            _require_role(conn, collection_id, user["id"], CAN_VIEW)
        try:
            remove_member(conn, collection_id, member_id)
        except InviteError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


@router.get("/{collection_id}/papers/{paper_id}/screening")
def get_paper_screening(collection_id: int, paper_id: int, user: CurrentUser) -> dict[str, Any]:
    """What this caller may see about one paper — the blinding rule applied."""
    with get_pool().connection() as conn:
        _require_role(conn, collection_id, user["id"], CAN_VIEW)
        row = conn.execute(
            "SELECT screening_mode FROM collections WHERE id = %s", (collection_id,)
        ).fetchone()
        blind = bool(row and row[0] == "blind")
        return screening_view.paper_view(
            conn, collection_id, paper_id, user["id"], blind=blind
        )


@router.get("/{collection_id}/conflicts")
def get_conflicts(collection_id: int, user: CurrentUser) -> dict[str, Any]:
    """Papers where screeners disagree and nobody has resolved it.

    Derived on read — there is no conflict table to drift out of sync with the
    decisions it describes.
    """
    with get_pool().connection() as conn:
        role = _require_role(conn, collection_id, user["id"], CAN_VIEW)
        return {
            "conflicts": screening_view.conflicts(
                conn, collection_id, user_id=user["id"], see_all=role in CAN_RESOLVE
            ),
            "scoped": role not in CAN_RESOLVE,
        }


@router.get("/{collection_id}/conflicts/{paper_id}")
def get_conflict_detail(collection_id: int, paper_id: int, user: CurrentUser) -> dict[str, Any]:
    """Every call on a contested paper, WITH the notes.

    This is the one place notes cross the blind. Resolving a disagreement
    requires understanding why it happened, and the reasoning is the most
    valuable thing on the screen — the protection exists to stop anchoring
    before a judgement, and by here every judgement is already made.
    """
    with get_pool().connection() as conn:
        _require_role(conn, collection_id, user["id"], CAN_RESOLVE)
        return screening_view.paper_view(
            conn, collection_id, paper_id, user["id"], blind=True, reconciling=True
        )


@router.put("/{collection_id}/conflicts/{paper_id}")
def put_resolution(
    collection_id: int, paper_id: int, body: ResolutionBody, user: CurrentUser
) -> dict[str, Any]:
    """Settle a conflict. The individual calls underneath are untouched.

    Two resolvers racing is a real tie with no correct winner; last write wins
    and `resolved_by` records who, which is honest about what happened rather
    than pretending the race did not occur.
    """
    with get_pool().connection() as conn:
        _require_role(conn, collection_id, user["id"], CAN_RESOLVE)
        row = conn.execute(
            """
            INSERT INTO screening_resolutions
                (collection_id, paper_id, decision, note, resolved_by, self_resolved)
            VALUES (
                %(collection_id)s, %(paper_id)s, %(decision)s, %(note)s, %(user_id)s,
                -- Was the adjudicator one of the disagreeing parties? Computed
                -- here, at the moment of the ruling, because the answer can
                -- change later if they edit their own screening — and what
                -- matters is whether they were interested WHEN they ruled.
                EXISTS (
                    SELECT 1 FROM screenings
                    WHERE collection_id = %(collection_id)s
                      AND paper_id = %(paper_id)s
                      AND user_id = %(user_id)s
                )
            )
            ON CONFLICT (collection_id, paper_id) DO UPDATE
                SET decision = EXCLUDED.decision,
                    note = EXCLUDED.note,
                    resolved_by = EXCLUDED.resolved_by,
                    self_resolved = EXCLUDED.self_resolved,
                    resolved_at = now()
            RETURNING decision, note, resolved_by, resolved_at, self_resolved
            """,
            {
                "collection_id": collection_id,
                "paper_id": paper_id,
                "decision": body.decision,
                "note": body.note,
                "user_id": user["id"],
            },
        ).fetchone()
    assert row is not None
    return {
        "paper_id": paper_id,
        "decision": row[0],
        "note": row[1],
        "resolved_by": row[2],
        "resolved_at": row[3],
        # Surfaced, not hidden: a reader of this review is entitled to know the
        # tie-breaker was one of the two people who disagreed.
        "self_resolved": row[4],
    }


@router.get("/{collection_id}/agreement")
def get_agreement(collection_id: int, user: CurrentUser) -> dict[str, Any]:
    """Inter-rater agreement, with the guards that make it honest.

    Krippendorff's alpha as the headline because it admits a variable number of
    raters per paper, which is the actual situation and which Fleiss' kappa
    cannot handle without discarding data. Pairwise Cohen's kappa alongside it,
    because "you and Sam agree at 0.41" is the number somebody can act on.
    Both absent rather than estimated below their thresholds.
    """
    with get_pool().connection() as conn:
        _require_role(conn, collection_id, user["id"], CAN_VIEW)
        rows = screening_view.agreement_rows(conn, collection_id)
    return agreement_report(rows)
