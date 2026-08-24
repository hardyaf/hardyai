from __future__ import annotations

import sqlite3

import pytest

from scripts.manage_database import _integrity, backup, restore


def test_online_backup_and_guarded_restore(tmp_path):
    database = tmp_path / "jarvis.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE example (value TEXT NOT NULL)")
    connection.execute("INSERT INTO example VALUES ('before')")
    connection.commit()
    connection.close()

    created = backup(database, tmp_path / "backups")
    assert created.is_file()
    assert _integrity(created) == "ok"

    connection = sqlite3.connect(database)
    connection.execute("UPDATE example SET value = 'after'")
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="--replace"):
        restore(created, database, replace=False)
    preserved = restore(created, database, replace=True)
    assert preserved is not None and preserved.is_file()
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT value FROM example").fetchone()[0] == "before"
    finally:
        connection.close()

