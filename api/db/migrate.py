"""Forward-only SQL migration runner.

Migrations are numbered .sql files in api/db/migrations, applied in filename
order (NNNN_name.sql, zero-padded — enforced, because lexicographic order IS
the application order). Applied filenames are recorded in schema_migrations;
an applied file is never edited — write a new migration instead.

The entire run — lock, bootstrap, every pending file, their records — is ONE
transaction. A deploy either fully migrates or leaves the database exactly as
it was; there is no partially-migrated state to reason about.

The lock is pg_advisory_xact_lock rather than session-level pg_advisory_lock,
deliberately: the demo database (Neon/Supabase) hands out transaction-pooled
connection strings by default, and under transaction pooling a session-level
lock lands on an arbitrary server backend and is abandoned there at the first
commit — it serializes nothing and blocks the next deploy until the pooler
recycles that backend. A transaction-scoped lock inside a single transaction
stays pinned to one backend and vanishes on commit or abort, pooled or not.

Alternative rejected: Alembic. Its main value is autogenerating diffs from ORM
models, and the search paths here are deliberately ORM-free — we would carry
the dependency while being unable to use its main feature. Sixty readable
lines beat a framework we can't explain.

Deliberate caveat: one transaction means no CREATE INDEX CONCURRENTLY. Fine
while migrations run against an idle database; revisit if we ever need to
migrate a live one.

Usage: python -m api.db.migrate  (reads DATABASE_URL)
"""

import os
import re
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Arbitrary but fixed constant identifying "sieve migration runner" to
# pg_advisory_xact_lock, so unrelated tools can't collide with it by accident.
LOCK_KEY = 742_001

_FILENAME = re.compile(r"^\d{4}_.+\.sql$")


def migrate(conninfo: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations in filename order; return what was applied."""
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        # A missing or empty directory is a broken deploy (say, .sql files
        # left out of the package), not an up-to-date database. Exiting 0
        # here would green-light a deploy that migrated nothing.
        raise FileNotFoundError(f"no migrations found in {migrations_dir}")
    bad = [p.name for p in files if not _FILENAME.match(p.name)]
    if bad:
        raise ValueError(f"migration filenames must match NNNN_name.sql: {bad}")

    applied: list[str] = []
    with psycopg.connect(conninfo) as conn:
        # Fail loudly if another runner is wedged (an OOM-killed container
        # whose server backend lingers) instead of hanging silently for hours.
        conn.execute("SET lock_timeout = '30s'")
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename   TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
        done = {r[0] for r in rows}
        for path in files:
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            applied.append(path.name)
        conn.commit()
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
