"""The embedding input contract (DECISION-2b). Every string that reaches the
encoder is built HERE, nowhere else.

bge-small-en-v1.5 is asymmetric: queries carry an instruction prefix,
documents never do (BAAI model card). Getting that wrong raises no error
and degrades retrieval silently — queries and documents just drift apart
in the space. That failure mode is why this trivial module exists as a
choke point with its own tests, instead of two f-strings inlined at the
call sites: the Phase 2 encoder and the search path both MUST route
through these functions, and the tests pin the contract.

Documents are title + abstract per DECISION-2b (measured: median 243
tokens, 5.6% beyond the 512 window), degrading to title-only when the
abstract is missing. No chunking — one vector per paper.
"""

# Exact string from the BAAI/bge-small-en-v1.5 model card, trailing space
# included. Applies to QUERIES ONLY.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def query_text(query: str) -> str:
    """What the encoder sees for a search query: prefix + the user's text."""
    return f"{QUERY_PREFIX}{query}"


def document_text(title: str, abstract: str | None) -> str:
    """What the encoder sees for a paper: title + abstract, NEVER a prefix."""
    if not abstract:
        return title
    return f"{title}. {abstract}"
