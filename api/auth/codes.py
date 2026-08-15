"""One-time email codes.

Verification is a real round trip, not a flag set at signup: the account is
created unverified, a code is sent, and only entering it marks the address
as belonging to the person who typed it.

Three properties matter and each is enforced rather than assumed:

  hashed      the stored value is sha256 of the code, so a database dump does
              not contain live codes. argon2 is unnecessary here — the code
              is short-lived and rate-limited, and hashing it 100k times per
              check would make the endpoint the cheapest DoS in the app.
  expiring    ten minutes. Checked in SQL alongside the lookup.
  limited     six attempts, counted in the row. A six-digit code is a million
              possibilities, which is only meaningful if guessing is capped —
              without a limit an attacker enumerates it in minutes.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import psycopg

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 6
CODE_DIGITS = 6


class CodeError(Exception):
    """Wrong, expired, exhausted, or missing. Deliberately indistinguishable
    to the caller: saying which one tells an attacker whether to keep going."""


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def generate_code() -> str:
    """A uniformly random 6-digit code.

    `secrets.randbelow` rather than `random.randint`: the latter is a Mersenne
    Twister seeded from the clock and its output is predictable from a handful
    of observations.
    """
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


def issue(conn: psycopg.Connection, user_id: int, purpose: str = "verify_email") -> str:
    """Create a code, invalidating any previous unconsumed one for this user.

    Superseding rather than accumulating: if two codes were live at once,
    "resend" would double an attacker's guessing surface instead of resetting
    it.
    """
    conn.execute(
        "UPDATE email_codes SET consumed_at = now()"
        " WHERE user_id = %s AND purpose = %s AND consumed_at IS NULL",
        (user_id, purpose),
    )
    code = generate_code()
    conn.execute(
        "INSERT INTO email_codes (user_id, code_hash, purpose, expires_at)"
        " VALUES (%s, %s, %s, %s)",
        (user_id, _hash(code), purpose, datetime.now(UTC) + CODE_TTL),
    )
    return code


def verify(
    conn: psycopg.Connection, user_id: int, code: str, purpose: str = "verify_email"
) -> None:
    """Consume the newest live code, or raise CodeError."""
    row = conn.execute(
        """
        SELECT id, code_hash, attempts
        FROM email_codes
        WHERE user_id = %s AND purpose = %s AND consumed_at IS NULL AND expires_at > now()
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (user_id, purpose),
    ).fetchone()
    if row is None:
        raise CodeError("That code is not valid. Request a new one.")

    code_id, stored, attempts = row[0], row[1], row[2]
    if attempts >= MAX_ATTEMPTS:
        # Burn it: an exhausted code must not become valid again by waiting.
        conn.execute("UPDATE email_codes SET consumed_at = now() WHERE id = %s", (code_id,))
        conn.commit()
        raise CodeError("Too many attempts. Request a new code.")

    if not secrets.compare_digest(stored, _hash(code)):
        conn.execute("UPDATE email_codes SET attempts = attempts + 1 WHERE id = %s", (code_id,))
        # COMMIT BEFORE RAISING. Caught by a test 2026-08-15: the caller wraps
        # this in `with get_pool().connection()`, which rolls back on an
        # exception — so the CodeError that should record a failed guess was
        # erasing the record of it, and the attempt limit counted to one
        # forever. An attacker could then enumerate a six-digit code freely.
        conn.commit()
        raise CodeError("That code is not valid. Request a new one.")

    conn.execute("UPDATE email_codes SET consumed_at = now() WHERE id = %s", (code_id,))
    conn.execute("UPDATE users SET email_verified_at = now() WHERE id = %s", (user_id,))
