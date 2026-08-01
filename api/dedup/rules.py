"""The rules that decide whether two records are the same paper.

The structural insight this module encodes (Kishan, 2026-08-01), which
generalizes four separate bugs into one rule:

    Records that are PARTS OF, or VERSIONS OF, a common parent inherit the
    parent's abstract. Textbook chapters share the book description,
    versioned data releases share the series description, supplementary
    files share the parent paper's abstract, proceedings volumes share a
    member paper's abstract.

    Same abstract + same title  -> duplicate.
    Same abstract + DIFFERENT titles -> siblings under a shared parent.

That is a permanent property of scholarly metadata, not an enumerable list
of bad strings, so a blocklist can only ever treat symptoms. The blocklist
still exists, but for a different job: EMBEDDING policy (a shared abstract
makes siblings mutually indistinguishable in vector space). The two roles
have different criteria and are no longer conflated.

Second rule, from the same family: titles that differ ONLY in digits are
enumerated siblings — "Additional file 1 of X" vs "Additional file 3 of X",
"Figure S4 from Y" vs "Figure S7 from Y". Trigram similarity rates them
~0.98 because one character differs, which is precisely the band where a
naive threshold looks safest.
"""

# Digits collapsed. Used to DETECT enumerated siblings, never to match them.
ENUM_NORM = "regexp_replace({}, '[0-9]+', '#', 'g')"

# True when two titles differ only by their numbers: not equal, but equal
# once digits collapse. Such a pair is refused by every fuzzy strategy.
ENUM_SIBLINGS = """(
    {a} <> {b}
    AND regexp_replace({a}, '[0-9]+', '#', 'g') = regexp_replace({b}, '[0-9]+', '#', 'g')
)"""

# Minimum title similarity for two same-abstract records to count as the
# same paper. Deliberately loose: an identical abstract is strong evidence,
# so this only has to separate "same paper, punctuation drift" from
# "different chapters of one book".
ABSTRACT_TITLE_SIM = 0.80

# Fuzzy title threshold for the same-year pass.
TRGM_THRESHOLD = 0.92

# A merge group larger than this is not merged. Real duplicate sets are
# small; a large component means a shared-parent artifact or a similarity
# chain, and both need human eyes. Measured basis: 91.9% of groups have
# exactly 2 members, and the tail above 10 was entirely bugs on first plan.
MAX_GROUP_SIZE = 8


def enum_siblings_sql(a_col: str, b_col: str) -> str:
    return ENUM_SIBLINGS.format(a=a_col, b=b_col)
