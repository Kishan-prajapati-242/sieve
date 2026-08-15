"""Accounts and sessions.

Deliberately small and boring. Everything here is a place where a clever
implementation is a vulnerability, so each choice is the conventional one:

  passwords   argon2id via argon2-cffi, library defaults. Never compared with
              ==; `verify` is constant-time and raises rather than returning
              False, which is why the caller catches instead of branching.
  session id  256 bits from `secrets.token_urlsafe`. A BIGSERIAL session id
              would be guessable, and guessing one is account takeover.
  lookup      by token, in one indexed query, with expiry checked in SQL so
              an expired session cannot be resurrected by a clock difference
              between the app and the database.
  enumeration login returns the same error whether the email is unknown or
              the password is wrong, and hashes a dummy password when the
              user does not exist so the two paths take similar time.

Raw SQL, like the rest of the project. Sessions are a table rather than a
JWT because logout has to actually revoke, and a revocable JWT needs server
state anyway — at which point it is a session table with extra steps.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

SESSION_TTL = timedelta(days=30)
SESSION_COOKIE = "sieve_session"

# Hashed once at import and compared against when the email is unknown, so a
# failed login does roughly the same work whether or not the account exists.
_DUMMY_HASH = _hasher.hash("not-a-real-password-used-for-timing-only")

MIN_PASSWORD_LEN = 10


class AuthError(Exception):
    """Anything the caller should turn into a 4xx without further detail."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        _hasher.verify(stored_hash, password)
        return True
    except (VerifyMismatchError, VerificationError):
        return False


def validate_password(password: str) -> None:
    """Length only.

    Composition rules (a digit, a symbol, a capital) push users toward
    Password1! and are no longer recommended by NIST. Length is the property
    that actually costs an attacker work.
    """
    if len(password) < MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if len(password) > 1024:
        # Argon2 is memory-hard; unbounded input is a free denial of service.
        raise AuthError("Password is too long")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def create_user(conn: psycopg.Connection, email: str, password: str) -> int:
    validate_password(password)
    email = normalize_email(email)
    if "@" not in email or len(email) < 3:
        raise AuthError("Enter a valid email address")
    try:
        row = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
            (email, hash_password(password)),
        ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise AuthError("An account with that email already exists") from exc
    assert row is not None
    return int(row[0])


def authenticate(conn: psycopg.Connection, email: str, password: str) -> int:
    row = conn.execute(
        "SELECT id, password_hash FROM users WHERE lower(email) = lower(%s)",
        (normalize_email(email),),
    ).fetchone()
    if row is None:
        # Same work, same message: do not leak which emails have accounts.
        verify_password(_DUMMY_HASH, password)
        raise AuthError("Incorrect email or password")
    if not verify_password(row[1], password):
        raise AuthError("Incorrect email or password")
    return int(row[0])


def create_session(conn: psycopg.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)  # 256 bits
    conn.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, %s)",
        (token, user_id, datetime.now(UTC) + SESSION_TTL),
    )
    return token


def user_for_session(conn: psycopg.Connection, token: str | None) -> dict[str, Any] | None:
    """The signed-in user, or None. Expiry is enforced in SQL."""
    if not token:
        return None
    row = conn.execute(
        """
        SELECT u.id, u.email, u.email_verified_at IS NOT NULL
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token = %s AND s.expires_at > now()
        """,
        (token,),
    ).fetchone()
    if row is None:
        return None
    return {"id": int(row[0]), "email": row[1], "email_verified": bool(row[2])}


def destroy_session(conn: psycopg.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token = %s", (token,))


def purge_expired(conn: psycopg.Connection) -> int:
    """Housekeeping. Expired rows are already unusable — this reclaims space."""
    cur = conn.execute("DELETE FROM sessions WHERE expires_at <= now()")
    return cur.rowcount


def user_for_session_by_id(conn: psycopg.Connection, user_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, email, email_verified_at IS NOT NULL FROM users WHERE id = %s", (user_id,)
    ).fetchone()
    assert row is not None
    return {"id": int(row[0]), "email": row[1], "email_verified": bool(row[2])}


def link_or_create_google_user(conn: psycopg.Connection, subject: str, email: str) -> int:
    """Resolve a Google identity to a local user id.

    Three cases, in order, and the order is the security decision:

      1. this `sub` is already linked  -> that user. The only stable key.
      2. an account exists with this email AND has no password credential
         -> link it. That account can only have come from a previous Google
         sign-in whose link was lost, so no password is being bypassed.
      3. otherwise -> create a fresh account.

    Case 2 deliberately refuses to link when the local account HAS a password.
    Auto-linking there would let anyone who can obtain a Google token for an
    address take over a password account registered to it, which is the
    classic federated-takeover bug. Those users must sign in with their
    password and link deliberately.

    Google has already verified the address, so the account is created
    verified — sending our own code to an address Google just confirmed would
    be theatre.
    """
    row = conn.execute(
        "SELECT user_id FROM oauth_identities WHERE provider = 'google' AND subject = %s",
        (subject,),
    ).fetchone()
    if row is not None:
        return int(row[0])

    existing = conn.execute(
        """
        SELECT u.id, u.password_hash, EXISTS (
            SELECT 1 FROM oauth_identities o WHERE o.user_id = u.id
        )
        FROM users u WHERE lower(u.email) = lower(%s)
        """,
        (email,),
    ).fetchone()
    # The `NOT already-linked` clause is load-bearing and was missing: without
    # it, a passwordless account already tied to Google subject A was matched
    # by email and handed to subject B — precisely the recycled-address
    # takeover this function claims to prevent. Caught by a test 2026-08-15.
    if existing is not None and existing[1] is None and not existing[2]:
        user_id = int(existing[0])
    elif existing is not None and existing[1] is not None:
        # A password account owns this address. Do not link silently.
        raise AuthError("An account with that email already exists. Sign in with your password.")
    elif existing is not None:
        # A passwordless account holds this address but is linked to a
        # DIFFERENT Google subject — the recycled-address case. Linking would
        # hand one person another's collections; creating would collide on the
        # unique email index. Refuse and say so, rather than guess.
        raise AuthError(
            "That email is already linked to a different Google account. "
            "Sign in with the original account, or contact support."
        )
    else:
        # Either no account for this address, or one that belongs to a
        # DIFFERENT Google subject. Both mean: this is a new person.
        created = conn.execute(
            "INSERT INTO users (email, password_hash, email_verified_at)"
            " VALUES (%s, NULL, now()) RETURNING id",
            (normalize_email(email),),
        ).fetchone()
        assert created is not None
        user_id = int(created[0])

    conn.execute(
        "INSERT INTO oauth_identities (provider, subject, user_id) VALUES ('google', %s, %s)"
        " ON CONFLICT DO NOTHING",
        (subject, user_id),
    )
    return user_id
