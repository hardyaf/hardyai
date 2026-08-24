from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db.migrations import configure_sqlite_connection


def resolve_database_path(database_path: str) -> Path:
    resolved = Path(database_path).expanduser()
    if not resolved.is_absolute():
        resolved = (Path.cwd() / resolved).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def open_sqlite_connection(
    database_path: str,
    *,
    timeout_seconds: float = 30.0,
) -> tuple[Path, sqlite3.Connection]:
    """Open every application SQLite connection with the same safety profile."""

    resolved = resolve_database_path(database_path)
    conn = sqlite3.connect(
        str(resolved),
        check_same_thread=False,
        timeout=max(0.1, float(timeout_seconds)),
    )
    conn.row_factory = sqlite3.Row
    configure_sqlite_connection(conn)
    return resolved, conn
