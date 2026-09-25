from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4

from app.db.connection import open_sqlite_connection
from app.db.migrations import initialize_schema
from app.tasks.types import TERMINAL_TASK_STATUSES, TaskStatus


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _json_load(value: Any, fallback: Any) -> Any:
    if not isinstance(value, str) or not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


class TaskConflictError(ValueError):
    pass


class TaskRepository:
    """Canonical Core-SQLite store for task state, learning, and effect receipts."""

    def __init__(self, database_path: str) -> None:
        self._database_path, self._conn = open_sqlite_connection(database_path)
        self._lock = RLock()
        initialize_schema(self._conn)

    @property
    def database_path(self) -> str:
        return str(self._database_path)

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield cursor
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    @staticmethod
    def _task_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for key in (
            "revision",
            "steering_revision",
            "run_generation",
            "model_decisions_limit",
            "model_decisions_used",
            "capability_calls_limit",
            "capability_calls_used",
        ):
            result[key] = int(result.get(key) or 0)
        result["budget_seconds_total"] = float(result.get("budget_seconds_total") or 0.0)
        result["budget_seconds_used"] = float(result.get("budget_seconds_used") or 0.0)
        result["budget"] = {
            "active_seconds": {
                "used": result["budget_seconds_used"],
                "limit": result["budget_seconds_total"],
                "remaining": max(
                    0.0,
                    result["budget_seconds_total"] - result["budget_seconds_used"],
                ),
            },
            "model_decisions": {
                "used": result["model_decisions_used"],
                "limit": result["model_decisions_limit"],
                "remaining": max(
                    0,
                    result["model_decisions_limit"] - result["model_decisions_used"],
                ),
            },
            "capability_calls": {
                "used": result["capability_calls_used"],
                "limit": result["capability_calls_limit"],
                "remaining": max(
                    0,
                    result["capability_calls_limit"] - result["capability_calls_used"],
                ),
            },
        }
        return result

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = _json_load(result.pop("payload_json", "{}"), {})
        return result

    @staticmethod
    def _message_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        raw_calls = result.pop("tool_calls_json", None)
        if raw_calls:
            result["tool_calls"] = _json_load(raw_calls, [])
        result["metadata"] = _json_load(result.pop("metadata_json", "{}"), {})
        return result

    def _append_event(
        self,
        cursor: sqlite3.Cursor,
        *,
        task_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        sequence = int(
            cursor.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_events WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
        )
        timestamp = created_at or _utc_now()
        cursor.execute(
            """
            INSERT INTO task_events (
                task_id, sequence, event_type, actor, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, sequence, event_type, actor, _json_dump(payload or {}), timestamp),
        )
        return {
            "event_id": int(cursor.lastrowid),
            "task_id": task_id,
            "sequence": sequence,
            "event_type": event_type,
            "actor": actor,
            "payload": payload or {},
            "created_at": timestamp,
        }

    def create_task(
        self,
        *,
        owner_id: str,
        source_interface: str,
        submission_id: str,
        goal: str,
        title: str,
        session_id: str | None,
        workspace_ref: str,
        budget_seconds: float,
        model_decisions: int,
        capability_calls: int,
    ) -> tuple[dict[str, Any], bool]:
        normalized_owner = str(owner_id or "").strip()
        normalized_submission = str(submission_id or "").strip()
        normalized_goal = str(goal or "").strip()
        if not normalized_owner or not normalized_submission or not normalized_goal:
            raise ValueError("task_identity_or_goal_missing")
        now = _utc_now()
        task_id = str(uuid4())
        with self._transaction(immediate=True) as cursor:
            existing = cursor.execute(
                "SELECT * FROM agent_tasks WHERE owner_id = ? AND submission_id = ?",
                (normalized_owner, normalized_submission),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["goal"]) != normalized_goal
                    or str(existing["source_interface"]) != str(source_interface or "").strip()
                ):
                    raise TaskConflictError("task_submission_id_conflict")
                return self._task_row(existing) or {}, False
            cursor.execute(
                """
                INSERT INTO agent_tasks (
                    task_id, owner_id, source_interface, session_id, submission_id,
                    title, goal, status, workspace_ref, budget_seconds_total,
                    model_decisions_limit, capability_calls_limit, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    normalized_owner,
                    str(source_interface or "local").strip(),
                    str(session_id or "").strip() or None,
                    normalized_submission,
                    str(title or normalized_goal[:100]).strip()[:160],
                    normalized_goal,
                    TaskStatus.QUEUED.value,
                    workspace_ref,
                    max(1.0, float(budget_seconds)),
                    max(1, int(model_decisions)),
                    max(1, int(capability_calls)),
                    now,
                    now,
                ),
            )
            self._append_event(
                cursor,
                task_id=task_id,
                event_type="task.created",
                actor=normalized_owner,
                payload={"title": str(title or normalized_goal[:100]).strip()[:160]},
                created_at=now,
            )
            self._append_message(
                cursor,
                task_id=task_id,
                role="user",
                content=normalized_goal,
                metadata={"kind": "goal", "submission_id": normalized_submission},
                created_at=now,
            )
            row = cursor.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._task_row(row) or {}, True

    def get_task(self, *, task_id: str, owner_id: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM agent_tasks WHERE task_id = ?"
        values: list[Any] = [str(task_id)]
        if owner_id is not None:
            sql += " AND owner_id = ?"
            values.append(str(owner_id))
        with self._lock:
            row = self._conn.execute(sql, values).fetchone()
        return self._task_row(row)

    def list_tasks(
        self,
        *,
        owner_id: str,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        values: list[Any] = [owner_id]
        sql = "SELECT * FROM agent_tasks WHERE owner_id = ?"
        if status:
            sql += " AND status = ?"
            values.append(str(status))
        sql += " ORDER BY updated_at DESC LIMIT ?"
        values.append(max(1, min(int(limit), 500)))
        with self._lock:
            rows = self._conn.execute(sql, values).fetchall()
        return [self._task_row(row) or {} for row in rows]

    def queued_tasks(self, *, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agent_tasks WHERE status = ? ORDER BY created_at LIMIT ?",
                (TaskStatus.QUEUED.value, max(1, min(int(limit), 2000))),
            ).fetchall()
        return [self._task_row(row) or {} for row in rows]

    def set_active_job(self, *, task_id: str, job_id: str, generation: int) -> bool:
        with self._transaction(immediate=True) as cursor:
            cursor.execute(
                """
                UPDATE agent_tasks SET active_job_id = ?, updated_at = ?
                WHERE task_id = ? AND run_generation = ? AND status = ?
                """,
                (job_id, _utc_now(), task_id, int(generation), TaskStatus.QUEUED.value),
            )
            return int(cursor.rowcount or 0) == 1

    def advance_queued_generation(
        self, *, task_id: str, expected_generation: int
    ) -> dict[str, Any]:
        """Move a queued task past a terminal orphan job from an interrupted enqueue."""

        with self._transaction(immediate=True) as cursor:
            cursor.execute(
                """
                UPDATE agent_tasks
                SET run_generation = run_generation + 1, active_job_id = NULL,
                    revision = revision + 1, updated_at = ?
                WHERE task_id = ? AND status = ? AND run_generation = ?
                """,
                (
                    _utc_now(),
                    task_id,
                    TaskStatus.QUEUED.value,
                    int(expected_generation),
                ),
            )
            if int(cursor.rowcount or 0) != 1:
                raise TaskConflictError("task_queue_generation_changed")
            row = cursor.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._task_row(row) or {}

    def set_workspace_ref(self, *, task_id: str, workspace_ref: str) -> dict[str, Any]:
        """Bind a queued task to its owner-scoped workspace before enqueue."""

        normalized = str(workspace_ref or "").strip()
        if not normalized:
            raise ValueError("task_workspace_ref_invalid")
        now = _utc_now()
        with self._transaction(immediate=True) as cursor:
            cursor.execute(
                """
                UPDATE agent_tasks
                SET workspace_ref = ?, updated_at = ?
                WHERE task_id = ? AND status = ? AND active_job_id IS NULL
                """,
                (normalized, now, task_id, TaskStatus.QUEUED.value),
            )
            if int(cursor.rowcount or 0) != 1:
                raise ValueError("task_workspace_binding_conflict")
            row = cursor.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._task_row(row) or {}

    def list_events(
        self,
        *,
        task_id: str,
        owner_id: str,
        after_event_id: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with self._lock:
            owner = self._conn.execute(
                "SELECT 1 FROM agent_tasks WHERE task_id = ? AND owner_id = ?",
                (task_id, owner_id),
            ).fetchone()
            if owner is None:
                return []
            rows = self._conn.execute(
                """
                SELECT * FROM task_events
                WHERE task_id = ? AND event_id > ?
                ORDER BY event_id LIMIT ?
                """,
                (task_id, max(0, int(after_event_id)), max(1, min(int(limit), 2000))),
            ).fetchall()
        return [self._event_row(row) for row in rows]

    def append_event(
        self,
        *,
        task_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._transaction(immediate=True) as cursor:
            return self._append_event(
                cursor,
                task_id=task_id,
                event_type=event_type,
                actor=actor,
                payload=payload,
            )

    def _append_message(
        self,
        cursor: sqlite3.Cursor,
        *,
        task_id: str,
        role: str,
        content: str,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        sequence = int(
            cursor.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_messages WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
        )
        message_id = str(uuid4())
        timestamp = created_at or _utc_now()
        cursor.execute(
            """
            INSERT INTO task_messages (
                message_id, task_id, sequence, role, content, tool_name,
                tool_call_id, tool_calls_json, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                task_id,
                sequence,
                role,
                content,
                tool_name,
                tool_call_id,
                _json_dump(tool_calls) if tool_calls is not None else None,
                _json_dump(metadata or {}),
                timestamp,
            ),
        )
        return {
            "message_id": message_id,
            "task_id": task_id,
            "sequence": sequence,
            "role": role,
            "content": content,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "tool_calls": tool_calls,
            "metadata": metadata or {},
            "created_at": timestamp,
        }

    def append_message(self, **kwargs: Any) -> dict[str, Any]:
        with self._transaction(immediate=True) as cursor:
            return self._append_message(cursor, **kwargs)

    def list_messages(self, *, task_id: str, limit: int = 500) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 2000))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM task_messages WHERE task_id = ?
                    ORDER BY sequence DESC LIMIT ?
                ) ORDER BY sequence
                """,
                (task_id, bounded),
            ).fetchall()
        return [self._message_row(row) for row in rows]

    def add_user_message(
        self,
        *,
        task_id: str,
        owner_id: str,
        expected_revision: int,
        submission_id: str,
        content: str,
    ) -> tuple[dict[str, Any], bool]:
        normalized = str(content or "").strip()
        if not normalized:
            raise ValueError("task_message_empty")
        with self._transaction(immediate=True) as cursor:
            row = cursor.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ? AND owner_id = ?",
                (task_id, owner_id),
            ).fetchone()
            if row is None:
                raise KeyError("task_not_found")
            prior = cursor.execute(
                """
                SELECT metadata_json FROM task_messages
                WHERE task_id = ? AND role = 'user'
                ORDER BY sequence
                """,
                (task_id,),
            ).fetchall()
            for item in prior:
                if _json_load(item["metadata_json"], {}).get("submission_id") == submission_id:
                    return self._task_row(row) or {}, False
            if int(row["revision"]) != int(expected_revision):
                raise TaskConflictError("task_revision_conflict")
            if str(row["status"]) in TERMINAL_TASK_STATUSES:
                raise TaskConflictError("task_terminal")
            previous_status = str(row["status"])
            should_requeue = previous_status in {
                TaskStatus.WAITING_INPUT.value,
                TaskStatus.WAITING_APPROVAL.value,
                TaskStatus.FAILED.value,
            }
            next_status = TaskStatus.QUEUED.value if should_requeue else previous_status
            next_generation = int(row["run_generation"]) + (1 if should_requeue else 0)
            now = _utc_now()
            cursor.execute(
                """
                UPDATE agent_tasks
                SET status = ?, revision = revision + 1,
                    steering_revision = steering_revision + 1,
                    run_generation = ?, waiting_prompt = NULL,
                    active_job_id = CASE WHEN ? THEN NULL ELSE active_job_id END,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    next_status,
                    next_generation,
                    1 if should_requeue else 0,
                    now,
                    task_id,
                ),
            )
            self._append_message(
                cursor,
                task_id=task_id,
                role="user",
                content=normalized,
                metadata={"kind": "steering", "submission_id": submission_id},
                created_at=now,
            )
            self._append_event(
                cursor,
                task_id=task_id,
                event_type="task.steered",
                actor=owner_id,
                payload={"resumed": should_requeue},
                created_at=now,
            )
            updated = cursor.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._task_row(updated) or {}, True

    def pause(self, *, task_id: str, owner_id: str, expected_revision: int) -> dict[str, Any]:
        return self._owner_transition(
            task_id=task_id,
            owner_id=owner_id,
            expected_revision=expected_revision,
            status=TaskStatus.PAUSED_USER.value,
            event_type="task.paused_by_user",
        )

    def cancel(self, *, task_id: str, owner_id: str, expected_revision: int) -> dict[str, Any]:
        return self._owner_transition(
            task_id=task_id,
            owner_id=owner_id,
            expected_revision=expected_revision,
            status=TaskStatus.CANCELLED.value,
            event_type="task.cancelled",
        )

    def _owner_transition(
        self,
        *,
        task_id: str,
        owner_id: str,
        expected_revision: int,
        status: str,
        event_type: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._transaction(immediate=True) as cursor:
            row = cursor.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ? AND owner_id = ?",
                (task_id, owner_id),
            ).fetchone()
            if row is None:
                raise KeyError("task_not_found")
            if int(row["revision"]) != int(expected_revision):
                raise TaskConflictError("task_revision_conflict")
            if str(row["status"]) in TERMINAL_TASK_STATUSES:
                return self._task_row(row) or {}
            cursor.execute(
                """
                UPDATE agent_tasks SET status = ?, revision = revision + 1,
                    steering_revision = steering_revision + 1, updated_at = ?,
                    cancelled_at = CASE WHEN ? = ? THEN ? ELSE cancelled_at END
                WHERE task_id = ?
                """,
                (status, now, status, TaskStatus.CANCELLED.value, now, task_id),
            )
            self._append_event(
                cursor,
                task_id=task_id,
                event_type=event_type,
                actor=owner_id,
                payload={},
                created_at=now,
            )
            updated = cursor.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._task_row(updated) or {}

    def continue_task(
        self,
        *,
        task_id: str,
        owner_id: str,
        expected_revision: int,
        submission_id: str,
        add_seconds: float,
        add_model_decisions: int,
        add_capability_calls: int,
    ) -> tuple[dict[str, Any], bool]:
        now = _utc_now()
        with self._transaction(immediate=True) as cursor:
            row = cursor.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ? AND owner_id = ?",
                (task_id, owner_id),
            ).fetchone()
            if row is None:
                raise KeyError("task_not_found")
            existing = cursor.execute(
                "SELECT 1 FROM task_budget_grants WHERE task_id = ? AND submission_id = ?",
                (task_id, submission_id),
            ).fetchone()
            if existing is not None:
                return self._task_row(row) or {}, False
            if int(row["revision"]) != int(expected_revision):
                raise TaskConflictError("task_revision_conflict")
            if str(row["status"]) in TERMINAL_TASK_STATUSES:
                raise TaskConflictError("task_terminal")
            if str(row["status"]) not in {
                TaskStatus.PAUSED_BUDGET.value,
                TaskStatus.PAUSED_USER.value,
                TaskStatus.WAITING_INPUT.value,
                TaskStatus.WAITING_APPROVAL.value,
                TaskStatus.FAILED.value,
            }:
                raise TaskConflictError("task_not_resumable")
            cursor.execute(
                """
                INSERT INTO task_budget_grants (
                    grant_id, task_id, submission_id, added_seconds,
                    added_model_decisions, added_capability_calls, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    task_id,
                    submission_id,
                    max(0.0, float(add_seconds)),
                    max(0, int(add_model_decisions)),
                    max(0, int(add_capability_calls)),
                    owner_id,
                    now,
                ),
            )
            cursor.execute(
                """
                UPDATE agent_tasks
                SET status = ?, revision = revision + 1,
                    steering_revision = steering_revision + 1,
                    run_generation = run_generation + 1, active_job_id = NULL,
                    budget_seconds_total = budget_seconds_total + ?,
                    model_decisions_limit = model_decisions_limit + ?,
                    capability_calls_limit = capability_calls_limit + ?,
                    waiting_prompt = NULL, error_code = NULL, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    TaskStatus.QUEUED.value,
                    max(0.0, float(add_seconds)),
                    max(0, int(add_model_decisions)),
                    max(0, int(add_capability_calls)),
                    now,
                    task_id,
                ),
            )
            self._append_event(
                cursor,
                task_id=task_id,
                event_type="task.continued",
                actor=owner_id,
                payload={
                    "added_seconds": max(0.0, float(add_seconds)),
                    "added_model_decisions": max(0, int(add_model_decisions)),
                    "added_capability_calls": max(0, int(add_capability_calls)),
                },
                created_at=now,
            )
            updated = cursor.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._task_row(updated) or {}, True

    def begin_run(self, *, task_id: str, job_id: str, generation: int) -> dict[str, Any] | None:
        now = _utc_now()
        with self._transaction(immediate=True) as cursor:
            update = cursor.execute(
                """
                UPDATE agent_tasks SET status = ?, revision = revision + 1,
                    active_job_id = ?, started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE task_id = ? AND run_generation = ? AND status IN (?, ?)
                  AND (active_job_id IS NULL OR active_job_id = ?)
                """,
                (
                    TaskStatus.RUNNING.value,
                    job_id,
                    now,
                    now,
                    task_id,
                    int(generation),
                    TaskStatus.QUEUED.value,
                    TaskStatus.RUNNING.value,
                    job_id,
                ),
            )
            row = cursor.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None or str(row["active_job_id"] or "") != job_id:
                return None
            if int(row["run_generation"]) != int(generation):
                return None
            if int(update.rowcount or 0):
                self._append_event(
                    cursor,
                    task_id=task_id,
                    event_type="task.running",
                    actor="task_worker",
                    payload={"job_id": job_id, "generation": int(generation)},
                    created_at=now,
                )
        return self._task_row(row)

    def set_plan(
        self,
        *,
        task_id: str,
        plan_markdown: str,
        progress_summary: str | None = None,
        expected_steering_revision: int | None = None,
    ) -> dict[str, Any]:
        plan = str(plan_markdown or "").strip()[:20_000]
        summary = str(progress_summary or "").strip()[:2_000]
        now = _utc_now()
        with self._transaction(immediate=True) as cursor:
            cursor.execute(
                """
                UPDATE agent_tasks SET plan_markdown = ?,
                    progress_summary = CASE WHEN ? = '' THEN progress_summary ELSE ? END,
                    revision = revision + 1, updated_at = ?
                WHERE task_id = ? AND status = ?
                    AND (? IS NULL OR steering_revision = ?)
                """,
                (
                    plan,
                    summary,
                    summary,
                    now,
                    task_id,
                    TaskStatus.RUNNING.value,
                    expected_steering_revision,
                    expected_steering_revision,
                ),
            )
            if int(cursor.rowcount or 0) != 1:
                raise TaskConflictError("task_not_running_or_steering_changed")
            self._append_event(
                cursor,
                task_id=task_id,
                event_type="task.plan_updated",
                actor="assistant",
                payload={"plan": plan, "progress_summary": summary},
                created_at=now,
            )
            row = cursor.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._task_row(row) or {}

    def budget_available(self, *, task_id: str) -> tuple[bool, str | None, dict[str, Any]]:
        task = self.get_task(task_id=task_id)
        if task is None:
            return False, "task_not_found", {}
        if task["budget_seconds_used"] >= task["budget_seconds_total"]:
            return False, "active_time_exhausted", task
        if task["model_decisions_used"] >= task["model_decisions_limit"]:
            return False, "model_decisions_exhausted", task
        if task["capability_calls_used"] >= task["capability_calls_limit"]:
            return False, "capability_calls_exhausted", task
        return True, None, task

    def consume_usage(
        self,
        *,
        task_id: str,
        expected_steering_revision: int,
        active_seconds: float = 0.0,
        model_decisions: int = 0,
        capability_calls: int = 0,
    ) -> dict[str, Any]:
        with self._transaction(immediate=True) as cursor:
            row = cursor.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError("task_not_found")
            if str(row["status"]) != TaskStatus.RUNNING.value:
                raise TaskConflictError("task_not_running")
            if int(row["steering_revision"]) != int(expected_steering_revision):
                raise TaskConflictError("task_steering_changed")
            next_seconds = float(row["budget_seconds_used"] or 0.0) + max(
                0.0, float(active_seconds)
            )
            next_decisions = int(row["model_decisions_used"] or 0) + max(
                0, int(model_decisions)
            )
            next_calls = int(row["capability_calls_used"] or 0) + max(
                0, int(capability_calls)
            )
            # A call admitted inside its soft wall-clock allowance may finish after
            # that allowance. Preserve the actual elapsed time and pause at the next
            # action boundary; discrete model/tool units cannot be overdrawn.
            if (
                next_decisions > int(row["model_decisions_limit"])
                or next_calls > int(row["capability_calls_limit"])
            ):
                raise TaskConflictError("task_budget_exhausted")
            cursor.execute(
                """
                UPDATE agent_tasks SET budget_seconds_used = ?,
                    model_decisions_used = ?, capability_calls_used = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (next_seconds, next_decisions, next_calls, _utc_now(), task_id),
            )
            updated = cursor.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._task_row(updated) or {}

    def worker_transition(
        self,
        *,
        task_id: str,
        status: str,
        event_type: str,
        summary: str = "",
        final_result: str | None = None,
        waiting_prompt: str | None = None,
        error_code: str | None = None,
        expected_steering_revision: int | None = None,
    ) -> dict[str, Any]:
        TaskStatus(status)
        now = _utc_now()
        with self._transaction(immediate=True) as cursor:
            cursor.execute(
                """
                UPDATE agent_tasks SET status = ?, revision = revision + 1,
                    progress_summary = CASE WHEN ? = '' THEN progress_summary ELSE ? END,
                    final_result = COALESCE(?, final_result), waiting_prompt = ?,
                    error_code = ?, active_job_id = NULL, updated_at = ?,
                    completed_at = CASE WHEN ? = ? THEN ? ELSE completed_at END,
                    cancelled_at = CASE WHEN ? = ? THEN ? ELSE cancelled_at END
                WHERE task_id = ? AND status = ?
                    AND (? IS NULL OR steering_revision = ?)
                """,
                (
                    status,
                    summary[:2_000],
                    summary[:2_000],
                    final_result,
                    waiting_prompt,
                    error_code,
                    now,
                    status,
                    TaskStatus.COMPLETED.value,
                    now,
                    status,
                    TaskStatus.CANCELLED.value,
                    now,
                    task_id,
                    TaskStatus.RUNNING.value,
                    expected_steering_revision,
                    expected_steering_revision,
                ),
            )
            if int(cursor.rowcount or 0) != 1:
                raise TaskConflictError("task_not_running_or_steering_changed")
            self._append_event(
                cursor,
                task_id=task_id,
                event_type=event_type,
                actor="task_worker",
                payload={
                    "status": status,
                    "summary": summary[:2_000],
                    **({"prompt": waiting_prompt} if waiting_prompt else {}),
                    **({"error_code": error_code} if error_code else {}),
                },
                created_at=now,
            )
            row = cursor.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._task_row(row) or {}

    def reserve_effect(
        self,
        *,
        task_id: str,
        logical_operation_id: str,
        provider_operation_id: str,
        tool_id: str,
        arguments_hash: str,
        run_id: str | None,
    ) -> tuple[dict[str, Any], bool]:
        now = _utc_now()
        with self._transaction(immediate=True) as cursor:
            existing = cursor.execute(
                """
                SELECT * FROM task_effect_receipts
                WHERE task_id = ? AND logical_operation_id = ?
                """,
                (task_id, logical_operation_id),
            ).fetchone()
            if existing is not None:
                result = dict(existing)
                result["result"] = _json_load(result.pop("result_json"), {})
                if (
                    str(existing["tool_id"]) != tool_id
                    or str(existing["arguments_hash"]) != arguments_hash
                    or str(existing["provider_operation_id"]) != provider_operation_id
                ):
                    raise TaskConflictError("task_effect_operation_conflict")
                return result, False
            receipt_id = str(uuid4())
            cursor.execute(
                """
                INSERT INTO task_effect_receipts (
                    receipt_id, task_id, logical_operation_id, provider_operation_id,
                    tool_id, arguments_hash, state, run_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?)
                """,
                (
                    receipt_id,
                    task_id,
                    logical_operation_id,
                    provider_operation_id,
                    tool_id,
                    arguments_hash,
                    run_id,
                    now,
                    now,
                ),
            )
            self._append_event(
                cursor,
                task_id=task_id,
                event_type="task.effect_reserved",
                actor="task_worker",
                payload={
                    "logical_operation_id": logical_operation_id,
                    "tool_id": tool_id,
                    "receipt_id": receipt_id,
                },
                created_at=now,
            )
            row = cursor.execute(
                "SELECT * FROM task_effect_receipts WHERE receipt_id = ?", (receipt_id,)
            ).fetchone()
        result = dict(row) if row is not None else {}
        result["result"] = _json_load(result.pop("result_json", "{}"), {})
        return result, True

    def finish_effect(
        self,
        *,
        task_id: str,
        logical_operation_id: str,
        state: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if state not in {
            "committed",
            "no_effect",
            "uncertain",
            "waiting_approval",
            "failed",
        }:
            raise ValueError("task_effect_state_invalid")
        encoded = _json_dump(result)
        if len(encoded) > 64_000:
            encoded = _json_dump({"status": result.get("status"), "truncated": True})
        now = _utc_now()
        with self._transaction(immediate=True) as cursor:
            cursor.execute(
                """
                UPDATE task_effect_receipts SET state = ?, result_json = ?, updated_at = ?
                WHERE task_id = ? AND logical_operation_id = ?
                """,
                (state, encoded, now, task_id, logical_operation_id),
            )
            if int(cursor.rowcount or 0) != 1:
                raise KeyError("task_effect_not_found")
            self._append_event(
                cursor,
                task_id=task_id,
                event_type="task.effect_recorded",
                actor="task_worker",
                payload={
                    "logical_operation_id": logical_operation_id,
                    "state": state,
                    "status": result.get("status"),
                },
                created_at=now,
            )
            row = cursor.execute(
                """
                SELECT * FROM task_effect_receipts
                WHERE task_id = ? AND logical_operation_id = ?
                """,
                (task_id, logical_operation_id),
            ).fetchone()
        output = dict(row) if row is not None else {}
        output["result"] = _json_load(output.pop("result_json", "{}"), {})
        return output

    def list_effects(self, *, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM task_effect_receipts WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["result"] = _json_load(item.pop("result_json"), {})
            output.append(item)
        return output

    def create_script_run(
        self,
        *,
        task_id: str,
        logical_run_id: str,
        source: str,
        source_ref: str,
        workspace_ref: str,
        steering_revision: int,
    ) -> tuple[dict[str, Any], bool]:
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        now = _utc_now()
        with self._transaction(immediate=True) as cursor:
            existing = cursor.execute(
                """
                SELECT * FROM task_script_runs
                WHERE task_id = ? AND logical_run_id = ? AND source_sha256 = ?
                """,
                (task_id, logical_run_id, digest),
            ).fetchone()
            if existing is not None:
                return self._script_row(existing), False
            run_id = str(uuid4())
            cursor.execute(
                """
                INSERT INTO task_script_runs (
                    run_id, task_id, logical_run_id, source_sha256, source_ref,
                    workspace_ref, steering_revision, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?)
                """,
                (
                    run_id,
                    task_id,
                    logical_run_id,
                    digest,
                    source_ref,
                    workspace_ref,
                    int(steering_revision),
                    now,
                    now,
                ),
            )
            row = cursor.execute(
                "SELECT * FROM task_script_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._script_row(row), True

    @staticmethod
    def _script_row(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        result = dict(row)
        result["artifacts"] = _json_load(result.pop("artifacts_json", "[]"), [])
        return result

    def mark_script_running(self, *, run_id: str) -> None:
        now = _utc_now()
        with self._transaction(immediate=True) as cursor:
            cursor.execute(
                """
                UPDATE task_script_runs SET status = 'running', started_at = COALESCE(started_at, ?),
                    updated_at = ? WHERE run_id = ? AND status IN ('prepared', 'interrupted')
                """,
                (now, now, run_id),
            )

    def finish_script_run(
        self,
        *,
        run_id: str,
        status: str,
        exit_code: int | None,
        stdout_text: str,
        stderr_text: str,
        artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if status not in {"completed", "failed", "timed_out", "interrupted", "diverged"}:
            raise ValueError("task_script_status_invalid")
        now = _utc_now()
        with self._transaction(immediate=True) as cursor:
            cursor.execute(
                """
                UPDATE task_script_runs SET status = ?, exit_code = ?, stdout_text = ?,
                    stderr_text = ?, artifacts_json = ?, completed_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    exit_code,
                    stdout_text[:131_072],
                    stderr_text[:131_072],
                    _json_dump(artifacts[:100]),
                    now,
                    now,
                    run_id,
                ),
            )
            row = cursor.execute(
                "SELECT * FROM task_script_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._script_row(row)

    def list_script_runs(self, *, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM task_script_runs WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            ).fetchall()
        return [self._script_row(row) for row in rows]

    def save_preference(
        self,
        *,
        owner_id: str,
        scope: str,
        rule_text: str,
        source_instruction: str,
        skill_id: str | None = None,
        preference_id: str | None = None,
        active: bool = True,
        task_id: str | None = None,
        expected_steering_revision: int | None = None,
    ) -> dict[str, Any]:
        normalized_scope = str(scope or "").strip().casefold()
        if normalized_scope not in {"general", "project", "skill"}:
            raise ValueError("preference_scope_invalid")
        normalized_rule = str(rule_text or "").strip()
        if not normalized_rule or len(normalized_rule) > 4_000:
            raise ValueError("preference_rule_invalid")
        stable_id = str(preference_id or uuid4())
        now = _utc_now()
        with self._transaction(immediate=True) as cursor:
            if task_id is not None:
                boundary = cursor.execute(
                    "SELECT status, steering_revision FROM agent_tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                if (
                    boundary is None
                    or str(boundary["status"]) != TaskStatus.RUNNING.value
                    or int(boundary["steering_revision"]) != int(expected_steering_revision or 0)
                ):
                    raise TaskConflictError("task_not_running_or_steering_changed")
            owner_row = cursor.execute(
                "SELECT owner_id FROM user_preferences WHERE preference_id = ? LIMIT 1",
                (stable_id,),
            ).fetchone()
            if owner_row is not None and str(owner_row["owner_id"]) != owner_id:
                raise TaskConflictError("preference_owner_conflict")
            current = cursor.execute(
                "SELECT MAX(revision) FROM user_preferences WHERE owner_id = ? AND preference_id = ?",
                (owner_id, stable_id),
            ).fetchone()[0]
            revision = int(current or 0) + 1
            cursor.execute(
                "UPDATE user_preferences SET active = 0 WHERE owner_id = ? AND preference_id = ? AND active = 1",
                (owner_id, stable_id),
            )
            cursor.execute(
                """
                INSERT INTO user_preferences (
                    preference_id, revision, owner_id, scope, skill_id, rule_text,
                    source_instruction, active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stable_id,
                    revision,
                    owner_id,
                    normalized_scope,
                    str(skill_id or "").strip() or None,
                    normalized_rule,
                    str(source_instruction or normalized_rule).strip()[:4_000],
                    1 if active else 0,
                    now,
                ),
            )
            row = cursor.execute(
                """
                SELECT * FROM user_preferences
                WHERE preference_id = ? AND revision = ?
                """,
                (stable_id, revision),
            ).fetchone()
        return self._learning_row(row)

    @staticmethod
    def _learning_row(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        result = dict(row)
        result["revision"] = int(result["revision"])
        result["active"] = bool(result["active"])
        return result

    def list_preferences(
        self,
        *,
        owner_id: str,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM user_preferences WHERE owner_id = ?"
        values: list[Any] = [owner_id]
        if active_only:
            sql += " AND active = 1"
        sql += " ORDER BY CASE scope WHEN 'project' THEN 1 WHEN 'skill' THEN 2 ELSE 3 END, created_at"
        with self._lock:
            rows = self._conn.execute(sql, values).fetchall()
        return [self._learning_row(row) for row in rows]

    def preference_history(self, *, owner_id: str, preference_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM user_preferences
                WHERE owner_id = ? AND preference_id = ? ORDER BY revision DESC
                """,
                (owner_id, preference_id),
            ).fetchall()
        return [self._learning_row(row) for row in rows]

    def retire_preference(self, *, owner_id: str, preference_id: str) -> bool:
        with self._transaction(immediate=True) as cursor:
            cursor.execute(
                "UPDATE user_preferences SET active = 0 WHERE owner_id = ? AND preference_id = ? AND active = 1",
                (owner_id, preference_id),
            )
            return int(cursor.rowcount or 0) > 0

    def restore_preference(
        self, *, owner_id: str, preference_id: str, revision: int
    ) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM user_preferences
                WHERE owner_id = ? AND preference_id = ? AND revision = ?
                """,
                (owner_id, preference_id, int(revision)),
            ).fetchone()
        if row is None:
            raise KeyError("preference_revision_not_found")
        return self.save_preference(
            owner_id=owner_id,
            preference_id=preference_id,
            scope=str(row["scope"]),
            skill_id=str(row["skill_id"] or "") or None,
            rule_text=str(row["rule_text"]),
            source_instruction=f"Restored revision {int(revision)}",
        )

    def save_skill_revision(
        self,
        *,
        owner_id: str,
        skill_id: str,
        title: str,
        instructions_markdown: str,
        source_instruction: str,
        base_skill_id: str | None = None,
        active: bool = True,
        task_id: str | None = None,
        expected_steering_revision: int | None = None,
    ) -> dict[str, Any]:
        normalized_id = str(skill_id or "").strip().casefold()
        content = str(instructions_markdown or "").strip()
        if not normalized_id.startswith("skill.") or len(normalized_id) > 160:
            raise ValueError("skill_id_invalid")
        if not content or len(content) > 40_000:
            raise ValueError("skill_instructions_invalid")
        now = _utc_now()
        with self._transaction(immediate=True) as cursor:
            if task_id is not None:
                boundary = cursor.execute(
                    "SELECT status, steering_revision FROM agent_tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                if (
                    boundary is None
                    or str(boundary["status"]) != TaskStatus.RUNNING.value
                    or int(boundary["steering_revision"]) != int(expected_steering_revision or 0)
                ):
                    raise TaskConflictError("task_not_running_or_steering_changed")
            current = cursor.execute(
                "SELECT MAX(revision) FROM user_skill_revisions WHERE owner_id = ? AND skill_id = ?",
                (owner_id, normalized_id),
            ).fetchone()[0]
            revision = int(current or 0) + 1
            cursor.execute(
                "UPDATE user_skill_revisions SET active = 0 WHERE owner_id = ? AND skill_id = ? AND active = 1",
                (owner_id, normalized_id),
            )
            cursor.execute(
                """
                INSERT INTO user_skill_revisions (
                    skill_id, revision, owner_id, title, instructions_markdown,
                    base_skill_id, active, source_instruction, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_id,
                    revision,
                    owner_id,
                    str(title or normalized_id).strip()[:160],
                    content,
                    str(base_skill_id or "").strip().casefold() or None,
                    1 if active else 0,
                    str(source_instruction or "").strip()[:4_000],
                    now,
                ),
            )
            row = cursor.execute(
                """
                SELECT * FROM user_skill_revisions
                WHERE owner_id = ? AND skill_id = ? AND revision = ?
                """,
                (owner_id, normalized_id, revision),
            ).fetchone()
        return self._learning_row(row)

    def list_user_skills(
        self,
        *,
        owner_id: str,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM user_skill_revisions WHERE owner_id = ?"
        values: list[Any] = [owner_id]
        if active_only:
            sql += " AND active = 1"
        sql += " ORDER BY title, revision DESC"
        with self._lock:
            rows = self._conn.execute(sql, values).fetchall()
        return [self._learning_row(row) for row in rows]

    def get_user_skill(self, *, owner_id: str, skill_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM user_skill_revisions
                WHERE owner_id = ? AND skill_id = ? AND active = 1
                ORDER BY revision DESC LIMIT 1
                """,
                (owner_id, str(skill_id or "").strip().casefold()),
            ).fetchone()
        return self._learning_row(row) if row is not None else None

    def skill_history(self, *, owner_id: str, skill_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM user_skill_revisions
                WHERE owner_id = ? AND skill_id = ? ORDER BY revision DESC
                """,
                (owner_id, str(skill_id or "").strip().casefold()),
            ).fetchall()
        return [self._learning_row(row) for row in rows]

    def retire_user_skill(self, *, owner_id: str, skill_id: str) -> bool:
        normalized = str(skill_id or "").strip().casefold()
        with self._transaction(immediate=True) as cursor:
            cursor.execute(
                "UPDATE user_skill_revisions SET active = 0 WHERE owner_id = ? AND skill_id = ? AND active = 1",
                (owner_id, normalized),
            )
            return int(cursor.rowcount or 0) > 0

    def restore_user_skill(
        self, *, owner_id: str, skill_id: str, revision: int
    ) -> dict[str, Any]:
        normalized = str(skill_id or "").strip().casefold()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM user_skill_revisions
                WHERE owner_id = ? AND skill_id = ? AND revision = ?
                """,
                (owner_id, normalized, int(revision)),
            ).fetchone()
        if row is None:
            raise KeyError("skill_revision_not_found")
        return self.save_skill_revision(
            owner_id=owner_id,
            skill_id=normalized,
            title=str(row["title"]),
            instructions_markdown=str(row["instructions_markdown"]),
            source_instruction=f"Restored revision {int(revision)}",
            base_skill_id=str(row["base_skill_id"] or "") or None,
        )

    def delete_task(self, *, owner_id: str, task_id: str) -> bool:
        with self._transaction(immediate=True) as cursor:
            row = cursor.execute(
                "SELECT status FROM agent_tasks WHERE owner_id = ? AND task_id = ?",
                (owner_id, task_id),
            ).fetchone()
            if row is None:
                return False
            if str(row["status"]) in {TaskStatus.QUEUED.value, TaskStatus.RUNNING.value}:
                raise TaskConflictError("task_active")
            cursor.execute(
                "DELETE FROM agent_tasks WHERE owner_id = ? AND task_id = ?",
                (owner_id, task_id),
            )
            return int(cursor.rowcount or 0) == 1

    def recent_task_context(self, *, owner_id: str, exclude_task_id: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT task_id, title, progress_summary, final_result, completed_at
                FROM agent_tasks
                WHERE owner_id = ? AND task_id != ? AND status = ?
                ORDER BY completed_at DESC LIMIT ?
                """,
                (
                    owner_id,
                    exclude_task_id,
                    TaskStatus.COMPLETED.value,
                    max(1, min(int(limit), 20)),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
