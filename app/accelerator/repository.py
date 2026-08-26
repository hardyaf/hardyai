from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import uuid4

from app.accelerator.types import AcceleratorLease
from app.db.connection import open_sqlite_connection
from app.db.accelerator_schema import ensure_accelerator_schema


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class AcceleratorLeaseRepository:
    """Durable, content-free waiter and fencing ledger for one shared accelerator."""

    def __init__(self, database_path: str) -> None:
        self.database_path, self._conn = open_sqlite_connection(database_path)
        self._lock = Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            ensure_accelerator_schema(self._conn)
            now = _timestamp(_utc_now())
            self._conn.execute(
                """
                INSERT INTO accelerator_resources(resource_id, updated_at)
                VALUES ('gpu0', ?)
                ON CONFLICT(resource_id) DO NOTHING
                """,
                (now,),
            )
            self._conn.commit()

    @staticmethod
    def _lane(value: str) -> str:
        lane = str(value or "").strip().casefold()
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", lane):
            raise ValueError("accelerator_lane_invalid")
        return lane

    def enqueue(
        self,
        *,
        lane: str,
        priority: int,
        wait_seconds: float,
        resource_id: str = "gpu0",
    ) -> str:
        normalized_lane = self._lane(lane)
        bounded_priority = max(1, min(int(priority), 1000))
        now = _utc_now()
        waiter_id = str(uuid4())
        with self._lock:
            self._expire_locked(now)
            self._conn.execute(
                """
                INSERT INTO accelerator_waiters(
                    waiter_id, resource_id, lane, priority, status, created_at, expires_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    waiter_id,
                    resource_id,
                    normalized_lane,
                    bounded_priority,
                    _timestamp(now),
                    _timestamp(now + timedelta(seconds=max(1.0, float(wait_seconds)))),
                ),
            )
            self._conn.commit()
        return waiter_id

    def try_acquire(self, *, waiter_id: str, lease_seconds: float) -> AcceleratorLease | None:
        now = _utc_now()
        now_text = _timestamp(now)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._expire_locked(now)
                waiter = self._conn.execute(
                    """
                    SELECT waiter_id, resource_id, lane, priority
                    FROM accelerator_waiters
                    WHERE waiter_id = ? AND status = 'queued' AND expires_at > ?
                    """,
                    (waiter_id, now_text),
                ).fetchone()
                if waiter is None:
                    self._conn.commit()
                    return None
                resource = self._conn.execute(
                    """
                    SELECT fencing_token, lease_owner, lease_expires_at
                    FROM accelerator_resources WHERE resource_id = ?
                    """,
                    (str(waiter["resource_id"]),),
                ).fetchone()
                if resource is None:
                    raise RuntimeError("accelerator_resource_missing")
                if resource["lease_owner"] and str(resource["lease_expires_at"] or "") > now_text:
                    self._conn.commit()
                    return None
                first = self._conn.execute(
                    """
                    SELECT waiter_id FROM accelerator_waiters
                    WHERE resource_id = ? AND status = 'queued' AND expires_at > ?
                    ORDER BY priority DESC, created_at, waiter_id
                    LIMIT 1
                    """,
                    (str(waiter["resource_id"]), now_text),
                ).fetchone()
                if first is None or str(first["waiter_id"]) != waiter_id:
                    self._conn.commit()
                    return None
                fencing_token = int(resource["fencing_token"] or 0) + 1
                expires_at = _timestamp(
                    now + timedelta(seconds=max(5.0, min(float(lease_seconds), 900.0)))
                )
                self._conn.execute(
                    """
                    UPDATE accelerator_resources
                    SET fencing_token = ?, lease_owner = ?, lease_lane = ?, lease_priority = ?,
                        lease_expires_at = ?, updated_at = ?
                    WHERE resource_id = ?
                    """,
                    (
                        fencing_token,
                        waiter_id,
                        str(waiter["lane"]),
                        int(waiter["priority"]),
                        expires_at,
                        now_text,
                        str(waiter["resource_id"]),
                    ),
                )
                self._conn.execute(
                    """
                    UPDATE accelerator_waiters SET status = 'acquired', acquired_at = ?
                    WHERE waiter_id = ? AND status = 'queued'
                    """,
                    (now_text, waiter_id),
                )
                self._conn.commit()
                return AcceleratorLease(
                    waiter_id=waiter_id,
                    resource_id=str(waiter["resource_id"]),
                    lane=str(waiter["lane"]),
                    priority=int(waiter["priority"]),
                    fencing_token=fencing_token,
                    lease_expires_at=expires_at,
                )
            except Exception:
                self._conn.rollback()
                raise

    def heartbeat(self, *, lease: AcceleratorLease, lease_seconds: float) -> bool:
        now = _utc_now()
        expires_at = _timestamp(
            now + timedelta(seconds=max(5.0, min(float(lease_seconds), 900.0)))
        )
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE accelerator_resources
                SET lease_expires_at = ?, updated_at = ?
                WHERE resource_id = ? AND lease_owner = ? AND fencing_token = ?
                """,
                (
                    expires_at,
                    _timestamp(now),
                    lease.resource_id,
                    lease.waiter_id,
                    lease.fencing_token,
                ),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    def release(self, *, lease: AcceleratorLease) -> bool:
        now = _timestamp(_utc_now())
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """
                    UPDATE accelerator_resources
                    SET lease_owner = NULL, lease_lane = NULL, lease_priority = NULL,
                        lease_expires_at = NULL, updated_at = ?
                    WHERE resource_id = ? AND lease_owner = ? AND fencing_token = ?
                    """,
                    (now, lease.resource_id, lease.waiter_id, lease.fencing_token),
                )
                if cursor.rowcount == 1:
                    self._conn.execute(
                        """
                        UPDATE accelerator_waiters SET status = 'completed', completed_at = ?
                        WHERE waiter_id = ? AND status = 'acquired'
                        """,
                        (now, lease.waiter_id),
                    )
                self._conn.commit()
                return cursor.rowcount == 1
            except Exception:
                self._conn.rollback()
                raise

    def cancel_waiter(self, waiter_id: str) -> None:
        now = _timestamp(_utc_now())
        with self._lock:
            self._conn.execute(
                """
                UPDATE accelerator_waiters SET status = 'cancelled', completed_at = ?
                WHERE waiter_id = ? AND status = 'queued'
                """,
                (now, waiter_id),
            )
            self._conn.commit()

    def snapshot(self) -> dict[str, object]:
        now = _utc_now()
        with self._lock:
            self._expire_locked(now)
            resource = self._conn.execute(
                """
                SELECT resource_id, fencing_token, lease_lane, lease_priority, lease_expires_at
                FROM accelerator_resources WHERE resource_id = 'gpu0'
                """
            ).fetchone()
            queued = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM accelerator_waiters WHERE status = 'queued'"
                ).fetchone()[0]
            )
            self._conn.commit()
        return {
            "resource_id": str(resource["resource_id"]) if resource else "gpu0",
            "fencing_token": int(resource["fencing_token"] or 0) if resource else 0,
            "lease_lane": str(resource["lease_lane"] or "") if resource else "",
            "lease_priority": int(resource["lease_priority"] or 0) if resource else 0,
            "lease_expires_at": str(resource["lease_expires_at"] or "") if resource else "",
            "queued": queued,
        }

    def _expire_locked(self, now: datetime) -> None:
        now_text = _timestamp(now)
        expired_owners = [
            str(row[0])
            for row in self._conn.execute(
                """
                SELECT lease_owner FROM accelerator_resources
                WHERE lease_owner IS NOT NULL AND lease_expires_at <= ?
                """,
                (now_text,),
            ).fetchall()
        ]
        self._conn.execute(
            """
            UPDATE accelerator_resources
            SET lease_owner = NULL, lease_lane = NULL, lease_priority = NULL,
                lease_expires_at = NULL, updated_at = ?
            WHERE lease_owner IS NOT NULL AND lease_expires_at <= ?
            """,
            (now_text, now_text),
        )
        if expired_owners:
            self._conn.executemany(
                """
                UPDATE accelerator_waiters SET status = 'expired', completed_at = ?
                WHERE waiter_id = ? AND status = 'acquired'
                """,
                ((now_text, owner) for owner in expired_owners),
            )
        self._conn.execute(
            """
            UPDATE accelerator_waiters SET status = 'expired', completed_at = ?
            WHERE status = 'queued' AND expires_at <= ?
            """,
            (now_text, now_text),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
