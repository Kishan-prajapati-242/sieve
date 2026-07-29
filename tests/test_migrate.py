"""Migration runner tests: ordered, idempotent, transactional.

Run against a scratch database (derived from DATABASE_URL) that is dropped
and recreated per session, so the dev database's migration history stays
untouched and the idempotency assertions start from a known-clean state.
"""

import os
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from api.db.migrate import MIGRATIONS_DIR, migrate


@pytest.fixture(scope="session")
def scratch_db_url() -> str:
    params = conninfo_to_dict(os.environ["DATABASE_URL"])
    scratch = f"{params['dbname']}_migrate_test"
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(scratch))
        )
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(scratch)))
    return make_conninfo(**{**params, "dbname": scratch})


def test_applies_in_order_then_noop(scratch_db_url: str) -> None:
    first = migrate(scratch_db_url)
    assert first == sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
    assert first, "there should be at least one migration"

    # Second run must be a no-op: same files, nothing new recorded.
    assert migrate(scratch_db_url) == []
    with psycopg.connect(scratch_db_url) as conn:
        rows = conn.execute("SELECT filename FROM schema_migrations ORDER BY filename").fetchall()
    assert [r[0] for r in rows] == first


def test_failed_migration_rolls_back(scratch_db_url: str, tmp_path: Path) -> None:
    (tmp_path / "0001_good.sql").write_text("CREATE TABLE rollback_probe (id INT);")
    (tmp_path / "0002_bad.sql").write_text(
        "CREATE TABLE half_applied (id INT); SELECT no_such_function();"
    )

    with pytest.raises(psycopg.errors.UndefinedFunction):
        migrate(scratch_db_url, migrations_dir=tmp_path)

    with psycopg.connect(scratch_db_url) as conn:
        recorded = {r[0] for r in conn.execute("SELECT filename FROM schema_migrations").fetchall()}
        # The good file applied and was recorded; the bad file's partial work
        # (half_applied) rolled back with its recording, so a fixed rerun
        # starts clean.
        assert "0001_good.sql" in recorded
        assert "0002_bad.sql" not in recorded
        exists = conn.execute("SELECT to_regclass('half_applied')").fetchone()
        assert exists is not None and exists[0] is None
