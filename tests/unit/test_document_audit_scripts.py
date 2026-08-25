from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.audit_document_content_boundaries import audit_database as audit_content
from scripts.audit_document_job_payloads import audit_database as audit_jobs


def _database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE durable_jobs (job_type TEXT, status TEXT, payload_json TEXT)"
    )
    connection.execute("CREATE TABLE events (event_type TEXT, payload_json TEXT)")
    return connection


def test_document_job_audit_accepts_archive_and_processing_opaque_payloads(tmp_path: Path) -> None:
    database = tmp_path / "core.db"
    connection = _database(database)
    try:
        connection.executemany(
            "INSERT INTO durable_jobs VALUES (?, ?, ?)",
            [
                (
                    "document.archive.v1",
                    "succeeded",
                    json.dumps(
                        {"document_id": "doc-1", "intake_id": "intake-1", "sha256": "a" * 64}
                    ),
                ),
                (
                    "document.process.v1",
                    "succeeded",
                    json.dumps(
                        {
                            "document_id": "doc-1",
                            "source_version_id": "source-1",
                            "run_id": "run-1",
                        }
                    ),
                ),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    result = audit_jobs(database, require_processing=True)

    assert result["status"] == "passed"
    assert result["jobs"] == 2


def test_document_job_audit_rejects_content_bearing_processing_payload(tmp_path: Path) -> None:
    database = tmp_path / "core.db"
    connection = _database(database)
    try:
        connection.executemany(
            "INSERT INTO durable_jobs VALUES (?, ?, ?)",
            [
                (
                    "document.archive.v1",
                    "succeeded",
                    json.dumps(
                        {"document_id": "doc-1", "intake_id": "intake-1", "sha256": "a" * 64}
                    ),
                ),
                (
                    "document.process.v1",
                    "queued",
                    json.dumps(
                        {
                            "document_id": "doc-1",
                            "source_version_id": "source-1",
                            "run_id": "run-1",
                            "text": "must-not-cross-boundary",
                        }
                    ),
                ),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    try:
        audit_jobs(database, require_processing=True)
    except RuntimeError as exc:
        assert "unapproved payload shape" in str(exc)
    else:
        raise AssertionError("content-bearing job payload was accepted")


def test_content_boundary_audit_reports_only_column_locations(tmp_path: Path) -> None:
    database = tmp_path / "core.db"
    connection = _database(database)
    try:
        connection.execute("INSERT INTO events VALUES (?, ?)", ("safe", "opaque"))
        connection.commit()
    finally:
        connection.close()

    assert audit_content(database, ["synthetic-canary"])["status"] == "passed"

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO events VALUES (?, ?)",
            ("unsafe", "contains synthetic-canary here"),
        )
        connection.commit()
    finally:
        connection.close()

    result = audit_content(database, ["synthetic-canary"])
    assert result["status"] == "failed"
    assert result["exposures"] == ["events.payload_json"]
