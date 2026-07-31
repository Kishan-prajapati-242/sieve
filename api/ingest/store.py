"""The one place a paper row is written, shared by every source client.

Exists so DECISION-3a has exactly ONE implementation: the embedding is
nulled at the write site whenever title or abstract changes, and a second
source client cannot forget to do it (a per-client copy would be the same
"second invariant every path must maintain" that got the hash column
rejected). arXiv, PubMed, and OpenAlex all derive their own PaperFields
and hand them here.

Refresh propagates title, abstract, year, venue, citation_count,
is_retracted, and authors. is_retracted is the correctness driver, not
embedding freshness: DECISION-1c keeps retracted papers so a reviewer can
exclude them deliberately, but a frozen refresh would only ever flag
papers already retracted at crawl time — anything retracted later would
show no warning, in a screening tool, forever.
"""

from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from api.dedup.normalize import normalize_doi, normalize_title


@dataclass(frozen=True)
class PaperFields:
    """The derived, source-agnostic shape of a paper."""

    title: str
    abstract: str | None
    year: int | None
    venue: str | None
    citation_count: int
    doi: str | None = None
    is_retracted: bool = False
    authors: list[str] | None = None
    arxiv_id: str | None = None
    pubmed_id: str | None = None

    @property
    def title_norm(self) -> str:
        return normalize_title(self.title)


RECORD_UPSERT_SQL = """
INSERT INTO source_records (source, source_id, raw, query_name)
VALUES (%(source)s, %(source_id)s, %(raw)s, %(query_name)s)
ON CONFLICT (source, source_id)
DO UPDATE SET raw = EXCLUDED.raw,
              fetched_at = now(),
              query_name = COALESCE(source_records.query_name, EXCLUDED.query_name)
RETURNING id, paper_id
"""

PAPER_INSERT_SQL = """
INSERT INTO papers
    (doi, title, title_norm, abstract, year, venue, citation_count, arxiv_id, pubmed_id,
     is_retracted, authors)
VALUES (%(doi)s, %(title)s, %(title_norm)s, %(abstract)s, %(year)s, %(venue)s,
        %(citation_count)s, %(arxiv_id)s, %(pubmed_id)s, %(is_retracted)s, %(authors)s)
ON CONFLICT (doi) DO NOTHING
RETURNING id
"""

# DECISION-3a: null the embedding in the same statement that moves the text,
# so no path can leave a vector describing text the paper no longer has.
# The `old` CTE is what makes the comparison possible — inside SET, a bare
# column already means the OLD value, but RETURNING sees the NEW one, so the
# CTE is the only way to report whether the text actually changed.
PAPER_REFRESH_SQL = """
WITH old AS (
    SELECT id, title, abstract FROM papers WHERE id = %(id)s FOR UPDATE
)
UPDATE papers p SET
    title = %(title)s,
    title_norm = %(title_norm)s,
    abstract = %(abstract)s,
    year = %(year)s,
    venue = %(venue)s,
    citation_count = %(citation_count)s,
    is_retracted = %(is_retracted)s,
    authors = %(authors)s,
    embedding = CASE
        WHEN old.title IS DISTINCT FROM %(title)s
          OR old.abstract IS DISTINCT FROM %(abstract)s
        THEN NULL ELSE p.embedding END
FROM old
WHERE p.id = old.id
RETURNING (old.title IS DISTINCT FROM %(title)s
           OR old.abstract IS DISTINCT FROM %(abstract)s) AS text_changed
"""


def _params(fields: PaperFields) -> dict[str, Any]:
    return {
        "doi": normalize_doi(fields.doi) if fields.doi else None,
        "title": fields.title,
        "title_norm": fields.title_norm,
        "abstract": fields.abstract,
        "year": fields.year,
        "venue": fields.venue,
        "citation_count": fields.citation_count,
        "arxiv_id": fields.arxiv_id,
        "pubmed_id": fields.pubmed_id,
        "is_retracted": fields.is_retracted,
        "authors": fields.authors,
    }


def upsert_record(
    conn: psycopg.Connection,
    *,
    source: str,
    source_id: str,
    raw: dict[str, Any],
    query_name: str | None,
) -> tuple[int, int | None]:
    """Store the immutable raw record; returns (record_id, existing paper_id)."""
    row = conn.execute(
        RECORD_UPSERT_SQL,
        {
            "source": source,
            "source_id": source_id,
            "raw": Jsonb(raw),
            "query_name": query_name,
        },
    ).fetchone()
    assert row is not None  # RETURNING on upsert always yields the row
    return int(row[0]), row[1]


# Which linked record gets to write the paper's text. Deterministic and
# schema-free: the lowest-id record linked to the paper owns it. Without an
# owner, every record linked by DOI collision overwrites the paper with its
# own title on every crawl — the text flip-flops by refresh order and the
# embedding is nulled forever (docs/findings.md 2026-07-31). When the Phase 3
# cascade decides preprint-vs-published survivorship, THIS is the predicate
# it replaces.
OWNS_PAPER_SQL = """
SELECT min(id) = %(record_id)s FROM source_records WHERE paper_id = %(paper_id)s
"""


def owns_paper(conn: psycopg.Connection, record_id: int, paper_id: int) -> bool:
    row = conn.execute(OWNS_PAPER_SQL, {"record_id": record_id, "paper_id": paper_id}).fetchone()
    return bool(row and row[0])


def refresh_paper(conn: psycopg.Connection, paper_id: int, fields: PaperFields) -> bool:
    """Propagate mutable fields onto an existing paper. Returns whether the
    embedding-relevant text changed (and was therefore nulled)."""
    row = conn.execute(PAPER_REFRESH_SQL, {"id": paper_id, **_params(fields)}).fetchone()
    assert row is not None
    return bool(row[0])


def create_or_link_paper(
    conn: psycopg.Connection, record_id: int, fields: PaperFields
) -> tuple[int, str]:
    """Derive a paper from an unlinked record. Returns (paper_id, outcome).

    On a DOI collision the record links to the existing paper and the merge
    is logged — dedup cascade step 1, audited from day one.
    """
    params = _params(fields)
    inserted = conn.execute(PAPER_INSERT_SQL, params).fetchone()
    if inserted is not None:
        paper_id, outcome = int(inserted[0]), "new_papers"
    else:
        # NULL DOIs never conflict, so this branch implies doi is set.
        existing = conn.execute("SELECT id FROM papers WHERE doi = %s", (params["doi"],)).fetchone()
        assert existing is not None
        paper_id = int(existing[0])
        conn.execute(
            """
            INSERT INTO merges (kept_paper_id, merged_from, strategy)
            VALUES (%s, %s, 'doi_exact')
            """,
            (paper_id, Jsonb({"source_record_ids": [record_id], "title": fields.title})),
        )
        outcome = "linked_by_doi"
    conn.execute("UPDATE source_records SET paper_id = %s WHERE id = %s", (paper_id, record_id))
    return paper_id, outcome
