"""Migration runner tests: ordered, idempotent, all-or-nothing.

The runner applies everything in one transaction, so the failure test asserts
total rollback — schema_migrations included. The validation tests need no
database at all: filenames are checked before any connection is opened, which
the never-connects conninfo would otherwise expose.
"""

from pathlib import Path

import psycopg
import pytest

from api.db.migrate import MIGRATIONS_DIR, migrate


def test_real_migrations_apply_then_noop(scratch_db: str) -> None:
    first = migrate(scratch_db)
    assert first == sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
    assert first, "there should be at least one migration"

    # Second run must be a no-op: nothing applied, nothing new recorded.
    assert migrate(scratch_db) == []
    with psycopg.connect(scratch_db) as conn:
        rows = conn.execute("SELECT filename FROM schema_migrations ORDER BY filename").fetchall()
    assert [r[0] for r in rows] == first


def test_applies_in_filename_order_not_directory_order(scratch_db: str, tmp_path: Path) -> None:
    # Written to disk out of order on purpose; readdir order is filesystem-
    # dependent. The FK makes a wrong order fail hard, not just record oddly.
    (tmp_path / "0010_child.sql").write_text("CREATE TABLE child (a INT REFERENCES parent(a));")
    (tmp_path / "0009_parent.sql").write_text("CREATE TABLE parent (a INT PRIMARY KEY);")
    assert migrate(scratch_db, migrations_dir=tmp_path) == ["0009_parent.sql", "0010_child.sql"]


def test_failure_rolls_back_the_entire_run(scratch_db: str, tmp_path: Path) -> None:
    (tmp_path / "0001_good.sql").write_text("CREATE TABLE probe_good (id INT);")
    (tmp_path / "0002_bad.sql").write_text(
        "CREATE TABLE probe_partial (id INT); SELECT no_such_function();"
    )

    with pytest.raises(psycopg.errors.UndefinedFunction):
        migrate(scratch_db, migrations_dir=tmp_path)

    # All-or-nothing: the good file's table and every recording are gone,
    # schema_migrations itself included (it bootstraps in the same txn).
    with psycopg.connect(scratch_db) as conn:
        for table in ("schema_migrations", "probe_good", "probe_partial"):
            row = conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()
            assert row is not None and row[0] is None, f"{table} should not exist"

    # A fixed rerun starts from a clean slate and applies everything.
    (tmp_path / "0002_bad.sql").write_text("CREATE TABLE probe_fixed (id INT);")
    assert migrate(scratch_db, migrations_dir=tmp_path) == ["0001_good.sql", "0002_bad.sql"]


def test_missing_or_empty_migrations_dir_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        migrate("dbname=never_connects", migrations_dir=tmp_path / "nowhere")
    with pytest.raises(FileNotFoundError):
        migrate("dbname=never_connects", migrations_dir=tmp_path)


def test_unpadded_filename_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "2_unpadded.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError, match="NNNN_name.sql"):
        migrate("dbname=never_connects", migrations_dir=tmp_path)
