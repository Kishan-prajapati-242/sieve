"""Executing merges, reversibly.

Reversibility was NOT free. A merge as first designed destroys three
things that source_records alone cannot rebuild:

  1. WHICH record derived the deleted paper. source_records.paper_id is
     repointed to the survivor, so the old link is gone and re-deriving
     would not know where to put the result.
  2. The deleted paper's own field values. They came from raw, but raw is
     a source-shaped document and the derivation (venue fallback chain,
     abstract de-inversion, author extraction) is lossy to re-run
     identically after the client changes.
  3. The SURVIVOR's pre-merge fields. DECISION-3b overwrites its title,
     abstract, venue and citation_count from the winning side, so the
     pre-merge state of a row that still exists is lost.

The fix is a full JSONB snapshot in merges.merged_from — a column already
being written, so the cost is bytes, not machinery. rollback() restores
the deleted rows WITH THEIR ORIGINAL IDS (explicit id insert, sequence
untouched), repoints source_records back, and restores the survivor's
overwritten fields. Embeddings are deliberately NOT snapshotted: they are
derivable from text at ~7.6 ms each, and DECISION-3a's null-on-text-change
already forces a re-embed for any row whose text moved.
"""

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

# Fields DECISION-3b may move, plus the identity fields a rollback needs.
SNAPSHOT_FIELDS = (
    "id",
    "doi",
    "title",
    "title_norm",
    "abstract",
    "year",
    "venue",
    "citation_count",
    "arxiv_id",
    "pubmed_id",
    "is_retracted",
    "authors",
)


def _publication_rank(row: dict[str, Any]) -> int:
    return int(row.get("publication_rank") or 1)


def choose_survivor(members: list[dict[str, Any]]) -> dict[str, Any]:
    """DECISION-3b: published beats preprint, then lowest id."""
    return sorted(members, key=lambda m: (-_publication_rank(m), m["id"]))[0]


def merged_fields(survivor: dict[str, Any], members: list[dict[str, Any]]) -> dict[str, Any]:
    """DECISION-3b field survivorship. Published side supplies title,
    abstract and venue; ids come from whichever side has them; citations
    take the MAX because summing double-counts anyone citing both."""
    out: dict[str, Any] = {
        "title": survivor["title"],
        "title_norm": survivor["title_norm"],
        "abstract": survivor["abstract"],
        "venue": survivor["venue"],
        "citation_count": max(int(m["citation_count"] or 0) for m in members),
        "is_retracted": any(bool(m["is_retracted"]) for m in members),
    }
    for field in ("arxiv_id", "pubmed_id", "doi"):
        out[field] = survivor.get(field) or next((m[field] for m in members if m.get(field)), None)
    # Authors: keep the survivor's list unless it has none.
    out["authors"] = survivor.get("authors") or next(
        (m["authors"] for m in members if m.get("authors")), None
    )
    return out


FETCH_SQL = f"""
SELECT {", ".join(SNAPSHOT_FIELDS)},
       CASE
         WHEN EXISTS (SELECT 1 FROM source_records sr
                      WHERE sr.paper_id = p.id
                        AND (sr.source = 'arxiv' OR sr.raw->>'type' = 'preprint'))
           OR p.doi LIKE '%%/preprints.%%'
           OR p.venue ILIKE '%%arxiv%%' OR p.venue ILIKE '%%biorxiv%%'
           OR p.venue ILIKE '%%medrxiv%%' OR p.venue ILIKE '%%preprint%%'
         THEN 0
         WHEN p.venue IS NOT NULL THEN 2
         ELSE 1
       END AS publication_rank
FROM papers p WHERE p.id = ANY(%(ids)s)
"""


def merge_group(
    conn: psycopg.Connection, members_ids: list[int], strategy: str, similarity: float | None
) -> dict[str, Any]:
    """Merge one group inside the caller's transaction. Returns a summary."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(FETCH_SQL, {"ids": members_ids})
        members = cur.fetchall()
    if len(members) < 2:
        return {"skipped": True, "reason": "fewer than two live members"}

    survivor = choose_survivor(members)
    losers = [m for m in members if m["id"] != survivor["id"]]
    updates = merged_fields(survivor, members)

    # Which records point where, BEFORE anything moves.
    record_map = conn.execute(
        "SELECT id, paper_id FROM source_records WHERE paper_id = ANY(%s)",
        ([m["id"] for m in members],),
    ).fetchall()

    snapshot = {
        "survivor_id": survivor["id"],
        "survivor_before": {k: survivor[k] for k in SNAPSHOT_FIELDS},
        "deleted_papers": [{k: m[k] for k in SNAPSHOT_FIELDS} for m in losers],
        "source_record_map": [{"record_id": r[0], "paper_id": r[1]} for r in record_map],
        "member_ids": [m["id"] for m in members],
    }

    merge_row = conn.execute(
        """
        INSERT INTO merges (kept_paper_id, merged_from, strategy, similarity)
        VALUES (%s, %s, %s, %s) RETURNING id
        """,
        (survivor["id"], Jsonb(snapshot), strategy, similarity),
    ).fetchone()
    assert merge_row is not None

    # Repoint every record at the survivor, then apply survivorship, then
    # delete the losers. Order matters: the FK on source_records.paper_id
    # must never point at a deleted row.
    conn.execute(
        "UPDATE source_records SET paper_id = %s WHERE paper_id = ANY(%s)",
        (survivor["id"], [m["id"] for m in losers]),
    )
    text_moved = (
        updates["title"] != survivor["title"] or updates["abstract"] != survivor["abstract"]
    )
    conn.execute(
        """
        UPDATE papers SET title=%(title)s, title_norm=%(title_norm)s, abstract=%(abstract)s,
               venue=%(venue)s, citation_count=%(citation_count)s, is_retracted=%(is_retracted)s,
               doi=%(doi)s, arxiv_id=%(arxiv_id)s, pubmed_id=%(pubmed_id)s, authors=%(authors)s,
               embedding = CASE WHEN %(text_moved)s THEN NULL ELSE embedding END
        WHERE id=%(id)s
        """,
        {**updates, "id": survivor["id"], "text_moved": text_moved},
    )
    conn.execute("DELETE FROM papers WHERE id = ANY(%s)", ([m["id"] for m in losers],))

    return {
        "merge_id": merge_row[0],
        "survivor_id": survivor["id"],
        "deleted": [m["id"] for m in losers],
        "text_moved": text_moved,
    }


def rollback(conn: psycopg.Connection, merge_id: int) -> dict[str, Any]:
    """Undo one merge: restore deleted papers with their ORIGINAL ids,
    repoint source_records, restore the survivor's overwritten fields."""
    row = conn.execute(
        "SELECT kept_paper_id, merged_from FROM merges WHERE id = %s", (merge_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"no merge {merge_id}")
    _kept, snap = row
    if "deleted_papers" not in snap:
        raise ValueError(f"merge {merge_id} predates snapshots and cannot be rolled back")

    cols = ", ".join(SNAPSHOT_FIELDS)
    placeholders = ", ".join(f"%({f})s" for f in SNAPSHOT_FIELDS)
    for paper in snap["deleted_papers"]:
        conn.execute(
            f"INSERT INTO papers ({cols}) VALUES ({placeholders})",  # noqa: S608
            paper,
        )
    for entry in snap["source_record_map"]:
        conn.execute(
            "UPDATE source_records SET paper_id = %s WHERE id = %s",
            (entry["paper_id"], entry["record_id"]),
        )
    before = snap["survivor_before"]
    conn.execute(
        """
        UPDATE papers SET doi=%(doi)s, title=%(title)s, title_norm=%(title_norm)s,
               abstract=%(abstract)s, year=%(year)s, venue=%(venue)s,
               citation_count=%(citation_count)s, arxiv_id=%(arxiv_id)s,
               pubmed_id=%(pubmed_id)s, is_retracted=%(is_retracted)s, authors=%(authors)s,
               embedding = NULL
        WHERE id=%(id)s
        """,
        before,
    )
    conn.execute("DELETE FROM merges WHERE id = %s", (merge_id,))
    return {"restored": [p["id"] for p in snap["deleted_papers"]], "survivor": before["id"]}
