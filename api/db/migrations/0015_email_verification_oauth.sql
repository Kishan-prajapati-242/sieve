-- Email verification by one-time code, and federated identities.
--
-- `users.password_hash` becomes NULLABLE: an account created through Google
-- has no password, and storing a random unusable one would be a lie that a
-- later "change password" flow could trip over. NULL means "this account has
-- no password credential", which is exactly true.
--
-- Codes are stored HASHED. A one-time code is a credential for the length of
-- its life, and a leaked database should not hand over live codes; sha256 is
-- adequate here where argon2 is not needed, because the input is
-- high-entropy-per-attempt only through the attempt limit, which is why
-- `attempts` exists and is enforced.

ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;
ALTER TABLE users ADD COLUMN email_verified_at TIMESTAMPTZ;

CREATE TABLE email_codes (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash  TEXT NOT NULL,
    purpose    TEXT NOT NULL CHECK (purpose IN ('verify_email')),
    attempts   SMALLINT NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only the newest live code per user is ever checked; the index makes that
-- lookup a single ordered probe rather than a scan of a user's history.
CREATE INDEX email_codes_user_live_idx
    ON email_codes (user_id, purpose, created_at DESC)
    WHERE consumed_at IS NULL;

CREATE TABLE oauth_identities (
    provider     TEXT NOT NULL CHECK (provider IN ('google')),
    -- The provider's stable subject id. NOT the email: an email can change
    -- hands, `sub` cannot, and matching on email is how account takeover via
    -- a recycled address happens.
    subject      TEXT NOT NULL,
    user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, subject)
);

CREATE INDEX oauth_identities_user_id_idx ON oauth_identities (user_id);
