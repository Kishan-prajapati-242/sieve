-- Abstracts that are repository/publisher boilerplate rather than a
-- description of the paper. Papers whose abstract hash is listed here embed
-- as TITLE ONLY, via document_text()'s existing null-abstract branch.
--
-- A TABLE, not a computed predicate (Kishan, 2026-07-31): "shared across
-- >= 5 papers" is derived from the current corpus, so it would move when
-- arXiv and PubMed land, silently flipping a paper's embedding policy with
-- no signal — the same moving-invariant problem that got the hash column
-- rejected in DECISION-3a. An explicit list changes only when someone
-- changes it, and every row records why it is here.
--
-- Seeded from the 2026-07-31 measurement (bench/seed_boilerplate.py); the
-- long-abstract band was hand-reviewed because it is mostly LEGITIMATE
-- content (real structured abstracts, dataset and textbook descriptions
-- that happen to be shared across editions).
CREATE TABLE boilerplate_abstracts (
    abstract_md5 TEXT PRIMARY KEY,
    sample       TEXT NOT NULL,   -- first ~120 chars, so the list is auditable by eye
    reason       TEXT NOT NULL,   -- which rule or hand judgment put it here
    n_at_seed    INTEGER,         -- papers sharing it when added (context, not a rule)
    added_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
