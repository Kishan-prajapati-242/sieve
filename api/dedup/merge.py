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


class ScreeningConflict(Exception):
    """Members carry DIFFERENT screening decisions in the same collection.

    Raised instead of merging, so the caller routes the group to review. The
    alternative — picking one decision — would have the machine overrule a
    human on a question the human answered twice, differently.
    """

    def __init__(self, message: str, *, collection_ids: list[int], member_ids: list[int]) -> None:
        super().__init__(message)
        self.collection_ids = collection_ids
        self.member_ids = member_ids


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

    # Earlier merges may name a loser as their kept_paper_id — a paper that
    # survived one merge can lose the next. The FK forbids deleting it, so
    # those rows follow the survivor and the old mapping is snapshotted.
    loser_ids = [m["id"] for m in losers]
    prior_merges = conn.execute(
        "SELECT id, kept_paper_id FROM merges WHERE kept_paper_id = ANY(%s)", (loser_ids,)
    ).fetchall()

    # Screening decisions follow the paper (Kishan, 2026-08-13). Until then
    # screenings.paper_id had no ON DELETE and merge_group did not repoint it,
    # so the DELETE below raised and the group was skipped — the cascade
    # silently under-merged on exactly the papers a human had screened, and a
    # later precision/recall measurement would inherit that with recall
    # understated and no trace in the metric (findings.md 2026-08-13).
    #
    # Two branches, and the split is the whole rule:
    #   same decision on both  -> collapse. There is no conflict to resolve.
    #   different decisions    -> refuse the merge, route to review. A human
    #                             disagreed with themselves about two records
    #                             that turned out to be one paper.
    # Rejected: most-recent-wins (outcome depends on timestamp order — the
    # order-dependence removed from the cap rule) and survivor's-decision-wins
    # (survivorship is chosen on METADATA quality and says nothing about which
    # judgment was better considered).
    screenings = conn.execute(
        "SELECT collection_id, paper_id, decision, note, user_id FROM screenings"
        " WHERE paper_id = ANY(%s) ORDER BY collection_id, paper_id",
        ([m["id"] for m in members],),
    ).fetchall()
    # Conflict is now per (collection, SCREENER), not per collection.
    #
    # Two screeners disagreeing about one paper is ordinary blind screening and
    # must not block a merge — that disagreement is the signal the whole
    # collaboration feature exists to capture. What still blocks a merge is ONE
    # person having called two duplicates differently, because collapsing those
    # would silently pick one of their own judgements over the other.
    by_rater: dict[tuple[int, int], set[str]] = {}
    for cid, _pid, decision, _note, uid in screenings:
        by_rater.setdefault((int(cid), int(uid)), set()).add(str(decision))
    conflicts = sorted({c for (c, _u), ds in by_rater.items() if len(ds) > 1})
    if conflicts:
        raise ScreeningConflict(
            f"collections {conflicts} hold different decisions across members "
            f"{[m['id'] for m in members]}",
            collection_ids=conflicts,
            member_ids=[m["id"] for m in members],
        )

    snapshot = {
        "survivor_id": survivor["id"],
        "survivor_before": {k: survivor[k] for k in SNAPSHOT_FIELDS},
        "deleted_papers": [{k: m[k] for k in SNAPSHOT_FIELDS} for m in losers],
        "source_record_map": [{"record_id": r[0], "paper_id": r[1]} for r in record_map],
        "prior_merge_map": [{"merge_id": r[0], "kept_paper_id": r[1]} for r in prior_merges],
        # Every screening row as it stood, so rollback can put the collapsed
        # ones back on the papers they were made about.
        "screening_map": [
            {
                "collection_id": r[0],
                "paper_id": r[1],
                "decision": r[2],
                "note": r[3],
                "user_id": r[4],
            }
            for r in screenings
        ],
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

    # ORDER IS THE WHOLE GAME here, and getting it wrong cost 536 failed
    # groups on the first run (docs/findings.md 2026-08-01):
    #   1. repoint source_records  — the FK must never point at a dead row;
    #   2. repoint prior merges    — same FK problem on merges.kept_paper_id;
    #   3. DELETE the losers       — BEFORE the survivor takes their DOI;
    #   4. update the survivor     — now that the donor row is gone, copying
    #      its DOI cannot collide with papers_doi_key.
    # Doing (4) before (3) makes two live rows briefly share a unique DOI,
    # which the constraint rejects — the survivor was inheriting a DOI from
    # a row that still existed.
    conn.execute(
        "UPDATE source_records SET paper_id = %s WHERE paper_id = ANY(%s)",
        (survivor["id"], loser_ids),
    )
    conn.execute(
        "UPDATE merges SET kept_paper_id = %s WHERE kept_paper_id = ANY(%s)",
        (survivor["id"], loser_ids),
    )
    # Collapse screenings onto the survivor before the losers die. Same
    # decision everywhere in a collection by the check above, so the survivor
    # either already carries it or inherits it; the loser rows then go.
    # Per-screener: every reviewer's call moves to the survivor independently,
    # so a merge cannot collapse two people's judgements into one.
    conn.execute(
        "INSERT INTO screenings (collection_id, paper_id, user_id, decision, note)"
        " SELECT collection_id, %s, user_id, decision, note FROM screenings"
        " WHERE paper_id = ANY(%s)"
        " ON CONFLICT (collection_id, paper_id, user_id) DO NOTHING",
        (survivor["id"], loser_ids),
    )
    conn.execute("DELETE FROM screenings WHERE paper_id = ANY(%s)", (loser_ids,))
    conn.execute("DELETE FROM papers WHERE id = ANY(%s)", (loser_ids,))

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

    # Mirror image of the merge order: give the survivor its OWN fields back
    # first, so reinserting a deleted paper cannot collide on the DOI the
    # survivor inherited from it.
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
    cols = ", ".join(SNAPSHOT_FIELDS)
    placeholders = ", ".join(f"%({f})s" for f in SNAPSHOT_FIELDS)
    for paper in snap["deleted_papers"]:
        conn.execute(
            f"INSERT INTO papers ({cols}) VALUES ({placeholders})",  # noqa: S608
            paper,
        )
    # Screenings: wipe what the merge left on the survivor for these
    # collections, then reinstate every row exactly as snapshotted.
    smap = snap.get("screening_map", [])
    if smap:
        conn.execute(
            "DELETE FROM screenings WHERE collection_id = ANY(%s) AND paper_id = ANY(%s)",
            (
                [e["collection_id"] for e in smap],
                sorted({e["paper_id"] for e in smap} | {snap["survivor_id"]}),
            ),
        )
        for e in smap:
            # user_id is read with .get so snapshots taken BEFORE collaboration
            # shipped still roll back — a merge recorded under the old schema
            # has no screener recorded, and refusing to restore it would make
            # old merges permanently irreversible. Those rows are attributed to
            # the collection's owner, which is who made them.
            conn.execute(
                "INSERT INTO screenings (collection_id, paper_id, user_id, decision, note)"
                " VALUES (%s, %s,"
                "  coalesce(%s, (SELECT user_id FROM collections WHERE id = %s)),"
                "  %s, %s) ON CONFLICT DO NOTHING",
                (
                    e["collection_id"],
                    e["paper_id"],
                    e.get("user_id"),
                    e["collection_id"],
                    e["decision"],
                    e["note"],
                ),
            )

    for entry in snap.get("prior_merge_map", []):
        conn.execute(
            "UPDATE merges SET kept_paper_id = %s WHERE id = %s",
            (entry["kept_paper_id"], entry["merge_id"]),
        )
    for entry in snap["source_record_map"]:
        conn.execute(
            "UPDATE source_records SET paper_id = %s WHERE id = %s",
            (entry["paper_id"], entry["record_id"]),
        )
    conn.execute("DELETE FROM merges WHERE id = %s", (merge_id,))
    return {"restored": [p["id"] for p in snap["deleted_papers"]], "survivor": before["id"]}
