-- Two changes to ingest_jobs, now that a worker actually claims from it.
--
-- 1. updated_at. created_at answers "when was this enqueued"; nothing
--    answered "when did this last move". A job stuck in 'running' is the
--    thing an operator most needs to see, and locked_at only covers the
--    running case — a job that has been retrying for an hour has a fresh
--    run_after and a stale everything else.
ALTER TABLE ingest_jobs
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- 2. The claim index, rebuilt on the columns the claim actually orders by.
--
--    The original was (status, run_after) WHERE status = 'pending'. Inside
--    a partial index whose predicate pins status to one value, status is a
--    constant in every entry: it can neither narrow the scan nor order it,
--    so the leading column is dead weight and the index cannot serve the
--    ORDER BY run_after, id without a sort.
--
--    (run_after, id) matches the claim's ordering exactly, so the scan
--    walks the index from the head of the queue and stops at LIMIT. id as
--    the second column makes FIFO within a single run_after value total
--    rather than arbitrary, which is what lets SKIP LOCKED workers collide
--    at the head and step forward deterministically instead of scattering.
DROP INDEX IF EXISTS ingest_jobs_claim_idx;
CREATE INDEX ingest_jobs_claim_idx ON ingest_jobs (run_after, id)
    WHERE status = 'pending';

-- For /api/stats' queue depth and for finding what died, without a scan.
CREATE INDEX IF NOT EXISTS ingest_jobs_status_idx ON ingest_jobs (status);
