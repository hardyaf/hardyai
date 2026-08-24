from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4


class CalendarInboxSQLiteStorage:
    """Durable slot, Gmail-message, and reconciled-event ledger for calendar ingestion."""

    def __init__(self, database_path: str) -> None:
        path = Path(database_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.database_path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS calendar_inbox_state (
                    state_key TEXT PRIMARY KEY,
                    state_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS calendar_inbox_runs (
                    run_id TEXT PRIMARY KEY,
                    slot_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    scanned_count INTEGER NOT NULL DEFAULT 0,
                    imported_count INTEGER NOT NULL DEFAULT 0,
                    updated_count INTEGER NOT NULL DEFAULT 0,
                    existing_count INTEGER NOT NULL DEFAULT 0,
                    ignored_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS calendar_inbox_messages (
                    gmail_message_id TEXT PRIMARY KEY,
                    gmail_thread_id TEXT,
                    gmail_internal_date TEXT,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    outcome_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES calendar_inbox_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS calendar_inbox_events (
                    source_key TEXT PRIMARY KEY,
                    gmail_message_id TEXT NOT NULL,
                    ical_uid TEXT NOT NULL,
                    recurrence_id TEXT,
                    house_calendar_id TEXT NOT NULL,
                    google_event_id TEXT,
                    action TEXT NOT NULL,
                    payload_hash TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (gmail_message_id) REFERENCES calendar_inbox_messages(gmail_message_id)
                );

                CREATE INDEX IF NOT EXISTS idx_calendar_inbox_runs_status
                    ON calendar_inbox_runs(status, slot_key);
                CREATE INDEX IF NOT EXISTS idx_calendar_inbox_messages_status
                    ON calendar_inbox_messages(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_calendar_inbox_events_uid
                    ON calendar_inbox_events(ical_uid, recurrence_id);
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get_or_create_activation_time(self, *, now: str) -> str:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO calendar_inbox_state(state_key, state_value, updated_at)
                VALUES ('activated_at', ?, ?)
                """,
                (now, now),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT state_value FROM calendar_inbox_state WHERE state_key = 'activated_at'"
            ).fetchone()
        return str(row["state_value"] if row is not None else now)

    def claim_run(
        self,
        *,
        slot_key: str,
        now: str,
        stale_before: str,
        max_attempts: int,
    ) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            row = cur.execute(
                "SELECT * FROM calendar_inbox_runs WHERE slot_key = ?",
                (slot_key,),
            ).fetchone()
            if row is None:
                run_id = str(uuid4())
                cur.execute(
                    """
                    INSERT INTO calendar_inbox_runs(
                        run_id, slot_key, status, attempt_count, created_at, updated_at
                    ) VALUES (?, ?, 'running', 1, ?, ?)
                    """,
                    (run_id, slot_key, now, now),
                )
                self._conn.commit()
                return {"claimed": True, "run_id": run_id, "attempt_count": 1}

            existing = dict(row)
            status = str(existing.get("status") or "").casefold()
            attempts = int(existing.get("attempt_count") or 0)
            if status == "completed":
                self._conn.commit()
                return {"claimed": False, "reason": "completed", **existing}
            if attempts >= max(1, int(max_attempts)):
                if status != "dead_letter":
                    cur.execute(
                        "UPDATE calendar_inbox_runs SET status = 'dead_letter', updated_at = ? WHERE run_id = ?",
                        (now, existing["run_id"]),
                    )
                self._conn.commit()
                return {"claimed": False, "reason": "max_attempts", **existing}
            if status == "running" and str(existing.get("updated_at") or "") >= stale_before:
                self._conn.commit()
                return {"claimed": False, "reason": "leased", **existing}

            attempts += 1
            cur.execute(
                """
                UPDATE calendar_inbox_runs
                SET status = 'running', attempt_count = ?, last_error = NULL, updated_at = ?
                WHERE run_id = ?
                """,
                (attempts, now, existing["run_id"]),
            )
            self._conn.commit()
            return {"claimed": True, "run_id": existing["run_id"], "attempt_count": attempts}

    def finish_run(self, *, run_id: str, result: dict[str, Any], now: str) -> None:
        counts = {
            key: max(0, int(result.get(key) or 0))
            for key in (
                "scanned_count",
                "imported_count",
                "updated_count",
                "existing_count",
                "ignored_count",
                "failed_count",
            )
        }
        with self._lock:
            self._conn.execute(
                """
                UPDATE calendar_inbox_runs
                SET status = 'completed', scanned_count = ?, imported_count = ?, updated_count = ?,
                    existing_count = ?, ignored_count = ?, failed_count = ?, last_error = NULL,
                    result_json = ?, updated_at = ?, completed_at = ?
                WHERE run_id = ?
                """,
                (
                    counts["scanned_count"],
                    counts["imported_count"],
                    counts["updated_count"],
                    counts["existing_count"],
                    counts["ignored_count"],
                    counts["failed_count"],
                    json.dumps(result, sort_keys=True),
                    now,
                    now,
                    run_id,
                ),
            )
            self._conn.commit()

    def fail_run(self, *, run_id: str, error: str, now: str, max_attempts: int) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT attempt_count FROM calendar_inbox_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            attempts = int(row["attempt_count"] if row is not None else 0)
            status = "dead_letter" if attempts >= max(1, int(max_attempts)) else "failed"
            self._conn.execute(
                """
                UPDATE calendar_inbox_runs
                SET status = ?, last_error = ?, updated_at = ? WHERE run_id = ?
                """,
                (status, str(error)[:1000], now, run_id),
            )
            self._conn.commit()
        return {"status": status, "attempt_count": attempts}

    def claim_message(
        self,
        *,
        gmail_message_id: str,
        gmail_thread_id: str | None,
        gmail_internal_date: str | None,
        run_id: str,
        now: str,
        stale_before: str,
        max_attempts: int,
    ) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            row = cur.execute(
                "SELECT * FROM calendar_inbox_messages WHERE gmail_message_id = ?",
                (gmail_message_id,),
            ).fetchone()
            if row is None:
                cur.execute(
                    """
                    INSERT INTO calendar_inbox_messages(
                        gmail_message_id, gmail_thread_id, gmail_internal_date, run_id,
                        status, attempt_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'processing', 1, ?, ?)
                    """,
                    (gmail_message_id, gmail_thread_id, gmail_internal_date, run_id, now, now),
                )
                self._conn.commit()
                return {"claimed": True, "attempt_count": 1}

            existing = dict(row)
            status = str(existing.get("status") or "").casefold()
            attempts = int(existing.get("attempt_count") or 0)
            if status in {"completed", "ignored"}:
                self._conn.commit()
                return {"claimed": False, "reason": status, **existing}
            if attempts >= max(1, int(max_attempts)):
                if status != "dead_letter":
                    cur.execute(
                        """
                        UPDATE calendar_inbox_messages
                        SET status = 'dead_letter', updated_at = ? WHERE gmail_message_id = ?
                        """,
                        (now, gmail_message_id),
                    )
                self._conn.commit()
                return {"claimed": False, "reason": "max_attempts", **existing}
            if status == "processing" and str(existing.get("updated_at") or "") >= stale_before:
                self._conn.commit()
                return {"claimed": False, "reason": "leased", **existing}

            attempts += 1
            cur.execute(
                """
                UPDATE calendar_inbox_messages
                SET run_id = ?, status = 'processing', attempt_count = ?, last_error = NULL, updated_at = ?
                WHERE gmail_message_id = ?
                """,
                (run_id, attempts, now, gmail_message_id),
            )
            self._conn.commit()
            return {"claimed": True, "attempt_count": attempts}

    def finish_message(
        self,
        *,
        gmail_message_id: str,
        outcome: dict[str, Any],
        ignored: bool,
        now: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE calendar_inbox_messages
                SET status = ?, outcome_json = ?, last_error = NULL, updated_at = ?, completed_at = ?
                WHERE gmail_message_id = ?
                """,
                (
                    "ignored" if ignored else "completed",
                    json.dumps(outcome, sort_keys=True),
                    now,
                    now,
                    gmail_message_id,
                ),
            )
            self._conn.commit()

    def fail_message(
        self,
        *,
        gmail_message_id: str,
        error: str,
        now: str,
        max_attempts: int,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT attempt_count FROM calendar_inbox_messages WHERE gmail_message_id = ?",
                (gmail_message_id,),
            ).fetchone()
            attempts = int(row["attempt_count"] if row is not None else 0)
            status = "dead_letter" if attempts >= max(1, int(max_attempts)) else "failed"
            self._conn.execute(
                """
                UPDATE calendar_inbox_messages
                SET status = ?, last_error = ?, updated_at = ? WHERE gmail_message_id = ?
                """,
                (status, str(error)[:1000], now, gmail_message_id),
            )
            self._conn.commit()
        return {"status": status, "attempt_count": attempts}

    def record_event(
        self,
        *,
        source_key: str,
        gmail_message_id: str,
        ical_uid: str,
        recurrence_id: str | None,
        house_calendar_id: str,
        google_event_id: str | None,
        action: str,
        payload_hash: str | None,
        result: dict[str, Any],
        now: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO calendar_inbox_events(
                    source_key, gmail_message_id, ical_uid, recurrence_id, house_calendar_id,
                    google_event_id, action, payload_hash, result_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    gmail_message_id = excluded.gmail_message_id,
                    google_event_id = excluded.google_event_id,
                    action = excluded.action,
                    payload_hash = excluded.payload_hash,
                    result_json = excluded.result_json,
                    updated_at = excluded.updated_at
                """,
                (
                    source_key,
                    gmail_message_id,
                    ical_uid,
                    recurrence_id,
                    house_calendar_id,
                    google_event_id,
                    action,
                    payload_hash,
                    json.dumps(result, sort_keys=True),
                    now,
                    now,
                ),
            )
            self._conn.commit()

    def get_run(self, *, slot_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM calendar_inbox_runs WHERE slot_key = ?",
                (slot_key,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_message(self, *, gmail_message_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM calendar_inbox_messages WHERE gmail_message_id = ?",
                (gmail_message_id,),
            ).fetchone()
        return dict(row) if row is not None else None
