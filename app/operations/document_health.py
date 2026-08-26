from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from shutil import disk_usage
from typing import Any

from app.db.connection import open_readonly_sqlite_connection


def document_operational_health(
    *,
    core_database: Path,
    documents_database: Path,
    storage_root: Path,
    spool_quota_bytes: int,
    min_free_bytes: int,
    max_spool_age_seconds: int = 3600,
    max_heartbeat_age_seconds: int = 120,
    max_backup_age_seconds: int = 172800,
) -> dict[str, Any]:
    """Inspect only counts, timestamps, capacity, and integrity—never document content."""

    now = datetime.now(UTC)
    alerts: list[dict[str, object]] = []
    metrics: dict[str, object] = {}
    root = storage_root.resolve()
    free = disk_usage(root).free
    metrics["disk_free_bytes"] = free
    if free < min_free_bytes:
        alerts.append(_alert("disk_free_low", "critical", free))

    spool = root / "jarvis" / "spool"
    files = [path for path in spool.rglob("*") if path.is_file() and not path.is_symlink()] if spool.is_dir() else []
    spool_bytes = sum(path.stat().st_size for path in files)
    oldest_age = max((now.timestamp() - path.stat().st_mtime for path in files), default=0.0)
    metrics.update(spool_files=len(files), spool_bytes=spool_bytes, oldest_spool_age_seconds=round(oldest_age, 3))
    if spool_bytes >= spool_quota_bytes:
        alerts.append(_alert("spool_quota_exceeded", "critical", spool_bytes))
    if oldest_age > max_spool_age_seconds:
        alerts.append(_alert("spool_item_stale", "warning", round(oldest_age, 3)))

    with _readonly(core_database) as connection:
        _quick_check(connection, "core_database", alerts)
        queue_rows = connection.execute(
            """
            SELECT status, COUNT(*) FROM durable_jobs
            WHERE job_type LIKE 'document.%' GROUP BY status
            """
        ).fetchall()
        queue = {str(status): int(count) for status, count in queue_rows}
        metrics["document_jobs"] = queue
        if queue.get("dead_letter", 0):
            alerts.append(_alert("document_jobs_dead_lettered", "critical", queue["dead_letter"]))
        heartbeat = connection.execute(
            "SELECT status, last_seen_at, last_error_code FROM worker_heartbeats "
            "WHERE worker_type = 'documents' ORDER BY last_seen_at DESC LIMIT 1"
        ).fetchone()
        if heartbeat is None:
            alerts.append(_alert("document_worker_heartbeat_missing", "critical", None))
        else:
            seen = _timestamp(heartbeat[1])
            age = (now - seen).total_seconds() if seen else None
            metrics["document_worker"] = {
                "status": str(heartbeat[0]),
                "heartbeat_age_seconds": round(age, 3) if age is not None else None,
                "last_error_code": str(heartbeat[2]) if heartbeat[2] else None,
            }
            if age is None or age > max_heartbeat_age_seconds:
                alerts.append(_alert("document_worker_heartbeat_stale", "critical", age))
            if str(heartbeat[0]) == "degraded":
                alerts.append(_alert("document_worker_degraded", "warning", heartbeat[2]))

    with _readonly(documents_database) as connection:
        _quick_check(connection, "documents_database", alerts)
        states = connection.execute(
            "SELECT processing_state, COUNT(*) FROM documents GROUP BY processing_state"
        ).fetchall()
        metrics["document_states"] = {str(state): int(count) for state, count in states}

    manifests = [path for path in (root / "backups").glob("*/manifest.json") if path.is_file() and not path.is_symlink()]
    latest_backup_age = min((now.timestamp() - path.stat().st_mtime for path in manifests), default=None)
    metrics["backup_age_seconds"] = round(latest_backup_age, 3) if latest_backup_age is not None else None
    if latest_backup_age is None or latest_backup_age > max_backup_age_seconds:
        alerts.append(_alert("document_backup_stale_or_missing", "critical", latest_backup_age))

    return {
        "status": "critical" if any(item["severity"] == "critical" for item in alerts) else "warning" if alerts else "ok",
        "observed_at": now.isoformat(),
        "metrics": metrics,
        "alerts": alerts,
    }


@contextmanager
def _readonly(path: Path) -> Iterator[sqlite3.Connection]:
    _, connection = open_readonly_sqlite_connection(str(path))
    try:
        yield connection
    finally:
        connection.close()


def _quick_check(connection: sqlite3.Connection, code: str, alerts: list[dict[str, object]]) -> None:
    row = connection.execute("PRAGMA quick_check").fetchone()
    if row is None or row[0] != "ok":
        alerts.append(_alert(f"{code}_integrity_failed", "critical", None))


def _timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _alert(code: str, severity: str, value: object) -> dict[str, object]:
    return {"code": code, "severity": severity, "value": value}
