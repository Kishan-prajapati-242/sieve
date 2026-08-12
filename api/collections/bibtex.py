"""BibTeX serialization. No dependency — the format is small and the
correctness lives in the escaping, which a library would hide.

What makes BibTeX output wrong in practice, and what this does about it:

  Special characters. `&`, `%`, `$`, `#`, `_`, `{`, `}` are TeX syntax; a
  title containing "R&D" produces a file that will not compile. They are
  backslash-escaped. Backslash itself becomes \\textbackslash{} — escaping
  it as \\\\ would mean "line break" in TeX, which is worse than wrong.

  Capitalization. BibTeX lowercases title words unless braced, so "DNA
  sequencing with BERT" renders as "Dna sequencing with bert". Any word
  with an interior capital is wrapped in braces to pin it.

  Keys must be unique and stable. The key is author-surname + year +
  first-title-word, slugified; collisions get a numeric suffix in a
  deterministic pass, so exporting the same collection twice produces
  byte-identical output. An unstable key would make the file useless in
  version control, which is where reviewers keep these.

  Entry type. A paper with a venue is @article; one without is @misc.
  arXiv preprints have no venue, and calling a preprint an article with an
  empty journal field is the kind of thing that gets caught in review.

Fields are emitted only when present. An empty `journal = {}` is worse
than an absent one: it looks like data.
"""

import re
from typing import Any

ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

_INTERIOR_CAPITAL = re.compile(r"^\S*[a-z]\S*[A-Z]|^[A-Z]{2,}")
_KEY_STRIP = re.compile(r"[^a-z0-9]")


_SPECIAL = re.compile("[" + re.escape("".join(ESCAPES)) + "]")


def escape(text: str) -> str:
    r"""TeX-escape a field value in ONE pass.

    Sequential str.replace() calls cannot do this: the replacements
    themselves contain braces, so replacing "\" with "\textbackslash{}"
    and then escaping "{" turns it into "\textbackslash\{\}". A single
    regex substitution never revisits what it has already written.
    """
    return _SPECIAL.sub(lambda m: ESCAPES[m.group()], text)


def protect_caps(title: str) -> str:
    """Brace words BibTeX would otherwise lowercase: acronyms (DNA, BERT)
    and interior capitals (pyTorch). Leaves ordinary Capitalized words
    alone — bracing every capital produces unreadable source."""
    return " ".join(
        "{" + word + "}" if _INTERIOR_CAPITAL.match(word) else word for word in title.split()
    )


def entry_key(paper: dict[str, Any]) -> str:
    """author-year-word, slugified. Deterministic for a given paper."""
    authors = paper.get("authors") or []
    surname = authors[0].split()[-1].lower() if authors else "anon"
    year = str(paper.get("year") or "nd")
    title = (paper.get("title") or "").split()
    first = title[0].lower() if title else "untitled"
    parts = [_KEY_STRIP.sub("", part) for part in (surname, year, first)]
    return "".join(p for p in parts if p) or "entry"


def unique_keys(papers: list[dict[str, Any]]) -> list[str]:
    """Keys in input order, collisions suffixed a, b, c...

    Deterministic so the same collection exports byte-identically every
    time — these files live in version control.
    """
    counts: dict[str, int] = {}
    keys = []
    for paper in papers:
        base = entry_key(paper)
        seen = counts.get(base, 0)
        counts[base] = seen + 1
        keys.append(base if seen == 0 else f"{base}{chr(ord('a') + seen - 1)}")
    return keys


def format_entry(paper: dict[str, Any], key: str) -> str:
    """One entry. @article when it has a venue, @misc when it does not —
    a preprint is not an article with a blank journal."""
    venue = paper.get("venue")
    fields: list[tuple[str, str]] = []
    if paper.get("title"):
        fields.append(("title", protect_caps(escape(paper["title"]))))
    if paper.get("authors"):
        fields.append(("author", " and ".join(escape(a) for a in paper["authors"])))
    if paper.get("year"):
        fields.append(("year", str(paper["year"])))
    if venue:
        fields.append(("journal", escape(venue)))
    if paper.get("doi"):
        fields.append(("doi", escape(paper["doi"])))
    if paper.get("arxiv_id"):
        fields.append(("eprint", escape(paper["arxiv_id"])))
        fields.append(("archiveprefix", "arXiv"))
    if paper.get("pubmed_id"):
        fields.append(("pmid", escape(paper["pubmed_id"])))
    # The reviewer's own decision travels with the entry: an exported
    # library that loses why a paper was included is half an export.
    if paper.get("note"):
        fields.append(("note", escape(paper["note"])))

    kind = "article" if venue else "misc"
    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
    return f"@{kind}{{{key},\n{body}\n}}"


def to_bibtex(papers: list[dict[str, Any]]) -> str:
    """A whole collection. Trailing newline: it is a text file."""
    keys = unique_keys(papers)
    if not papers:
        return ""
    return "\n\n".join(format_entry(p, k) for p, k in zip(papers, keys, strict=True)) + "\n"
