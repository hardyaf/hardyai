from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, ContextManager
from uuid import uuid4

from app.db.connection import open_sqlite_connection
from app.db.migrations import initialize_schema
from app.db.transaction import sqlite_transaction
from app.jobs.types import JobStatus, ResourceClass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_utc(value: datetime | None = None) -> str:
    current = value or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _json_load(value: Any, fallback: Any) -> Any:
    if not isinstance(value, str) or not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


class DurableJobRepository:
    """Generic leased job ledger backed by the existing core SQLite schema."""

    def __init__(
        self,
        database_path: str | None = None,
        *,
        connection: sqlite3.Connection | None = None,
        lock: RLock | None = None,
    ) -> None:
        if connection is None:
            if not database_path:
                raise ValueError("database_path is required when connection is not supplied")
            self._database_path, self._conn = open_sqlite_connection(database_path)
            self._owns_connection = True
            self._lock = lock or RLock()
            initialize_schema(self._conn)
        else:
            self._database_path = None
            self._conn = connection
            self._owns_connection = False
            self._lock = lock or RLock()

    @property
    def database_path(self) -> str | None:
        return str(self._database_path) if self._database_path is not None else None

    def _transaction(self, *, immediate: bool = False) -> ContextManager[sqlite3.Cursor]:
        return sqlite_transaction(conn=self._conn, lock=self._lock, immediate=immediate)

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["payload"] = _json_load(payload.pop("payload_json"), {})
        return payload

    def enqueue_job(
        self,
        *,
        job_type: str,
        aggregate_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        available_at: str | None = None,
        max_attempts: int = 3,
        priority: int = 100,
        resource_class: str = ResourceClass.CPU_SMALL.value,
        total_deadline_at: str | None = None,
        cursor: sqlite3.Cursor | None = None,
    ) -> dict[str, Any]:
        now = _iso_utc()
        values = (
            str(uuid4()),
            job_type,
            aggregate_id,
            idempotency_key,
            _json_dump(payload),
            JobStatus.PENDING.value,
            available_at or now,
            max(1, int(max_attempts)),
            max(0, min(int(priority), 1000)),
            ResourceClass(resource_class).value,
            total_deadline_at,
            now,
            now,
        )
        sql = """
            INSERT INTO durable_jobs (
                job_id, job_type, aggregate_id, idempotency_key, payload_json,
                status, available_at, max_attempts, priority, resource_class,
                total_deadline_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO NOTHING
        """
        if cursor is not None:
            cursor.execute(sql, values)
            row = cursor.execute(
                "SELECT * FROM durable_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("durable job enqueue did not produce a row")
            return self._job_row(row)
        with self._transaction(immediate=True) as cur:
            cur.execute(sql, values)
            row = cur.execute(
                "SELECT * FROM durable_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            raise RuntimeError("durable job enqueue did not produce a row")
        return self._job_row(row)

    def claim_jobs(
        self,
        *,
        job_type: str,
        worker_id: str,
        limit: int,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        current = now or _utc_now()
        current_text = _iso_utc(current)
        lease_text = _iso_utc(current + timedelta(seconds=max(1.0, float(lease_seconds))))
        bounded = max(1, min(int(limit), 100))
        claimed: list[sqlite3.Row] = []
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE durable_jobs
                SET status = ?, cancelled_at = ?, lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE status IN (?, ?) AND cancel_requested_at IS NOT NULL
                """,
                (
                    JobStatus.CANCELLED.value,
                    current_text,
                    current_text,
                    JobStatus.PENDING.value,
                    JobStatus.RETRY.value,
                ),
            )
            cur.execute(
                """
                UPDATE durable_jobs
                SET status = ?, last_error_code = 'total_deadline_exceeded',
                    lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE status IN (?, ?) AND total_deadline_at IS NOT NULL
                  AND total_deadline_at <= ?
                """,
                (
                    JobStatus.DEAD_LETTER.value,
                    current_text,
                    JobStatus.PENDING.value,
                    JobStatus.RETRY.value,
                    current_text,
                ),
            )
            cur.execute(
                """
                UPDATE durable_jobs
                SET status = CASE
                        WHEN cancel_requested_at IS NOT NULL THEN ?
                        WHEN attempt_count >= max_attempts THEN ?
                        ELSE ?
                    END,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    available_at = ?,
                    updated_at = ?,
                    cancelled_at = CASE
                        WHEN cancel_requested_at IS NOT NULL THEN ? ELSE cancelled_at END,
                    last_error_code = CASE
                        WHEN cancel_requested_at IS NOT NULL THEN 'cancelled' ELSE 'lease_expired' END
                WHERE status = ? AND lease_expires_at <= ?
                """,
                (
                    JobStatus.CANCELLED.value,
                    JobStatus.DEAD_LETTER.value,
                    JobStatus.RETRY.value,
                    current_text,
                    current_text,
                    current_text,
                    JobStatus.RUNNING.value,
                    current_text,
                ),
            )
            rows = cur.execute(
                """
                SELECT job_id FROM durable_jobs
                WHERE job_type = ?
                  AND status IN (?, ?)
                  AND available_at <= ?
                  AND attempt_count < max_attempts
                  AND cancel_requested_at IS NULL
                  AND (total_deadline_at IS NULL OR total_deadline_at > ?)
                ORDER BY priority, available_at, created_at
                LIMIT ?
                """,
                (
                    job_type,
                    JobStatus.PENDING.value,
                    JobStatus.RETRY.value,
                    current_text,
                    current_text,
                    bounded,
                ),
            ).fetchall()
            for row in rows:
                cur.execute(
                    """
                    UPDATE durable_jobs
                    SET status = ?, lease_owner = ?, lease_expires_at = ?,
                        attempt_count = attempt_count + 1,
                        lease_fencing_token = lease_fencing_token + 1,
                        updated_at = ?
                    WHERE job_id = ? AND status IN (?, ?)
                      AND cancel_requested_at IS NULL
                    """,
                    (
                        JobStatus.RUNNING.value,
                        worker_id,
                        lease_text,
                        current_text,
                        row["job_id"],
                        JobStatus.PENDING.value,
                        JobStatus.RETRY.value,
                    ),
                )
                if int(cur.rowcount or 0) == 1:
                    claimed_row = cur.execute(
                        "SELECT * FROM durable_jobs WHERE job_id = ?",
                        (row["job_id"],),
                    ).fetchone()
                    if claimed_row is not None:
                        claimed.append(claimed_row)
        return [self._job_row(row) for row in claimed]

    def complete_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        fencing_token: int | None = None,
    ) -> bool:
        token_sql = "" if fencing_token is None else " AND lease_fencing_token = ?"
        values: list[Any] = [
            JobStatus.COMPLETED.value,
            _iso_utc(),
            job_id,
            JobStatus.RUNNING.value,
            worker_id,
        ]
        if fencing_token is not None:
            values.append(int(fencing_token))
        with self._transaction(immediate=True) as cur:
            cur.execute(
                f"""
                UPDATE durable_jobs
                SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                    progress_current = CASE
                        WHEN progress_total IS NULL THEN progress_current ELSE progress_total END,
                    updated_at = ?
                WHERE job_id = ? AND status = ? AND lease_owner = ?
                  AND cancel_requested_at IS NULL{token_sql}
                """,
                values,
            )
            return int(cur.rowcount or 0) == 1

    def retry_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        delay_seconds: float,
        fencing_token: int | None = None,
    ) -> bool:
        now = _utc_now()
        with self._transaction(immediate=True) as cur:
            token_sql = "" if fencing_token is None else " AND lease_fencing_token = ?"
            select_values: list[Any] = [job_id, worker_id]
            if fencing_token is not None:
                select_values.append(int(fencing_token))
            row = cur.execute(
                "SELECT attempt_count, max_attempts, cancel_requested_at FROM durable_jobs "
                f"WHERE job_id = ? AND lease_owner = ?{token_sql}",
                select_values,
            ).fetchone()
            if row is None:
                return False
            cancelled = row["cancel_requested_at"] is not None
            status = JobStatus.CANCELLED.value if cancelled else (
                JobStatus.DEAD_LETTER.value
                if int(row["attempt_count"]) >= int(row["max_attempts"])
                else JobStatus.RETRY.value
            )
            update_values: list[Any] = [
                status,
                _iso_utc(now + timedelta(seconds=max(0.0, float(delay_seconds)))),
                error_code[:120],
                _iso_utc(now) if cancelled else None,
                _iso_utc(now),
                job_id,
                worker_id,
            ]
            if fencing_token is not None:
                update_values.append(int(fencing_token))
            cur.execute(
                f"""
                UPDATE durable_jobs
                SET status = ?, available_at = ?, lease_owner = NULL,
                    lease_expires_at = NULL, last_error_code = ?, cancelled_at = ?, updated_at = ?
                WHERE job_id = ? AND lease_owner = ?{token_sql}
                """,
                update_values,
            )
            return int(cur.rowcount or 0) == 1

    def defer_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        fencing_token: int,
        delay_seconds: float,
        reconcile_state: str,
    ) -> bool:
        """Release an asynchronous provider poll without consuming a failure attempt."""

        now = _utc_now()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE durable_jobs
                SET status = ?, available_at = ?, attempt_count = MAX(0, attempt_count - 1),
                    lease_owner = NULL, lease_expires_at = NULL,
                    provider_reconcile_state = ?, updated_at = ?
                WHERE job_id = ? AND status = ? AND lease_owner = ?
                  AND lease_fencing_token = ? AND cancel_requested_at IS NULL
                """,
                (
                    JobStatus.RETRY.value,
                    _iso_utc(now + timedelta(seconds=max(0.0, float(delay_seconds)))),
                    str(reconcile_state or "pending")[:80],
                    _iso_utc(now),
                    job_id,
                    JobStatus.RUNNING.value,
                    worker_id,
                    int(fencing_token),
                ),
            )
            return int(cur.rowcount or 0) == 1

    def release_jobs(
        self,
        *,
        job_type: str,
        aggregate_id: str,
        reconcile_state: str,
    ) -> int:
        """Wake content-free subscriptions after their aggregate reaches new durable state."""

        normalized_type = str(job_type or "").strip()
        normalized_aggregate = str(aggregate_id or "").strip()
        normalized_state = str(reconcile_state or "").strip().casefold()[:80]
        if not normalized_type or not normalized_aggregate or not normalized_state:
            raise ValueError("job type, aggregate ID, and reconciliation state are required")
        now = _iso_utc()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE durable_jobs
                SET available_at = ?, provider_reconcile_state = ?, updated_at = ?
                WHERE job_type = ? AND aggregate_id = ? AND status IN (?, ?)
                  AND cancel_requested_at IS NULL
                """,
                (
                    now,
                    normalized_state,
                    now,
                    normalized_type,
                    normalized_aggregate,
                    JobStatus.PENDING.value,
                    JobStatus.RETRY.value,
                ),
            )
            return int(cur.rowcount or 0)

    def dead_letter_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        fencing_token: int,
        error_code: str,
    ) -> bool:
        """Fail closed for a claimed job that must never be retried automatically."""

        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE durable_jobs
                SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                    last_error_code = ?, updated_at = ?
                WHERE job_id = ? AND status = ? AND lease_owner = ?
                  AND lease_fencing_token = ?
                """,
                (
                    JobStatus.DEAD_LETTER.value,
                    str(error_code or "job_rejected")[:120],
                    _iso_utc(),
                    job_id,
                    JobStatus.RUNNING.value,
                    worker_id,
                    int(fencing_token),
                ),
            )
            return int(cur.rowcount or 0) == 1

    def renew_lease(
        self,
        *,
        job_id: str,
        worker_id: str,
        fencing_token: int,
        lease_seconds: float,
    ) -> bool:
        now = _utc_now()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE durable_jobs
                SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND status = ? AND lease_owner = ?
                  AND lease_fencing_token = ? AND cancel_requested_at IS NULL
                """,
                (
                    _iso_utc(now + timedelta(seconds=max(1.0, float(lease_seconds)))),
                    _iso_utc(now),
                    job_id,
                    JobStatus.RUNNING.value,
                    worker_id,
                    int(fencing_token),
                ),
            )
            return int(cur.rowcount or 0) == 1

    def update_progress(
        self,
        *,
        job_id: str,
        worker_id: str,
        fencing_token: int,
        stage: str,
        current: int,
        total: int | None = None,
    ) -> bool:
        normalized_stage = str(stage or "").strip().casefold()
        if not normalized_stage or len(normalized_stage) > 80:
            raise ValueError("invalid job stage")
        bounded_current = max(0, int(current))
        bounded_total = max(bounded_current, int(total)) if total is not None else None
        now = _iso_utc()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE durable_jobs
                SET current_stage = ?, progress_current = ?, progress_total = ?,
                    stage_started_at = CASE WHEN current_stage = ? THEN stage_started_at ELSE ? END,
                    updated_at = ?
                WHERE job_id = ? AND status = ? AND lease_owner = ?
                  AND lease_fencing_token = ? AND cancel_requested_at IS NULL
                """,
                (
                    normalized_stage,
                    bounded_current,
                    bounded_total,
                    normalized_stage,
                    now,
                    now,
                    job_id,
                    JobStatus.RUNNING.value,
                    worker_id,
                    int(fencing_token),
                ),
            )
            return int(cur.rowcount or 0) == 1

    def set_provider_operation(
        self,
        *,
        job_id: str,
        worker_id: str,
        fencing_token: int,
        operation_ref: str,
        reconcile_state: str,
    ) -> bool:
        operation = str(operation_ref or "").strip()[:240]
        state = str(reconcile_state or "").strip().casefold()[:80]
        if not operation or not state:
            raise ValueError("provider operation and reconciliation state are required")
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE durable_jobs
                SET provider_operation_ref = ?, provider_reconcile_state = ?, updated_at = ?
                WHERE job_id = ? AND status = ? AND lease_owner = ?
                  AND lease_fencing_token = ? AND cancel_requested_at IS NULL
                """,
                (
                    operation,
                    state,
                    _iso_utc(),
                    job_id,
                    JobStatus.RUNNING.value,
                    worker_id,
                    int(fencing_token),
                ),
            )
            return int(cur.rowcount or 0) == 1

    def request_cancel(self, *, job_id: str) -> dict[str, Any] | None:
        now = _iso_utc()
        with self._transaction(immediate=True) as cur:
            row = cur.execute("SELECT status FROM durable_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            status = str(row["status"])
            if status in {JobStatus.COMPLETED.value, JobStatus.DEAD_LETTER.value, JobStatus.CANCELLED.value}:
                persisted = cur.execute("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)).fetchone()
                return self._job_row(persisted) if persisted is not None else None
            cur.execute(
                """
                UPDATE durable_jobs
                SET cancel_requested_at = COALESCE(cancel_requested_at, ?),
                    status = CASE WHEN status IN (?, ?) THEN ? ELSE status END,
                    cancelled_at = CASE WHEN status IN (?, ?) THEN ? ELSE cancelled_at END,
                    lease_owner = CASE WHEN status IN (?, ?) THEN NULL ELSE lease_owner END,
                    lease_expires_at = CASE WHEN status IN (?, ?) THEN NULL ELSE lease_expires_at END,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (
                    now,
                    JobStatus.PENDING.value,
                    JobStatus.RETRY.value,
                    JobStatus.CANCELLED.value,
                    JobStatus.PENDING.value,
                    JobStatus.RETRY.value,
                    now,
                    JobStatus.PENDING.value,
                    JobStatus.RETRY.value,
                    JobStatus.PENDING.value,
                    JobStatus.RETRY.value,
                    now,
                    job_id,
                ),
            )
            persisted = cur.execute("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job_row(persisted) if persisted is not None else None

    def acknowledge_cancel(
        self,
        *,
        job_id: str,
        worker_id: str,
        fencing_token: int,
    ) -> bool:
        now = _iso_utc()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE durable_jobs
                SET status = ?, cancelled_at = ?, lease_owner = NULL,
                    lease_expires_at = NULL, last_error_code = 'cancelled', updated_at = ?
                WHERE job_id = ? AND status = ? AND lease_owner = ?
                  AND lease_fencing_token = ? AND cancel_requested_at IS NOT NULL
                """,
                (
                    JobStatus.CANCELLED.value,
                    now,
                    now,
                    job_id,
                    JobStatus.RUNNING.value,
                    worker_id,
                    int(fencing_token),
                ),
            )
            return int(cur.rowcount or 0) == 1

    def requeue_job(self, *, job_id: str, delay_seconds: float = 0.0) -> dict[str, Any] | None:
        now = _utc_now()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE durable_jobs
                SET status = ?, available_at = ?, attempt_count = 0,
                    lease_owner = NULL, lease_expires_at = NULL,
                    cancel_requested_at = NULL, cancelled_at = NULL,
                    last_error_code = NULL, updated_at = ?
                WHERE job_id = ? AND status IN (?, ?)
                """,
                (
                    JobStatus.RETRY.value,
                    _iso_utc(now + timedelta(seconds=max(0.0, float(delay_seconds)))),
                    _iso_utc(now),
                    job_id,
                    JobStatus.DEAD_LETTER.value,
                    JobStatus.CANCELLED.value,
                ),
            )
            row = cur.execute("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job_row(row) if row is not None else None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job_row(row) if row is not None else None

    def list_jobs(
        self,
        *,
        job_type: str | None = None,
        status: JobStatus | str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 1000))
        status_value = JobStatus(status).value if status is not None else None
        with self._lock:
            if job_type and status_value:
                rows = self._conn.execute(
                    """
                    SELECT * FROM durable_jobs
                    WHERE job_type = ? AND status = ?
                    ORDER BY updated_at DESC LIMIT ?
                    """,
                    (job_type, status_value, bounded),
                ).fetchall()
            elif job_type:
                rows = self._conn.execute(
                    "SELECT * FROM durable_jobs WHERE job_type = ? ORDER BY created_at DESC LIMIT ?",
                    (job_type, bounded),
                ).fetchall()
            elif status_value:
                rows = self._conn.execute(
                    "SELECT * FROM durable_jobs WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                    (status_value, bounded),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM durable_jobs ORDER BY created_at DESC LIMIT ?",
                    (bounded,),
                ).fetchall()
        return [self._job_row(row) for row in rows]

    def record_worker_heartbeat(
        self,
        *,
        worker_type: str,
        worker_id: str,
        status: str,
        last_error_code: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                INSERT INTO worker_heartbeats (
                    worker_type, worker_id, status, last_seen_at,
                    last_error_code, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_type, worker_id) DO UPDATE SET
                    status = excluded.status,
                    last_seen_at = excluded.last_seen_at,
                    last_error_code = excluded.last_error_code,
                    metadata_json = excluded.metadata_json
                """,
                (
                    worker_type,
                    worker_id,
                    status,
                    _iso_utc(),
                    last_error_code,
                    _json_dump(metadata or {}),
                ),
            )

    def close(self) -> None:
        if not self._owns_connection:
            return
        with self._lock:
            self._conn.close()
