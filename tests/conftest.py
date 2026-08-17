"""Test defaults and shared fixtures.

Tests run against the real compose Postgres, no mocks — the point of this
project is the database behavior. DATABASE_URL from the environment wins
(CI sets it); the default matches .env.example for local runs.
"""

import os
import re
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

os.environ.setdefault("DATABASE_URL", "postgresql://sieve:sieve@localhost:5432/sieve")


@pytest.fixture
def scratch_db(request: pytest.FixtureRequest) -> Iterator[str]:
    """A private, per-test database, dropped afterwards.

    Per-test rather than shared: a session-scoped scratch database made the
    suite order-dependent (one test's leftovers broke another's exact-contents
    assertions). The non-local guard exists because this fixture issues DROP
    DATABASE and a shell-exported DATABASE_URL may legitimately point at Neon.
    """
    params = conninfo_to_dict(os.environ["DATABASE_URL"])
    host = str(params.get("host") or "localhost")
    # Loopback is always fine. Anything else must be named explicitly via
    # SIEVE_TESTS_ALLOW_DB_HOST — the compose test service sets it to
    # "postgres" (its own network's database); a shell that leaks a Neon URL
    # never has the matching opt-in, so those still skip.
    allowed = ("localhost", "127.0.0.1", "::1", os.environ.get("SIEVE_TESTS_ALLOW_DB_HOST"))
    if host not in allowed:
        pytest.skip(f"scratch_db issues DROP DATABASE; refusing non-local host {host!r}")
    name = re.sub(r"\W", "_", f"{params['dbname']}_t_{request.node.name}").lower()[:63]
    admin_url = os.environ["DATABASE_URL"]
    drop = sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(drop)
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    yield make_conninfo("", **{**params, "dbname": name})
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(drop)


@pytest.fixture(autouse=True)
def _enable_password_signup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Password signup is disabled in deployment (no verified mail domain) but
    the flow still has to be tested — the flag flips back the moment the domain
    lands, and untested code is not code that comes back safely."""
    monkeypatch.setenv("PASSWORD_SIGNUP", "1")
