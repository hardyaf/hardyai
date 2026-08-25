#!/usr/bin/env python3
"""Print a content-free summary of Paperless task API rows for diagnostics."""

from __future__ import annotations

import argparse
import json
import urllib.request
import urllib.parse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://paperless-webserver:8000")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--task-id")
    args = parser.parse_args()
    token = args.token_file.read_text(encoding="utf-8").strip()
    query = urllib.parse.urlencode({"task_id": args.task_id}) if args.task_id else ""
    request = urllib.request.Request(
        f"{args.base_url.rstrip('/')}/api/tasks/{'?' + query if query else ''}",
        headers={
            "Authorization": f"Token {token}",
            "Accept": "application/json; version=10",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        value = json.load(response)
    rows = value.get("results", value) if isinstance(value, dict) else value
    safe_rows = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        safe_row = {"keys": sorted(str(key) for key in row)}
        safe_row.update(
            {
                key: row.get(key)
                for key in (
                    "id",
                    "task_id",
                    "status",
                    "task_type",
                    "result_data",
                    "related_document_ids",
                )
                if key in row
            }
        )
        safe_rows.append(safe_row)
    print(json.dumps({"count": len(safe_rows), "rows": safe_rows[:20]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
