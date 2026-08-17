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


def signup_verified(c: TestClient, dsn: str, email: str, password: str = PASSWORD) -> None:
    """Sign up AND complete verification.

    Most tests here are about what a signed-in user can do, and since
    verification became a real gate, signing up alone no longer produces one.
    The gate itself is tested in TestVerificationActuallyGates.
    """
    from api.auth import codes

    signup(c, email, password).raise_for_status()
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE lower(email) = lower(%s)", (email,)
        ).fetchone()
        assert row is not None
        code = codes.issue(conn, int(row[0]))
    c.post("/api/auth/verify", json={"code": code}).raise_for_status()


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
    def test_session_cookie_is_httponly_and_not_guessable(
        self, client: TestClient, scratch_db: str
    ) -> None:
        signup_verified(client, scratch_db, "ada@example.com")
        resp = client.post(
            "/api/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
        )
        assert resp.status_code == 200
        header = resp.headers["set-cookie"]
        assert "HttpOnly" in header  # an XSS cannot read it
        assert "samesite=lax" in header.lower()  # a cross-site POST cannot ride it
        token = client.cookies[SESSION_COOKIE]
        assert len(token) >= 40  # 256 bits of urandom, not a serial

    def test_logout_actually_revokes_server_side(
        self, client: TestClient, scratch_db: str
    ) -> None:
        signup_verified(client, scratch_db, "ada@example.com")
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

    def test_one_user_cannot_see_or_touch_anothers_collection(
        self, client: TestClient, scratch_db: str
    ) -> None:
        signup_verified(client, scratch_db, "ada@example.com")
        made = client.post("/api/collections", json={"name": "Ada's review"})
        assert made.status_code == 201
        cid = made.json()["id"]
        client.post("/api/auth/logout")

        signup_verified(client, scratch_db, "grace@example.com", OTHER)
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

    def test_owner_still_sees_their_own(self, client: TestClient, scratch_db: str) -> None:
        signup_verified(client, scratch_db, "ada@example.com")
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
        signup_verified(client, scratch_db, "ada@example.com")
        assert client.get("/api/collections").json() == []


class TestEmailVerification:
    """OTP is a real round trip, so the properties that make it worth having
    are the ones under attack: guessability, expiry, and attempt limits."""

    def _code(self, dsn: str) -> str:
        # The plaintext is never stored, so tests read it the same way the
        # console transport emits it — by issuing a known one.
        with psycopg.connect(dsn, autocommit=True) as conn:
            from api.auth import codes

            uid = conn.execute("SELECT id FROM users ORDER BY id DESC LIMIT 1").fetchone()
            assert uid is not None
            return codes.issue(conn, int(uid[0]))

    def test_signup_starts_unverified(self, client: TestClient) -> None:
        resp = signup(client, "ada@example.com")
        assert resp.json()["email_verified"] is False

    def _verified_client(self, client: TestClient, scratch_db: str) -> None:
        """Complete verification, so tests about POST-verification behaviour
        can reach the routes the gate now protects."""
        code = self._code(scratch_db)
        client.post("/api/auth/verify", json={"code": code})

    def test_the_code_is_not_stored_in_plaintext(
        self, client: TestClient, scratch_db: str
    ) -> None:
        signup(client, "ada@example.com")
        code = self._code(scratch_db)
        with psycopg.connect(scratch_db) as conn:
            stored = conn.execute(
                "SELECT code_hash FROM email_codes WHERE consumed_at IS NULL"
            ).fetchone()
        assert stored is not None
        assert code not in stored[0]
        assert len(stored[0]) == 64  # sha256 hex

    def test_correct_code_verifies(self, client: TestClient, scratch_db: str) -> None:
        signup(client, "ada@example.com")
        code = self._code(scratch_db)
        resp = client.post("/api/auth/verify", json={"code": code})
        assert resp.status_code == 200
        assert resp.json()["email_verified"] is True
        assert client.get("/api/auth/me").json()["email_verified"] is True

    def test_wrong_code_is_refused_and_burns_an_attempt(
        self, client: TestClient, scratch_db: str
    ) -> None:
        signup(client, "ada@example.com")
        self._code(scratch_db)
        assert client.post("/api/auth/verify", json={"code": "000000"}).status_code == 400
        with psycopg.connect(scratch_db) as conn:
            row = conn.execute(
                "SELECT attempts FROM email_codes WHERE consumed_at IS NULL"
            ).fetchone()
        assert row is not None and row[0] == 1

    def test_attempts_are_capped_so_a_six_digit_code_cannot_be_enumerated(
        self, client: TestClient, scratch_db: str
    ) -> None:
        signup(client, "ada@example.com")
        code = self._code(scratch_db)
        for _ in range(6):
            client.post("/api/auth/verify", json={"code": "000000"})
        # Even the RIGHT code fails now: the exhausted code is burned rather
        # than left guessable, so waiting does not restore it.
        assert client.post("/api/auth/verify", json={"code": code}).status_code == 400
        # Still gated: no session was ever minted, so nothing is reachable.
        assert client.get("/api/auth/me").status_code == 401

    def test_expired_code_is_refused(self, client: TestClient, scratch_db: str) -> None:
        signup(client, "ada@example.com")
        code = self._code(scratch_db)
        with psycopg.connect(scratch_db, autocommit=True) as conn:
            conn.execute("UPDATE email_codes SET expires_at = now() - interval '1 second'")
        assert client.post("/api/auth/verify", json={"code": code}).status_code == 400

    def test_resend_supersedes_the_previous_code(
        self, client: TestClient, scratch_db: str
    ) -> None:
        signup(client, "ada@example.com")
        first = self._code(scratch_db)
        client.post("/api/auth/verify/resend")
        # The old code must die, or "resend" would double an attacker's
        # guessing surface instead of resetting it.
        assert client.post("/api/auth/verify", json={"code": first}).status_code == 400


class TestGoogleLinking:
    """The federated-takeover boundary, tested without touching Google."""

    def test_google_never_links_onto_a_password_account(
        self, client: TestClient, scratch_db: str
    ) -> None:
        from api.auth.service import AuthError, link_or_create_google_user

        signup(client, "ada@example.com")  # a real password account
        with psycopg.connect(scratch_db, autocommit=True) as conn, pytest.raises(AuthError):
            link_or_create_google_user(conn, "google-sub-1", "ada@example.com")

    def test_google_identity_is_keyed_on_subject_not_email(self, scratch_db: str) -> None:
        from api.auth.service import link_or_create_google_user

        migrate(scratch_db)
        with psycopg.connect(scratch_db, autocommit=True) as conn:
            uid = link_or_create_google_user(conn, "sub-abc", "grace@example.com")
            # Same person, address changed at Google: same account.
            assert link_or_create_google_user(conn, "sub-abc", "grace2@example.com") == uid
            # A different Google subject presenting the OLD address is the
            # recycled-address case. It must NOT resolve to the first account:
            # matching on email there is exactly how takeover happens. It also
            # cannot silently create a second account, because the address is
            # unique — so the only safe answer is an explicit refusal.
            from api.auth.service import AuthError

            with pytest.raises(AuthError, match="different Google account"):
                link_or_create_google_user(conn, "sub-xyz", "grace@example.com")

    def test_google_accounts_start_verified(self, scratch_db: str) -> None:
        from api.auth.service import link_or_create_google_user, user_for_session_by_id

        migrate(scratch_db)
        with psycopg.connect(scratch_db, autocommit=True) as conn:
            uid = link_or_create_google_user(conn, "sub-1", "who@example.com")
            assert user_for_session_by_id(conn, uid)["email_verified"] is True


class TestVerificationActuallyGates:
    """The gate must GATE. Signing up with an address you do not own must not
    produce a working account — which is exactly what happened when signup
    issued a real session and the UI merely suggested verifying."""

    def test_signup_does_not_grant_a_usable_session(self, client: TestClient) -> None:
        resp = signup(client, "impostor@example.com")
        assert resp.status_code == 201
        # A pending cookie exists, a session cookie does not.
        assert "sieve_pending" in client.cookies
        assert SESSION_COOKIE not in client.cookies
        # And it opens nothing.
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/collections").status_code == 401
        assert client.post("/api/collections", json={"name": "x"}).status_code == 401

    def test_access_begins_only_after_the_code_is_entered(
        self, client: TestClient, scratch_db: str
    ) -> None:
        signup(client, "ada@example.com")
        assert client.get("/api/collections").status_code == 401

        with psycopg.connect(scratch_db, autocommit=True) as conn:
            from api.auth import codes

            uid = conn.execute("SELECT id FROM users ORDER BY id DESC LIMIT 1").fetchone()
            assert uid is not None
            code = codes.issue(conn, int(uid[0]))

        resp = client.post("/api/auth/verify", json={"code": code})
        assert resp.status_code == 200
        assert resp.json()["email_verified"] is True
        # Proving control of the address is what mints access.
        assert SESSION_COOKIE in client.cookies
        assert client.get("/api/collections").status_code == 200

    def test_pending_token_is_consumed_so_it_cannot_be_replayed(
        self, client: TestClient, scratch_db: str
    ) -> None:
        signup(client, "ada@example.com")
        pending = client.cookies["sieve_pending"]
        with psycopg.connect(scratch_db, autocommit=True) as conn:
            from api.auth import codes

            uid = conn.execute("SELECT id FROM users ORDER BY id DESC LIMIT 1").fetchone()
            assert uid is not None
            code = codes.issue(conn, int(uid[0]))
        client.post("/api/auth/verify", json={"code": code})

        client.cookies.clear()
        client.cookies.set("sieve_pending", pending)
        # The pending row is deleted at verification, so a captured cookie is
        # dead rather than a second way in.
        assert client.get("/api/auth/me").status_code == 401

    def test_a_wrong_code_leaves_the_account_locked_out(
        self, client: TestClient, scratch_db: str
    ) -> None:
        signup(client, "ada@example.com")
        assert client.post("/api/auth/verify", json={"code": "000000"}).status_code == 400
        assert client.get("/api/collections").status_code == 401


class TestPasswordSignupGate:
    """The flag has to actually close the door, and reopening it has to work —
    a disabled path nobody can re-enable is a deleted path with extra steps."""

    def test_signup_is_refused_when_the_flag_is_off(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PASSWORD_SIGNUP", raising=False)
        resp = signup(client, "nobody@example.com")
        # 403 from the SERVER, not merely hidden in the UI: a curl must not be
        # able to create an account that can never verify.
        assert resp.status_code == 403
        assert "Google" in resp.json()["detail"]
        assert client.get("/api/auth/config").json()["password_signup"] is False

    def test_existing_password_accounts_can_still_sign_in(
        self, client: TestClient, scratch_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Created while signup was open...
        signup_verified(client, scratch_db, "early@example.com")
        client.post("/api/auth/logout")
        # ...and closing signup must not lock them out.
        monkeypatch.delenv("PASSWORD_SIGNUP", raising=False)
        resp = client.post(
            "/api/auth/login", json={"email": "early@example.com", "password": PASSWORD}
        )
        assert resp.status_code == 200
        assert client.get("/api/auth/me").status_code == 200

    def test_flipping_the_flag_restores_signup(self, client: TestClient) -> None:
        assert client.get("/api/auth/config").json()["password_signup"] is True
        assert signup(client, "later@example.com").status_code == 201
