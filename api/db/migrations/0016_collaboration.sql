-- Collaborative screening: membership, invitations, per-screener decisions.
--
-- The load-bearing change is the `screenings` primary key. It was
-- (collection_id, paper_id) — one decision per paper, full stop — which makes
-- independent screening impossible to represent: a second person's call would
-- overwrite the first, and the disagreement that systematic review exists to
-- measure would be silently discarded. It becomes
-- (collection_id, paper_id, user_id).
--
-- A solo collection is unchanged by this: one screener, one row per paper,
-- conflicts that can never arise. The machinery costs a lone reviewer nothing.

-- ---------------------------------------------------------------- members --

CREATE TABLE collection_members (
    collection_id BIGINT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- 'resolver' exists because the two parties to a disagreement should not
    -- adjudicate it. In real systematic review the tie-breaker is a third
    -- person for exactly that reason. It is OPTIONAL rather than required,
    -- because in the two-person case Kishan actually described there is no
    -- third party to fill it — making the correct configuration possible
    -- without making the common one impossible.
    role          TEXT NOT NULL CHECK (role IN ('owner', 'resolver', 'screener', 'viewer')),
    invited_by    BIGINT REFERENCES users(id),
    joined_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (collection_id, user_id)
);

CREATE INDEX collection_members_user_idx ON collection_members (user_id);

-- Every existing collection's owner becomes its first member. This backfill is
-- EXACT rather than a guess: collections.user_id already records who owns it.
INSERT INTO collection_members (collection_id, user_id, role)
SELECT id, user_id, 'owner' FROM collections WHERE user_id IS NOT NULL;

-- ------------------------------------------------------------ invitations --

-- Link invitations rather than email, because mail delivery reaches exactly
-- one address until a sending domain is verified — and because a shareable
-- link is what Figma and Notion actually use regardless of email.
--
-- The token is hashed at rest for the same reason session tokens are: a
-- database dump must not contain live credentials, and an invite link IS a
-- credential until it is used.
CREATE TABLE collection_invites (
    token_hash    TEXT PRIMARY KEY,
    collection_id BIGINT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    role          TEXT NOT NULL CHECK (role IN ('resolver', 'screener', 'viewer')),
    created_by    BIGINT NOT NULL REFERENCES users(id),
    expires_at    TIMESTAMPTZ NOT NULL,
    used_at       TIMESTAMPTZ,
    used_by       BIGINT REFERENCES users(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX collection_invites_collection_idx ON collection_invites (collection_id);

-- ----------------------------------------------------------------- mode ----

-- 'solo'  : one screener, no reconciliation
-- 'blind' : every member screens every paper without seeing others' calls
--
-- Fixed at creation. Switching mid-review would leave most papers screened
-- once where the mode expects several, and "partially double-screened" is a
-- state nobody wants to design around. Changing your mind means a new
-- collection — a real limitation, stated rather than engineered around.
ALTER TABLE collections
    ADD COLUMN screening_mode TEXT NOT NULL DEFAULT 'solo'
        CHECK (screening_mode IN ('solo', 'blind'));

-- ----------------------------------------------- per-screener decisions ----

ALTER TABLE screenings ADD COLUMN user_id BIGINT REFERENCES users(id);

UPDATE screenings s
   SET user_id = c.user_id
  FROM collections c
 WHERE c.id = s.collection_id
   AND c.user_id IS NOT NULL;

-- Screenings in ownerless collections are unreachable by anyone — those
-- collections predate accounts and match no session — so the rows are dead
-- data with no owner to attribute them to. Deleted rather than given a
-- fabricated one.
DELETE FROM screenings WHERE user_id IS NULL;

ALTER TABLE screenings ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE screenings DROP CONSTRAINT screenings_pkey;
ALTER TABLE screenings ADD PRIMARY KEY (collection_id, paper_id, user_id);

-- The conflicts query groups by (collection_id, paper_id) to compare calls.
-- Named for what it serves rather than reusing screenings_paper_idx, which
-- already exists from 0012 on (paper_id) alone and answers a different
-- question — "which collections hold this paper" versus "which calls exist on
-- this paper here".
CREATE INDEX screenings_collection_paper_idx ON screenings (collection_id, paper_id);

-- --------------------------------------------------------- resolutions ----

-- A resolution is NOT a screening row with a flag. Storing it separately means
-- a resolver's own blind call and their reconciliation verdict stay distinct —
-- the same person can legitimately have both, and collapsing them would lose
-- the fact that they changed their mind after seeing the disagreement.
--
-- Nothing is overwritten anywhere: "Ada said include, Grace said exclude, Ada
-- resolved to include" survives in full, which is what makes a review
-- defensible months later.
CREATE TABLE screening_resolutions (
    collection_id BIGINT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    paper_id      BIGINT NOT NULL REFERENCES papers(id),
    decision      TEXT NOT NULL CHECK (decision IN ('include', 'exclude', 'maybe')),
    note          TEXT,
    resolved_by   BIGINT NOT NULL REFERENCES users(id),
    resolved_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Did the resolver settle a disagreement they were party to?
    --
    -- Recorded rather than prevented. Blocking it would deadlock the
    -- two-person case — the only people who can resolve are the two who
    -- disagreed — but leaving it invisible would hide that the tie-breaker
    -- was an interested party. Derived at write time because the answer can
    -- change afterwards if the resolver later edits their own screening, and
    -- what matters is whether they were party to it AT THE MOMENT they ruled.
    self_resolved BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (collection_id, paper_id)
);
