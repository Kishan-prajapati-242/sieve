-- Collection phase: when blinding applies, and who lifted it.
--
-- Phase is a property of the COLLECTION, not of an invitation. Membership
-- already exists by the time an owner decides screening is finished, so
-- re-inviting people to change what they can see would be re-issuing
-- credentials to solve a state problem.
--
-- A separate migration from 0016 rather than folded into it: 0016 has been
-- rehearsed against Neon's real rows and the runbook documents its exact
-- contents. Editing a rehearsed migration invalidates the rehearsal.

-- 'screening' : blind. Nobody sees another's call before making their own.
-- 'review'    : blinding lifted collection-wide. Every decision visible,
--               conflicts open to everyone, agreement computed over all of it.
-- 'closed'    : review finished. No new screenings, no new resolutions.
--
-- 'closed' earns its place because it is the difference between "we stopped
-- working on this" and "this is the final record". A review that gets cited
-- must not change silently afterwards, and an export taken from a closed
-- collection is a claim about a fixed state rather than a snapshot of
-- whatever happened to be true that afternoon.
ALTER TABLE collections
    ADD COLUMN phase TEXT NOT NULL DEFAULT 'screening'
        CHECK (phase IN ('screening', 'review', 'closed'));

-- WHO lifted the blind, and WHEN. A table rather than two columns because
-- reopening is permitted (see below), so phase changes are a sequence and not
-- a single fact — and "when did this review stop being blind" is exactly the
-- question a reader asks months later.
CREATE TABLE collection_phase_events (
    id            BIGSERIAL PRIMARY KEY,
    collection_id BIGINT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    from_phase    TEXT NOT NULL,
    to_phase      TEXT NOT NULL,
    changed_by    BIGINT NOT NULL REFERENCES users(id),
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- What was about to be revealed, captured at the moment of the change.
    -- Recorded rather than recomputed because the numbers move afterwards, and
    -- the interesting quantity is what the owner was shown when they decided.
    papers_revealed   INTEGER NOT NULL DEFAULT 0,
    decisions_revealed INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX collection_phase_events_collection_idx
    ON collection_phase_events (collection_id, changed_at DESC);

-- Every existing collection is mid-screening; that is what they were doing
-- before phases existed, so the default is the truthful backfill rather than a
-- convenient one.
