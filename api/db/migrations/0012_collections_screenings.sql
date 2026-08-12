-- Screening: the workflow the retrieval exists to serve.
--
-- A collection is one literature question. A screening is one include /
-- exclude / maybe decision about one paper within it. That is the whole
-- model — no workflow states, no assignees, no review rounds. The brief's
-- acceptance is "screening workflow usable end to end", and every field
-- beyond these is a feature nobody asked for.
--
-- Notes on the choices that are not obvious:
--
--   PRIMARY KEY (collection_id, paper_id) makes a decision unique per
--     paper per collection by construction: re-deciding is an UPDATE, not a
--     second row, so there is no "which decision is current" question and
--     no timestamp tiebreak to get wrong.
--
--   decision is CHECKed, not an enum, for the same reason as ingest_jobs:
--     migrations here run in one transaction, and altering an enum type
--     inside one is a fight.
--
--   ON DELETE CASCADE from collections: deleting a question should take
--     its decisions with it. NOT from papers — dedup deletes paper rows
--     when it merges them, and a screening decision must not vanish
--     because the paper it referred to was merged into its twin. The FK
--     stays RESTRICT so a merge that would orphan a decision fails loudly
--     instead of silently discarding a human judgment.
CREATE TABLE collections (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    question    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE screenings (
    collection_id BIGINT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    paper_id      BIGINT NOT NULL REFERENCES papers(id),
    decision      TEXT NOT NULL CHECK (decision IN ('include', 'exclude', 'maybe')),
    note          TEXT,
    decided_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (collection_id, paper_id)
);

-- "Which collections is this paper in" and the merge-time orphan check.
-- The PK covers (collection_id, paper_id); this covers the other direction.
CREATE INDEX screenings_paper_idx ON screenings (paper_id);

-- Every query, for the stats page and for building the Phase 4 eval set.
-- result_ids is an array rather than a join table: it is written once,
-- read whole, and never joined — a row per result would be 20x the rows
-- for no query anyone will run.
CREATE TABLE query_log (
    id          BIGSERIAL PRIMARY KEY,
    query       TEXT NOT NULL,
    mode        TEXT NOT NULL CHECK (mode IN ('bm25', 'vector', 'hybrid')),
    result_ids  BIGINT[],
    latency_ms  REAL,
    cache_hit   BOOLEAN,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX query_log_created_idx ON query_log (created_at DESC);
