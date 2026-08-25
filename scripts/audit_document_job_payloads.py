#!/usr/bin/env python3
"""Verify document durable-job payloads contain only approved opaque fields."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path


ALLOWED_KEYS = {
    "document.archive.v1": {"document_id", "intake_id", "sha256"},
    "document.process.v1": {"document_id", "source_version_id", "run_id"},
}


def audit_database(database: Path, *, require_processing: bool = False) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT job_type, status, payload_json
            FROM durable_jobs
            WHERE job_type IN ('document.archive.v1', 'document.process.v1')
            """
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        raise RuntimeError("no document jobs were found")
    statuses: dict[str, Counter[str]] = {
        job_type: Counter() for job_type in ALLOWED_KEYS
    }
    observed_types: set[str] = set()
    for row in rows:
        job_type = str(row["job_type"])
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict) or set(payload) != ALLOWED_KEYS[job_type]:
            raise RuntimeError("a document job contains an unapproved payload shape")
        if not all(
            isinstance(payload[key], str)
            and 1 <= len(payload[key]) <= 200
            and not any(character.isspace() for character in payload[key])
            for key in ALLOWED_KEYS[job_type]
        ):
            raise RuntimeError("a document job contains an invalid opaque reference")
        if job_type == "document.archive.v1" and not re.fullmatch(
            r"[0-9a-f]{64}", payload["sha256"]
        ):
            raise RuntimeError("a document job contains an invalid SHA-256")
        observed_types.add(job_type)
        statuses[job_type][str(row["status"])] += 1
    if "document.archive.v1" not in observed_types:
        raise RuntimeError("no document archive jobs were found")
    if require_processing and "document.process.v1" not in observed_types:
        raise RuntimeError("no document processing jobs were found")
    return {
        "status": "passed",
        "jobs": len(rows),
        "job_types": {
            job_type: dict(statuses[job_type])
            for job_type in sorted(observed_types)
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--require-processing", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            audit_database(args.database, require_processing=args.require_processing),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
