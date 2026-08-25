#!/usr/bin/env python3
"""Print a content-minimized summary of a Paperless full-text API query."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://paperless-webserver:8000")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--text", required=True)
    args = parser.parse_args()
    token = args.token_file.read_text(encoding="utf-8").strip()
    query = urllib.parse.urlencode({"text": args.text, "page_size": 20})
    request = urllib.request.Request(
        f"{args.base_url.rstrip('/')}/api/documents/?{query}",
        headers={
            "Authorization": f"Token {token}",
            "Accept": "application/json; version=10",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        value = json.load(response)
    rows = value.get("results", value) if isinstance(value, dict) else value
    safe_rows = [
        {"id": row.get("id"), "title": str(row.get("title") or "")[:100]}
        for row in rows if isinstance(rows, list) and isinstance(row, dict)
    ]
    print(json.dumps({"count": len(safe_rows), "rows": safe_rows}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
