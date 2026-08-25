#!/usr/bin/env python3
"""Idempotently grant the Paperless read service access to archived mappings."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.config import settings
from app.integrations.paperless.adapter import PaperlessArchiveAdapter
from app.integrations.paperless.client import PaperlessClient


def main() -> int:
    read_user_id_path = Path(settings.paperless_read_user_id_path)
    if read_user_id_path.is_symlink():
        raise RuntimeError("Paperless read-user ID path must not be a symlink")
    read_user_id_text = read_user_id_path.read_text(encoding="utf-8").strip()
    if not read_user_id_text.isdigit() or int(read_user_id_text) <= 0:
        raise RuntimeError("Paperless read-user ID file is invalid")

    database_path = Path(settings.documents_database_path).resolve()
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT external_id
            FROM document_archive_sources
            WHERE provider = 'paperless'
            ORDER BY external_id
            """
        ).fetchall()
    finally:
        connection.close()

    client = PaperlessClient(
        base_url=settings.paperless_base_url,
        token_path=settings.paperless_archive_token_path,
        api_version=settings.paperless_api_version,
        server_version=settings.paperless_server_version,
        timeout_seconds=settings.paperless_timeout_seconds,
    )
    adapter = PaperlessArchiveAdapter(client, read_user_id=int(read_user_id_text))
    try:
        for (external_id,) in rows:
            adapter.grant_read_access(str(external_id))
    finally:
        client.close()
    print(json.dumps({"status": "passed", "reconciled": len(rows)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
