"""Forward-only SQL migration runner.

Migrations are numbered .sql files in api/db/migrations, applied in filename
order. Applied filenames are recorded in schema_migrations; an applied file is
never edited — write a new migration instead. Each file runs in its own
transaction: it either fully applies and is recorded, or rolls back and is not.

A Postgres advisory lock serializes concurrent runners (the api and worker
containers may both migrate at deploy time); the second runner blocks on the
lock, then finds nothing left to apply. The lock is session-level, so it
survives the per-file commits and is released when the connection closes.

Alternative rejected: Alembic. Its main value is autogenerating diffs from ORM
models, and the search paths here are deliberately ORM-free — we would carry
the dependency while being unable to use its main feature. Fifty lines we can
read beat a framework we can't.

Deliberate caveat: running each file in a transaction means no CREATE INDEX
CONCURRENTLY. Fine while migrations run against an idle database; revisit if
we ever need to migrate a live one.

Usage: python -m api.db.migrate  (reads DATABASE_URL)
"""

import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Arbitrary but fixed constant identifying "sieve migration runner" to
# pg_advisory_lock, so unrelated tools can't collide with it by accident.
LOCK_KEY = 742_001


def _applied(conn: psycopg.Connection) -> set[str]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename   TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    return {r[0] for r in rows}


def migrate(conninfo: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations in filename order; return what was applied."""
    files = sorted(migrations_dir.glob("*.sql"))
    applied: list[str] = []
    with psycopg.connect(conninfo) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        done = _applied(conn)
        conn.commit()
        for path in files:
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            conn.commit()
            applied.append(path.name)
    return applied


def main() -> None:
    conninfo = os.environ.get("DATABASE_URL")
    if not conninfo:
        sys.exit("DATABASE_URL is not set")
    applied = migrate(conninfo)
    for name in applied:
        print(f"applied {name}")
    if not applied:
        print("nothing to apply")


if __name__ == "__main__":
    main()
