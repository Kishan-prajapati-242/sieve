-- Regularize dedup_review, which bench/dedup_execute.py had been creating
-- with CREATE TABLE IF NOT EXISTS.
--
-- That was fine while it held scratch output. It stopped being fine when
-- DECISION-3c routed 122 unwound merges into it: the table now holds 179
-- rows of real state — groups the cascade refused or gave back, waiting on
-- a human — and a table holding state a reviewer is expected to act on
-- belongs in the schema, not in whichever bench script happened to run first.
--
-- IF NOT EXISTS because the live database already has it with these exact
-- columns; this migration adopts it rather than recreating it.
CREATE TABLE IF NOT EXISTS dedup_review (
    id          BIGSERIAL PRIMARY KEY,
    member_ids  BIGINT[] NOT NULL,
    size        INTEGER NOT NULL,
    strategies  TEXT NOT NULL,
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
