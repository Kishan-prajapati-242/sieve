-- Core Phase 1 tables, per the brief's Part 5 data model.
--
-- The source_records / papers / merges split is the immutable-raw /
-- derived-canonical / audit-trail pattern: raw API responses are never
-- mutated after insert, canonical papers are derived from them by the dedup
-- pass, and every merge decision is logged so dedup precision can be
-- measured (bench/dedup_precision.py) instead of guessed.
--
-- papers comes first only because source_records.paper_id references it.

CREATE TABLE papers (
    id             BIGSERIAL PRIMARY KEY,
    doi            TEXT UNIQUE,      -- normalized: lowercase, no https://doi.org/ prefix
    title          TEXT NOT NULL,
    title_norm     TEXT NOT NULL,    -- lowercased, punctuation stripped; the dedup key space
    abstract       TEXT,
    year           SMALLINT,
    venue          TEXT,
    citation_count INTEGER DEFAULT 0,
    arxiv_id       TEXT,
    pubmed_id      TEXT,
    -- Generated, not trigger- or app-maintained, so it can never drift from
    -- title/abstract. Weight A (title) outranks B (abstract) in ts_rank_cd.
    fts            tsvector GENERATED ALWAYS AS (
                       setweight(to_tsvector('english', coalesce(title, '')),    'A') ||
                       setweight(to_tsvector('english', coalesce(abstract, '')), 'B')
                   ) STORED,
    embedding      halfvec(384),     -- filled by the Phase 2 embedding pipeline
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX papers_fts_idx        ON papers USING GIN (fts);
CREATE INDEX papers_title_trgm_idx ON papers USING GIN (title_norm gin_trgm_ops);
CREATE INDEX papers_year_idx       ON papers (year);

-- Deliberately NO HNSW index on embedding yet. It is created in Phase 2,
-- AFTER the bulk load and embedding backfill: an index that exists during
-- the load makes every insert pay HNSW graph maintenance, whereas building
-- it once over the loaded table is a single (parallelizable) build. The
-- Phase 2 migration will run:
--     CREATE INDEX papers_embed_idx ON papers
--         USING hnsw (embedding halfvec_cosine_ops)
--         WITH (m = 16, ef_construction = 64);

-- Raw records exactly as fetched, one row per source per paper.
-- Never mutated after insert (raw refreshes aside). This is the audit trail.
CREATE TABLE source_records (
    id         BIGSERIAL PRIMARY KEY,
    source     TEXT NOT NULL,        -- 'openalex' | 'arxiv' | 'pubmed'
    source_id  TEXT NOT NULL,        -- native ID within that source
    raw        JSONB NOT NULL,       -- untouched API response
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    paper_id   BIGINT REFERENCES papers(id),  -- set by the dedup pass
    -- The ingestion idempotency key: INSERT .. ON CONFLICT (source, source_id)
    -- makes refetching the same record converge instead of duplicating.
    UNIQUE (source, source_id)
);

-- Audit trail of every merge decision, one row per merge.
CREATE TABLE merges (
    id            BIGSERIAL PRIMARY KEY,
    kept_paper_id BIGINT NOT NULL REFERENCES papers(id),
    merged_from   JSONB NOT NULL,    -- the source_record ids and their titles
    strategy      TEXT NOT NULL,     -- cascade step that fired:
                                     -- 'doi_exact' | 'id_exact' | 'title_exact' | 'title_trgm' | 'manual'
    similarity    REAL,              -- score that triggered the merge, if fuzzy
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The job queue. This IS the queue — no Redis (DECISION-3, Phase 3), claimed
-- with FOR UPDATE SKIP LOCKED once ingestion moves to workers.
CREATE TABLE ingest_jobs (
    id           BIGSERIAL PRIMARY KEY,
    job_type     TEXT NOT NULL,      -- 'fetch_page' | 'embed_batch' | 'dedup_batch'
    payload      JSONB NOT NULL,
    dedupe_key   TEXT UNIQUE,        -- enqueuing the same work twice is a no-op
    -- CHECKed so a typo in worker code surfaces as an insert error, not as
    -- jobs that silently never match the claim query's status = 'pending'.
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'running', 'done', 'failed', 'dead')),
    attempts     SMALLINT NOT NULL DEFAULT 0,
    max_attempts SMALLINT NOT NULL DEFAULT 5,
    run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- backoff pushes this forward
    locked_at    TIMESTAMPTZ,
    locked_by    TEXT,
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Partial index: the claim query only ever scans pending jobs, and the
-- predicate keeps done/failed/dead rows out of the index entirely.
CREATE INDEX ingest_jobs_claim_idx ON ingest_jobs (status, run_after)
    WHERE status = 'pending';
