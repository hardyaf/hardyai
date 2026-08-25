#!/usr/bin/env python3
"""Validate restored Paperless originals against a restored documents.db."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path


def _request(url: str, token: str) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Token {token}",
            "Accept": "application/json; version=10",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(), {name.casefold(): value for name, value in response.headers.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--search-text", default="Utility")
    args = parser.parse_args()
    token = args.token_file.read_text(encoding="utf-8").strip()
    connection = sqlite3.connect(f"file:{args.database.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT s.external_id, d.sha256, d.size_bytes
            FROM document_archive_sources AS s
            JOIN documents AS d ON d.document_id = s.document_id
            WHERE s.provider = 'paperless' AND d.state = 'ready'
            ORDER BY s.external_id
            """
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        raise RuntimeError("restored documents.db contains no ready Paperless mappings")

    base_url = args.base_url.rstrip("/")
    verified_ids: set[str] = set()
    observed_server_version = ""
    observed_api_version = ""
    for external_id, expected_sha256, expected_size in rows:
        payload, headers = _request(
            f"{base_url}/api/documents/{external_id}/download/?original=true",
            token,
        )
        if len(payload) != int(expected_size) or hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise RuntimeError(f"restored original mismatch for Paperless ID {external_id}")
        observed_server_version = headers.get("x-version", observed_server_version)
        observed_api_version = headers.get("x-api-version", observed_api_version)
        verified_ids.add(str(external_id))

    query = urllib.parse.urlencode({"text": args.search_text, "page_size": 100})
    payload, headers = _request(f"{base_url}/api/documents/?{query}", token)
    value = json.loads(payload)
    results = value.get("results", []) if isinstance(value, dict) else []
    search_ids = {str(row.get("id")) for row in results if isinstance(row, dict) and row.get("id") is not None}
    if not verified_ids.intersection(search_ids):
        raise RuntimeError("restored Paperless full-text search returned no mapped canary")
    observed_server_version = headers.get("x-version", observed_server_version)
    observed_api_version = headers.get("x-api-version", observed_api_version)
    if observed_server_version != "3.0.5" or observed_api_version != "10":
        raise RuntimeError("restored Paperless API/server version mismatch")

    print(
        json.dumps(
            {
                "status": "passed",
                "verified_originals": len(verified_ids),
                "search_matches": len(search_ids),
                "paperless_server_version": observed_server_version,
                "paperless_api_version": observed_api_version,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
