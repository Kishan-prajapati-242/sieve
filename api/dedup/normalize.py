"""Normalization for identity fields (DOI, title).

Lives in dedup/ because these functions define the key space the dedup
cascade matches on: two records are only comparable if both passed through
the same normalization. Ingestion applies them at write time so papers rows
are born normalized, instead of every query re-normalizing on comparison —
the trigram index needs the stored column pre-normalized anyway.

Alternative rejected: normalizing in SQL (lower(regexp_replace(...)) at
query time). It scatters the rules across every query that touches a key,
and the rules must never diverge between writers and readers.
"""

import re

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:")
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def normalize_doi(doi: str | None) -> str | None:
    """Lowercase, strip URL and 'doi:' prefixes; empty becomes None."""
    if doi is None:
        return None
    d = doi.strip().lower()
    for prefix in _DOI_PREFIXES:
        d = d.removeprefix(prefix)
    return d or None


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, drop a leading 'the'.

    Deliberately aggressive: this produces a *blocking key* for duplicate
    candidates, not a display value. The fuzzy trigram step of the cascade
    (Phase 3) does the fine discrimination.
    """
    t = _PUNCT.sub(" ", title.lower())
    t = _WS.sub(" ", t).strip()
    return t.removeprefix("the ")
