"""CSV export of a collection, including the screening work.

BibTeX carries citations; it carries nothing about what anyone DECIDED. The
point of handing a collection to a colleague is usually the decisions and the
notes, not the bibliography — so this exports the review, with the papers
attached, rather than the other way round.

CSV rather than xlsx: it opens in Excel, Sheets and Numbers with no
dependency, and writing real xlsx means adding openpyxl to serve one endpoint.
If a formatted workbook is wanted later this is the function to wrap, not
replace.

WHAT IS IN THE FILE DEPENDS ON WHO ASKED. Under blind screening a screener's
export carries their own rows only; an owner's carries everyone's. That
filtering happens in the query (routes.PAPERS_SQL), not here — this function
renders whatever it is handed, and putting the boundary in the SQL means a new
export route cannot forget to apply it by calling the wrong formatter.

Two details that decide whether the file actually opens correctly:

  BOM         Excel on Windows reads a UTF-8 CSV as Latin-1 unless the file
              starts with a byte-order mark, which turns every accented author
              name into mojibake. utf-8-sig writes it.
  formulas    A field beginning = + - or @ is executed by Excel and Sheets as
              a formula. Paper titles genuinely start with "-" sometimes, and
              a crafted abstract could do worse, so those fields are prefixed
              with a single quote. This is CSV injection and it is the one
              security consideration in an export.
"""

from __future__ import annotations

import csv
import io
from typing import Any

COLUMNS = [
    # Whose call this is. Meaningless in a solo collection and essential in a
    # blind one, where the same paper appears once per screener — without it
    # an owner's export is an unattributed pile of contradictory rows.
    "screener",
    "decision",
    "note",
    "decided_at",
    # The collection's official answer, visible to every member because it is
    # not a private judgement.
    "resolved_decision",
    "resolved_note",
    "resolved_by",
    "title",
    "authors",
    "year",
    "venue",
    "doi",
    "citation_count",
    "is_retracted",
    "arxiv_id",
    "pubmed_id",
    "abstract",
]

# Leading characters a spreadsheet will treat as the start of a formula.
_RISKY = ("=", "+", "-", "@", "\t", "\r")


def _safe(value: Any) -> str:
    """Render a cell, defusing anything a spreadsheet would execute."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = "; ".join(str(v) for v in value) if isinstance(value, list | tuple) else str(value)
    # A single leading quote makes Excel and Sheets treat the rest as text.
    return "'" + text if text.startswith(_RISKY) else text


def to_csv(papers: list[dict[str, Any]]) -> bytes:
    """The collection as a spreadsheet, decisions first.

    Decision columns lead because that is what the reader is being sent: the
    bibliography is context for the judgement, not the other way round.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for p in papers:
        writer.writerow([_safe(p.get(c)) for c in COLUMNS])
    # utf-8-sig: the BOM is what stops Excel mangling non-ASCII author names.
    return buf.getvalue().encode("utf-8-sig")
