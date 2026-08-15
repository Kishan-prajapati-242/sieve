-- Accounts, and collections that belong to somebody.
--
-- Collections existed before users did, so every row in `collections` is
-- currently ownerless. `user_id` is therefore NULLABLE rather than NOT NULL:
-- a forward-only migration cannot invent an owner for existing rows, and
-- backfilling them to a placeholder account would fabricate data. Ownerless
-- collections are legacy and readable by nobody through the API — the query
-- filters on user_id, so a NULL owner matches no session.
--
-- Sessions are a table, not a JWT. A JWT cannot be revoked without keeping
-- server state anyway, and we already have the one piece of infrastructure a
-- session table needs. Logout has to actually log out.

CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Case-insensitive uniqueness on the stored lowercase form. A functional
-- unique index rather than CITEXT: no extension, and the index is the same
-- expression the lookup uses, so it is actually used.
CREATE UNIQUE INDEX users_email_key ON users (lower(email));

CREATE TABLE sessions (
    -- The cookie value itself: 256 bits of urandom, base64url. Not a serial,
    -- because a guessable session id is an account takeover.
    token       TEXT PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX sessions_user_id_idx ON sessions (user_id);
CREATE INDEX sessions_expires_at_idx ON sessions (expires_at);

ALTER TABLE collections ADD COLUMN user_id BIGINT REFERENCES users(id) ON DELETE CASCADE;
CREATE INDEX collections_user_id_idx ON collections (user_id);
