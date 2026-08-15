"""Auth, against a real database through the real app.

These pin SECURITY properties, not just the happy path. Every assertion here
corresponds to a way accounts get broken in practice: readable password
storage, guessable sessions, sessions that outlive logout, account
enumeration, and — the one that matters most for this app — one user reading
another user's collections.
"""

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from api.auth.service import SESSION_COOKIE, hash_password, verify_password
from api.db.migrate import migrate
from api.db.pool import close_pool
from api.main import app

PASSWORD = "correct-horse-battery"
OTHER = "a-different-long-password"


@pytest.fixture
def client(scratch_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    migrate(scratch_db)
    close_pool()
    monkeypatch.setenv("DATABASE_URL", scratch_db)
    with TestClient(app) as c:
        yield c
    close_pool()


def signup(c: TestClient, email: str, password: str = PASSWORD):  # type: ignore[no-untyped-def]
    return c.post("/api/auth/signup", json={"email": email, "password": password})


class TestPasswordStorage:
    def test_password_is_never_stored_in_a_recoverable_form(
        self, client: TestClient, scratch_db: str
    ) -> None:
        signup(client, "ada@example.com")
        with psycopg.connect(scratch_db) as conn:
            row = conn.execute("SELECT password_hash FROM users").fetchone()
        assert row is not None
        stored = row[0]
        assert PASSWORD not in stored
        assert stored.startswith("$argon2id$")  # memory-hard, salted, not a bare digest

    def test_two_identical_passwords_hash_differently(self) -> None:
        # If they matched, the hash would be unsalted and a rainbow table
        # would work against the whole users table at once.
        assert hash_password(PASSWORD) != hash_password(PASSWORD)
        assert verify_password(hash_password(PASSWORD), PASSWORD)
        assert not verify_password(hash_password(PASSWORD), PASSWORD + "x")


class TestSessions:
    def test_session_cookie_is_httponly_and_not_guessable(self, client: TestClient) -> None:
        resp = signup(client, "ada@example.com")
        assert resp.status_code == 201
        header = resp.headers["set-cookie"]
        assert "HttpOnly" in header  # an XSS cannot read it
        assert "samesite=lax" in header.lower()  # a cross-site POST cannot ride it
        token = client.cookies[SESSION_COOKIE]
        assert len(token) >= 40  # 256 bits of urandom, not a serial

    def test_logout_actually_revokes_server_side(
        self, client: TestClient, scratch_db: str
    ) -> None:
        signup(client, "ada@example.com")
        token = client.cookies[SESSION_COOKIE]
        assert client.get("/api/auth/me").status_code == 200

        client.post("/api/auth/logout")
        # Not merely cleared in the browser: the row is gone, so replaying a
        # captured cookie fails too. This is the reason sessions are a table.
        with psycopg.connect(scratch_db) as conn:
            assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0  # type: ignore[index]
        client.cookies.set(SESSION_COOKIE, token)
        assert client.get("/api/auth/me").status_code == 401

    def test_expired_session_is_rejected(self, client: TestClient, scratch_db: str) -> None:
        signup(client, "ada@example.com")
        with psycopg.connect(scratch_db, autocommit=True) as conn:
            conn.execute("UPDATE sessions SET expires_at = now() - interval '1 second'")
        assert client.get("/api/auth/me").status_code == 401

    def test_a_forged_token_is_rejected(self, client: TestClient) -> None:
        client.cookies.set(SESSION_COOKIE, "clearly-not-a-real-token")
        assert client.get("/api/auth/me").status_code == 401


class TestAccountRules:
    def test_duplicate_email_is_refused_case_insensitively(self, client: TestClient) -> None:
        assert signup(client, "ada@example.com").status_code == 201
        again = signup(client, "ADA@Example.com ")
        assert again.status_code == 400
        assert "already exists" in again.json()["detail"]

    def test_short_passwords_are_refused(self, client: TestClient) -> None:
        resp = signup(client, "ada@example.com", "short")
        assert resp.status_code == 400
        assert "at least" in resp.json()["detail"]

    def test_login_does_not_reveal_whether_an_account_exists(self, client: TestClient) -> None:
        signup(client, "ada@example.com")
        client.post("/api/auth/logout")
        wrong_pw = client.post(
            "/api/auth/login", json={"email": "ada@example.com", "password": "wrong-password-x"}
        )
        no_user = client.post(
            "/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-x"}
        )
        # Identical status AND identical body: a difference in either is an
        # account-enumeration oracle.
        assert wrong_pw.status_code == no_user.status_code == 401
        assert wrong_pw.json() == no_user.json()

    def test_login_is_case_insensitive_on_email(self, client: TestClient) -> None:
        signup(client, "ada@example.com")
        client.post("/api/auth/logout")
        resp = client.post(
            "/api/auth/login", json={"email": "Ada@Example.COM", "password": PASSWORD}
        )
        assert resp.status_code == 200


class TestCollectionIsolation:
    """The property the whole feature exists for."""

    def test_one_user_cannot_see_or_touch_anothers_collection(self, client: TestClient) -> None:
        signup(client, "ada@example.com")
        made = client.post("/api/collections", json={"name": "Ada's review"})
        assert made.status_code == 201
        cid = made.json()["id"]
        client.post("/api/auth/logout")

        signup(client, "grace@example.com", OTHER)
        # Not listed.
        assert client.get("/api/collections").json() == []
        # Not readable, and 404 rather than 403 — a 403 would confirm the id
        # exists, which is itself a leak.
        assert client.get(f"/api/collections/{cid}").status_code == 404
        # Not writable, on every state-changing route.
        assert (
            client.put(
                f"/api/collections/{cid}/screenings/1", json={"decision": "include"}
            ).status_code
            == 404
        )
        assert client.delete(f"/api/collections/{cid}/screenings/1").status_code == 404
        assert client.get(f"/api/collections/{cid}/export.bib").status_code == 404

    def test_owner_still_sees_their_own(self, client: TestClient) -> None:
        signup(client, "ada@example.com")
        cid = client.post("/api/collections", json={"name": "Mine"}).json()["id"]
        assert [c["id"] for c in client.get("/api/collections").json()] == [cid]
        assert client.get(f"/api/collections/{cid}").status_code == 200

    def test_signed_out_callers_get_401_not_an_empty_list(self, client: TestClient) -> None:
        # An empty 200 would let the UI render "no collections yet" to someone
        # who is simply signed out, which reads as data loss.
        assert client.get("/api/collections").status_code == 401
        assert client.post("/api/collections", json={"name": "x"}).status_code == 401

    def test_legacy_ownerless_collections_are_invisible(
        self, client: TestClient, scratch_db: str
    ) -> None:
        # Rows that predate accounts have user_id IS NULL. They must match no
        # session rather than being visible to everyone.
        with psycopg.connect(scratch_db, autocommit=True) as conn:
            conn.execute("INSERT INTO collections (name) VALUES ('legacy')")
        signup(client, "ada@example.com")
        assert client.get("/api/collections").json() == []
