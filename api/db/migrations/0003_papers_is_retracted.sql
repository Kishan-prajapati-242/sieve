-- Retracted papers stay in the corpus, flagged — not dropped (DECISION-1c):
-- this is a screening tool, and a systematic reviewer needs to SEE a
-- retracted paper to exclude it deliberately and check what cites it.
-- Junk types (paratext, editorial, ...) are excluded at ingest instead;
-- retraction-the-type is the notice document, is_retracted is the flag on
-- the real paper, and the two are deliberately treated differently.

ALTER TABLE papers ADD COLUMN is_retracted BOOLEAN NOT NULL DEFAULT false;
