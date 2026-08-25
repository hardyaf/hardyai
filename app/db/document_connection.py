from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from app.db.connection import open_sqlite_connection
from app.db.document_schema import initialize_document_schema


def open_document_connection(database_path: str) -> tuple[Path, sqlite3.Connection]:
    path, conn = open_sqlite_connection(database_path)
    try:
        os.chmod(path.parent, 0o700)
        os.chmod(path, 0o600)
        initialize_document_schema(conn)
    except Exception:
        conn.close()
        raise
    return path, conn
