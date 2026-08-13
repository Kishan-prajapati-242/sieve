-- Pair-level negative constraints: "these two are NOT the same paper."
--
-- PROPOSED, not yet wired into the cascade. See docs/findings.md
-- 2026-08-13 for why this shape was chosen over the two group-keyed
-- alternatives.
--
-- The problem it solves: dedup_review is write-only. dedup_execute inserts
-- into it and nothing reads it, so a human decision to hold a group back
-- survives exactly until the next planning run. Measured 2026-08-13: a
-- re-plan re-proposed all 122 groups and all 314 papers that DECISION-3c
-- had unwound.
--
-- Why PAIR-keyed rather than group-keyed. A group is a set of ids that a
-- later corpus can dissolve — merge one member away and the recorded set
-- no longer matches anything, so the judgment silently stops applying. A
-- pair judgment has no such state: if both papers exist it holds
-- regardless of what else joined the component, and if either is gone it
-- is moot rather than wrong. That is a structural guarantee, not a policy
-- one, and it is the reason this option needs no new invariant.
--
-- Ordering: (a, b) is stored with a < b, enforced by CHECK, so a pair has
-- exactly one representation and lookup never has to try both directions.
--
-- ON DELETE CASCADE from papers, unlike screenings. A screening is a
-- judgment ABOUT a paper and must outlive a merge (it fails loudly
-- instead). A negative pair is a judgment about a RELATIONSHIP, and when
-- one side ceases to exist the relationship does too — moot, not lost.
CREATE TABLE dedup_negative_pairs (
    a          BIGINT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    b          BIGINT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    source     TEXT NOT NULL,   -- 'hand_label' | 'review' | 'rule'
    note       TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (a, b),
    CONSTRAINT dedup_negative_pairs_ordered CHECK (a < b)
);

-- The reverse direction, for "is this paper party to any refusal".
CREATE INDEX dedup_negative_pairs_b_idx ON dedup_negative_pairs (b);
