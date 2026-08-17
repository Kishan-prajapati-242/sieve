"""Membership, roles, and invitations.

Access moved from `collections.user_id` (one owner, nobody else) to a
membership table. Every route now asks "what is this caller's role here" and
gets one of owner / screener / viewer / None, and None is indistinguishable
from "no such collection" at the API boundary — a 403 would confirm the id
exists, which is itself a leak.

Raw SQL like the rest of the project. Three places need concurrency care and
each is handled in the query rather than in Python:

  invite acceptance   two people (or one person, twice, on a flaky
                      connection) redeeming the same link must produce exactly
                      one membership. `UPDATE ... WHERE used_at IS NULL
                      RETURNING` makes the claim atomic — the loser gets no
                      row back and is told the link is spent, rather than both
                      reading "unused" and both inserting.
  screening           two members screening the same paper at the same instant
                      are two different primary keys now, so they cannot
                      collide at all. That is a property of the schema, not of
                      locking, which is why the PK change was the important
                      part of this feature.
  resolution          two resolvers settling one conflict simultaneously is a
                      genuine race with no correct winner. Last write wins,
                      but `resolved_by` and `resolved_at` record who, and the
                      individual calls underneath are untouched — so the
                      history shows what happened rather than hiding it.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import psycopg

Role = Literal["owner", "screener", "viewer"]

INVITE_TTL = timedelta(days=14)

# Who may do what. Kept as data rather than scattered `if role ==` checks, so
# the permission model can be read in one place and tested as a table.
CAN_SCREEN: frozenset[str] = frozenset({"owner", "screener"})
CAN_RESOLVE: frozenset[str] = frozenset({"owner"})
CAN_INVITE: frozenset[str] = frozenset({"owner"})
CAN_VIEW: frozenset[str] = frozenset({"owner", "screener", "viewer"})


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def role_for(conn: psycopg.Connection, collection_id: int, user_id: int) -> Role | None:
    row = conn.execute(
        "SELECT role FROM collection_members WHERE collection_id = %s AND user_id = %s",
        (collection_id, user_id),
    ).fetchone()
    return str(row[0]) if row else None  # type: ignore[return-value]


def list_members(conn: psycopg.Connection, collection_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT m.user_id, u.email, m.role, m.joined_at
        FROM collection_members m JOIN users u ON u.id = m.user_id
        WHERE m.collection_id = %s
        ORDER BY (m.role = 'owner') DESC, m.joined_at
        """,
        (collection_id,),
    ).fetchall()
    return [
        {"user_id": r[0], "email": r[1], "role": r[2], "joined_at": r[3]} for r in rows
    ]


def create_invite(
    conn: psycopg.Connection, collection_id: int, role: Role, created_by: int
) -> str:
    """Mint a single-use invite token. The caller gets the only plaintext copy."""
    token = secrets.token_urlsafe(24)
    conn.execute(
        "INSERT INTO collection_invites (token_hash, collection_id, role, created_by, expires_at)"
        " VALUES (%s, %s, %s, %s, %s)",
        (_hash(token), collection_id, role, created_by, datetime.now(UTC) + INVITE_TTL),
    )
    return token


class InviteError(Exception):
    """Expired, spent, unknown, or already a member."""


def accept_invite(conn: psycopg.Connection, token: str, user_id: int) -> int:
    """Redeem an invite, returning the collection id.

    The UPDATE claims the token and returns a row only if it was unused and
    unexpired. Two concurrent redemptions therefore produce one winner and one
    InviteError, rather than two memberships or a lost update.
    """
    claimed = conn.execute(
        """
        UPDATE collection_invites
           SET used_at = now(), used_by = %s
         WHERE token_hash = %s AND used_at IS NULL AND expires_at > now()
        RETURNING collection_id, role
        """,
        (user_id, _hash(token)),
    ).fetchone()
    if claimed is None:
        raise InviteError("That invitation link is invalid, expired, or already used.")

    collection_id, role = int(claimed[0]), claimed[1]
    # Already a member: the token is spent (correctly — it was used), and the
    # caller lands where they expected rather than seeing an error for a
    # no-op.
    conn.execute(
        "INSERT INTO collection_members (collection_id, user_id, role, invited_by)"
        " VALUES (%s, %s, %s, (SELECT created_by FROM collection_invites WHERE token_hash = %s))"
        " ON CONFLICT (collection_id, user_id) DO NOTHING",
        (collection_id, user_id, role, _hash(token)),
    )
    return collection_id


def remove_member(conn: psycopg.Connection, collection_id: int, user_id: int) -> None:
    """Remove a member, refusing to remove the last owner.

    A collection with no owner cannot be administered by anyone and cannot be
    deleted — it becomes a permanent orphan, which is exactly the state the
    ownerless legacy collections were in.
    """
    row = conn.execute(
        "SELECT count(*) FROM collection_members WHERE collection_id = %s AND role = 'owner'",
        (collection_id,),
    ).fetchone()
    owners = int(row[0]) if row else 0
    target = role_for(conn, collection_id, user_id)
    if target == "owner" and owners <= 1:
        raise InviteError("A collection must keep at least one owner.")
    conn.execute(
        "DELETE FROM collection_members WHERE collection_id = %s AND user_id = %s",
        (collection_id, user_id),
    )
    # Their screenings stay. Removing someone must not delete the judgements
    # they contributed — a review's record is not the reviewer's to withdraw,
    # and the agreement statistics already computed would silently change.
