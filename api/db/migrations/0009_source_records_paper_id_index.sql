-- The index a foreign key does NOT create.
--
-- source_records.paper_id has REFERENCED papers(id) since migration 0002,
-- and PostgreSQL does not index the referencing side of a foreign key. So
-- every lookup "which records belong to this paper" was a parallel seq scan
-- of the largest table in the database (815 MB).
--
-- Measured: the merge executor's fetch query took 1,140 ms per group, of
-- which 1,127 ms was one Parallel Seq Scan on source_records inside a
-- correlated EXISTS. Across 12,796 groups that is 3.3 hours of scanning to
-- answer questions a primary-key-sized index answers in microseconds.
-- (docs/findings.md 2026-08-01.)
--
-- Three hot paths were paying it, not just the merge:
--   * api/ingest/store.py owns_paper(), on EVERY refresh of EVERY record;
--   * api/stats.py papers-by-query attribution;
--   * the merge executor's record remap and snapshot.
CREATE INDEX source_records_paper_id_idx ON source_records (paper_id)
    WHERE paper_id IS NOT NULL;
