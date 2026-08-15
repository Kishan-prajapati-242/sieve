"""Collections and screening: the reviewer's workflow over search results.

Six endpoints, no more than the brief's acceptance needs:

    POST   /api/collections                       create a question
    GET    /api/collections                       list, with decision counts
    GET    /api/collections/{id}                  the collection + its papers
    PUT    /api/collections/{id}/screenings/{pid} decide (idempotent upsert)
    DELETE /api/collections/{id}/screenings/{pid} undecide
    GET    /api/collections/{id}/export.bib       BibTeX

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
from api.collections.bibtex import to_bibtex
from api.db.pool import get_pool

logger = logging.getLogger("sieve.collections")

router = APIRouter(prefix="/api/collections")

Decision = Literal["include", "exclude", "maybe"]
# Annotated rather than a Query() default: a call in a default argument is
# evaluated once at import and shared by every request (ruff B008).
DecisionFilter = Annotated[Decision | None, Query()]

LIST_SQL = """
SELECT c.id, c.name, c.question, c.created_at,
       count(s.paper_id)                                          AS screened,
       count(*) FILTER (WHERE s.decision = 'include')             AS included,
       count(*) FILTER (WHERE s.decision = 'exclude')             AS excluded,
       count(*) FILTER (WHERE s.decision = 'maybe')               AS maybe
FROM collections c
LEFT JOIN screenings s ON s.collection_id = c.id
WHERE c.user_id = %s
GROUP BY c.id
ORDER BY c.created_at DESC, c.id DESC
"""

PAPERS_SQL = """
SELECT p.id, p.doi, p.title, p.abstract, p.year, p.venue, p.citation_count,
       p.is_retracted, p.authors, p.arxiv_id, p.pubmed_id,
       s.decision, s.note, s.decided_at
FROM screenings s
JOIN papers p ON p.id = s.paper_id
WHERE s.collection_id = %(collection_id)s
  AND (%(decision)s::text IS NULL OR s.decision = %(decision)s)
ORDER BY s.decided_at DESC, p.id
"""

UPSERT_SQL = """
INSERT INTO screenings (collection_id, paper_id, decision, note)
VALUES (%(collection_id)s, %(paper_id)s, %(decision)s, %(note)s)
ON CONFLICT (collection_id, paper_id)
DO UPDATE SET decision = EXCLUDED.decision, note = EXCLUDED.note, decided_at = now()
RETURNING decision, note, decided_at
"""


class CollectionCreate(BaseModel):
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


@router.post("", status_code=201)
def create_collection(
    body: CollectionCreate, user: CurrentUser
) -> CollectionSummary:
    with get_pool().connection() as conn:
        row = conn.execute(
            "INSERT INTO collections (name, question, user_id) VALUES (%s, %s, %s)"
            " RETURNING id, name, question, created_at",
            (body.name, body.question, user["id"]),
        ).fetchone()
    assert row is not None
    return CollectionSummary(id=row[0], name=row[1], question=row[2], created_at=row[3])


@router.get("")
def list_collections(user: CurrentUser) -> list[CollectionSummary]:
    with get_pool().connection() as conn:
        rows = conn.execute(LIST_SQL, (user["id"],)).fetchall()
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
        )
        for r in rows
    ]


def _fetch_papers(conn: Any, collection_id: int, decision: str | None) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(PAPERS_SQL, {"collection_id": collection_id, "decision": decision})
        rows: list[dict[str, Any]] = cur.fetchall()
        return rows


def _require_collection(conn: Any, collection_id: int, user_id: int) -> tuple[Any, ...]:
    """Fetch a collection the caller owns.

    The ownership predicate is in the WHERE clause, not an `if` after the
    fetch: a row the caller does not own must never be loaded, and a 404 (not
    a 403) is returned so the API does not confirm that someone else's
    collection id exists. Legacy rows with user_id IS NULL match no caller.
    """
    row = conn.execute(
        "SELECT id, name, question, created_at FROM collections"
        " WHERE id = %s AND user_id = %s",
        (collection_id, user_id),
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
        papers = _fetch_papers(conn, collection_id, decision)
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
        _require_collection(conn, collection_id, user["id"])
        if conn.execute("SELECT 1 FROM papers WHERE id = %s", (paper_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail=f"no paper {paper_id}")
        row = conn.execute(
            UPSERT_SQL,
            {
                "collection_id": collection_id,
                "paper_id": paper_id,
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
        # Ownership first: without it, any signed-in user could delete any
        # screening by guessing a collection id.
        _require_collection(conn, collection_id, user["id"])
        deleted = conn.execute(
            "DELETE FROM screenings WHERE collection_id = %s AND paper_id = %s RETURNING paper_id",
            (collection_id, paper_id),
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
        papers = _fetch_papers(conn, collection_id, decision)
    name = str(row[1]).replace('"', "")
    return Response(
        content=to_bibtex(papers),
        media_type="application/x-bibtex",
        headers={"content-disposition": f'attachment; filename="{name}.bib"'},
    )
