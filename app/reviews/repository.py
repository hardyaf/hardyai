from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from threading import RLock
from typing import Any, ContextManager
from uuid import uuid4

from app.db.connection import open_sqlite_connection
from app.db.migrations import initialize_schema
from app.db.transaction import sqlite_transaction
from app.reviews.types import ReviewDecisionKind, ReviewRequest, ReviewState


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


class HumanReviewRepository:
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

    def _transaction(self, *, immediate: bool = False) -> ContextManager[sqlite3.Cursor]:
        return sqlite_transaction(conn=self._conn, lock=self._lock, immediate=immediate)

    @staticmethod
    def _item(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for source, target, fallback in (
            ("validator_summary_json", "validator_summary", []),
            ("evidence_refs_json", "evidence_refs", []),
        ):
            raw = value.pop(source)
            try:
                value[target] = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                value[target] = fallback
        return value

    def expire_due(self, *, now: str | None = None) -> int:
        observed = now or _now()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE review_items
                SET state = ?, updated_at = ?
                WHERE state = ? AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (ReviewState.EXPIRED.value, observed, ReviewState.PENDING.value, observed),
            )
            return int(cur.rowcount or 0)

    def create(self, request: ReviewRequest) -> dict[str, Any]:
        observed = _now()
        review_id = str(uuid4())
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                INSERT INTO review_items (
                    review_id, review_kind, subject_type, subject_id, subject_version,
                    item_hash, source_ref, sensitivity, confidence,
                    validator_summary_json, evidence_refs_json, target_operation,
                    authorization_binding, state, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(review_kind, subject_type, subject_id, subject_version, item_hash)
                DO NOTHING
                """,
                (
                    review_id,
                    request.review_kind.value,
                    request.subject_type,
                    request.subject_id,
                    request.subject_version,
                    request.item_hash,
                    request.source_ref,
                    request.sensitivity,
                    request.confidence,
                    _json(list(request.validator_summary)),
                    _json(list(request.evidence_refs)),
                    request.target_operation,
                    request.authorization_binding,
                    ReviewState.PENDING.value,
                    request.expires_at,
                    observed,
                    observed,
                ),
            )
            row = cur.execute(
                """
                SELECT * FROM review_items
                WHERE review_kind = ? AND subject_type = ? AND subject_id = ?
                  AND subject_version = ? AND item_hash = ?
                """,
                (
                    request.review_kind.value,
                    request.subject_type,
                    request.subject_id,
                    request.subject_version,
                    request.item_hash,
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("review creation did not produce a row")
        return self._item(row)

    def get(self, review_id: str) -> dict[str, Any] | None:
        self.expire_due()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM review_items WHERE review_id = ?",
                (review_id,),
            ).fetchone()
        return self._item(row) if row is not None else None

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM review_decisions WHERE decision_id = ?",
                (str(decision_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def latest_decision(self, review_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM review_decisions WHERE review_id = ?
                ORDER BY decided_at DESC LIMIT 1
                """,
                (str(review_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_items(
        self,
        *,
        state: ReviewState | str | None = None,
        subject_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.expire_due()
        clauses: list[str] = []
        values: list[Any] = []
        if state is not None:
            clauses.append("state = ?")
            values.append(ReviewState(state).value)
        if subject_type:
            clauses.append("subject_type = ?")
            values.append(str(subject_type).strip().casefold())
        sql = "SELECT * FROM review_items"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        values.append(max(1, min(int(limit), 500)))
        with self._lock:
            rows = self._conn.execute(sql, values).fetchall()
        return [self._item(row) for row in rows]

    def decide(
        self,
        *,
        review_id: str,
        bound_item_hash: str,
        decision: ReviewDecisionKind | str,
        actor_principal: str,
        reason: str,
        idempotency_key: str,
        edited_value_ref: str | None = None,
    ) -> dict[str, Any]:
        observed = _now()
        decision_value = ReviewDecisionKind(decision).value
        target_state = (
            ReviewState.APPROVED.value
            if decision_value == ReviewDecisionKind.APPROVE.value
            else ReviewState.REJECTED.value
        )
        with self._transaction(immediate=True) as cur:
            existing = cur.execute(
                "SELECT * FROM review_decisions WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            row = cur.execute(
                "SELECT state, item_hash, expires_at FROM review_items WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            if row is None:
                raise KeyError(review_id)
            if row["expires_at"] is not None and str(row["expires_at"]) <= observed:
                cur.execute(
                    "UPDATE review_items SET state = ?, updated_at = ? WHERE review_id = ?",
                    (ReviewState.EXPIRED.value, observed, review_id),
                )
                raise ValueError("review_expired")
            if str(row["state"]) != ReviewState.PENDING.value:
                raise ValueError("review_not_pending")
            if str(row["item_hash"]) != str(bound_item_hash):
                raise ValueError("review_version_changed")
            decision_id = str(uuid4())
            cur.execute(
                """
                INSERT INTO review_decisions (
                    decision_id, review_id, decision, actor_principal, reason,
                    decided_at, bound_item_hash, edited_value_ref, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    review_id,
                    decision_value,
                    actor_principal,
                    reason,
                    observed,
                    bound_item_hash,
                    edited_value_ref,
                    idempotency_key,
                ),
            )
            cur.execute(
                "UPDATE review_items SET state = ?, updated_at = ? WHERE review_id = ? AND state = ?",
                (target_state, observed, review_id, ReviewState.PENDING.value),
            )
            if int(cur.rowcount or 0) != 1:
                raise ValueError("review_state_changed")
            decision_row = cur.execute(
                "SELECT * FROM review_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
        if decision_row is None:
            raise RuntimeError("review decision did not produce a row")
        return dict(decision_row)

    def supersede(self, *, review_id: str, replacement_review_id: str) -> bool:
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE review_items
                SET state = ?, superseded_by_review_id = ?, updated_at = ?
                WHERE review_id = ? AND state = ?
                """,
                (
                    ReviewState.SUPERSEDED.value,
                    replacement_review_id,
                    _now(),
                    review_id,
                    ReviewState.PENDING.value,
                ),
            )
            return int(cur.rowcount or 0) == 1

    def mark_applied(self, *, decision_id: str, action_receipt_ref: str | None = None) -> bool:
        observed = _now()
        with self._transaction(immediate=True) as cur:
            row = cur.execute(
                "SELECT review_id, decision FROM review_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            if row is None or str(row["decision"]) != ReviewDecisionKind.APPROVE.value:
                return False
            cur.execute(
                """
                UPDATE review_decisions
                SET applied_at = COALESCE(applied_at, ?), action_receipt_ref = COALESCE(?, action_receipt_ref)
                WHERE decision_id = ?
                """,
                (observed, action_receipt_ref, decision_id),
            )
            state = ReviewState.EXECUTED.value if action_receipt_ref else ReviewState.APPLIED.value
            cur.execute(
                "UPDATE review_items SET state = ?, updated_at = ? WHERE review_id = ?",
                (state, observed, row["review_id"]),
            )
            return True

    def close(self) -> None:
        if not self._owns_connection:
            return
        with self._lock:
            self._conn.close()
