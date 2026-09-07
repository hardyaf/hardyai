from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Callable, ContextManager, Mapping

from app.db.connection import open_sqlite_connection
from app.db.migrations import initialize_schema
from app.db.transaction import sqlite_transaction
from app.jobs.repository import DurableJobRepository
from app.tickets.types import TicketKind, TicketStatus, iso_utc, new_id, utc_now


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _json_load(value: Any, fallback: Any) -> Any:
    if not isinstance(value, str) or not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def content_hash(value: Any) -> str:
    if isinstance(value, str):
        payload = value
    else:
        payload = _json_dump(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TicketRepository:
    _ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        TicketStatus.CAPTURED.value: {
            TicketStatus.WAITING_CLARIFICATION.value,
            TicketStatus.EXECUTING.value,
            TicketStatus.CANCELLED.value,
            TicketStatus.UNVERIFIABLE.value,
            TicketStatus.RECONCILIATION_REQUIRED.value,
        },
        TicketStatus.WAITING_CLARIFICATION.value: {
            TicketStatus.WAITING_CLARIFICATION.value,
            TicketStatus.EXECUTING.value,
            TicketStatus.CANCELLED.value,
            TicketStatus.UNVERIFIABLE.value,
        },
        TicketStatus.EXECUTING.value: {
            TicketStatus.EXECUTING.value,
            TicketStatus.WAITING_CLARIFICATION.value,
            TicketStatus.VERIFICATION_PENDING.value,
            TicketStatus.UNVERIFIABLE.value,
            TicketStatus.RECONCILIATION_REQUIRED.value,
            TicketStatus.ESCALATED.value,
            TicketStatus.CANCELLED.value,
        },
        TicketStatus.VERIFICATION_PENDING.value: {
            TicketStatus.VERIFICATION_PENDING.value,
            TicketStatus.VERIFYING.value,
            TicketStatus.UNVERIFIABLE.value,
            TicketStatus.CANCELLED.value,
            TicketStatus.ESCALATED.value,
        },
        TicketStatus.VERIFYING.value: {
            TicketStatus.VERIFYING.value,
            TicketStatus.VERIFICATION_PENDING.value,
            TicketStatus.VERIFIED.value,
            TicketStatus.SUPERSEDED.value,
            TicketStatus.REMEDIATION_QUEUED.value,
            TicketStatus.UNVERIFIABLE.value,
            TicketStatus.RECONCILIATION_REQUIRED.value,
            TicketStatus.ESCALATED.value,
        },
        TicketStatus.RECONCILIATION_REQUIRED.value: {
            TicketStatus.EXECUTING.value,
            TicketStatus.VERIFICATION_PENDING.value,
            TicketStatus.RECONCILIATION_REQUIRED.value,
            TicketStatus.ESCALATED.value,
            TicketStatus.CANCELLED.value,
        },
    }
    _TERMINAL_REOPEN_TARGETS = {
        TicketStatus.VERIFICATION_PENDING.value,
        TicketStatus.VERIFIED.value,
        TicketStatus.CANCELLED.value,
        TicketStatus.ESCALATED.value,
    }

    def __init__(self, database_path: str) -> None:
        self._database_path, self._conn = open_sqlite_connection(database_path)
        self._lock = RLock()
        initialize_schema(self._conn)
        self._jobs = DurableJobRepository(connection=self._conn, lock=self._lock)

    @property
    def database_path(self) -> str:
        return str(self._database_path)

    @property
    def job_repository(self) -> DurableJobRepository:
        return self._jobs

    def _transaction(self, *, immediate: bool = False) -> ContextManager[sqlite3.Cursor]:
        return sqlite_transaction(
            conn=self._conn,
            lock=self._lock,
            immediate=immediate,
        )

    @staticmethod
    def _ticket_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _entry_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["structured_payload"] = _json_load(payload.pop("structured_payload_json"), {})
        return payload

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["payload"] = _json_load(payload.pop("payload_json"), {})
        return payload

    @staticmethod
    def _receipt_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = dict(row)
        for column, target, fallback in (
            ("expected_effect_json", "expected_effect", {}),
            ("resource_locator_json", "resource_locator", {}),
            ("execution_observation_json", "execution_observation", {}),
            ("result_json", "result", {}),
        ):
            payload[target] = _json_load(payload.pop(column), fallback)
        return payload

    @staticmethod
    def _expectation_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = dict(row)
        payload["resource_locator"] = _json_load(payload.pop("resource_locator_json"), {})
        payload["expected_state"] = _json_load(payload.pop("expected_state_json"), {})
        return payload

    @staticmethod
    def _review_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        for column, target, fallback in (
            ("source_evidence_json", "source_evidence", {}),
            ("discrepancy_json", "discrepancy", []),
            ("proposed_repair_json", "proposed_repair", None),
        ):
            payload[target] = _json_load(payload.pop(column), fallback)
        return payload

    def create_ticket(
        self,
        *,
        origin_request_id: str,
        session_id: str,
        user_id: str,
        agent_id: str,
        source: str,
        intent: str,
        skill_id: str | None,
        route: str,
        title: str,
        status: TicketStatus = TicketStatus.CAPTURED,
        resource_key: str | None = None,
        ticket_kind: TicketKind = TicketKind.ORIGINAL,
        parent_ticket_id: str | None = None,
        root_ticket_id: str | None = None,
        remediation_generation: int = 0,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        existing = self.get_ticket_by_request_id(origin_request_id)
        if existing is not None:
            return existing
        ticket_id = new_id()
        root_id = root_ticket_id or ticket_id
        now = created_at or iso_utc()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO work_tickets (
                    ticket_id, root_ticket_id, parent_ticket_id, ticket_kind,
                    remediation_generation, status, version, origin_request_id,
                    session_id, user_id, agent_id, source, intent, skill_id, route,
                    resource_key, title, created_at, last_material_activity_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket_id,
                    root_id,
                    parent_ticket_id,
                    ticket_kind.value,
                    max(0, int(remediation_generation)),
                    status.value,
                    origin_request_id,
                    session_id,
                    user_id,
                    agent_id,
                    source,
                    intent,
                    skill_id,
                    route,
                    resource_key,
                    title[:240],
                    now,
                    now,
                ),
            )
        return self.get_ticket_by_request_id(origin_request_id) or {}

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM work_tickets WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
        return self._ticket_row(row)

    def get_ticket_by_request_id(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM work_tickets WHERE origin_request_id = ?",
                (request_id,),
            ).fetchone()
        return self._ticket_row(row)

    def list_tickets(
        self,
        *,
        status: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if status:
            clauses.append("status = ?")
            values.append(status.strip().lower())
        if user_id:
            clauses.append("user_id = ?")
            values.append(user_id.strip())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 1000)))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM work_tickets {where} ORDER BY created_at DESC LIMIT ?",
                tuple(values),
            ).fetchall()
        return [dict(row) for row in rows]

    def transition_ticket(
        self,
        *,
        ticket_id: str,
        status: TicketStatus,
        expected_version: int | None = None,
        resource_key: str | None = None,
        completed_at: str | None = None,
        review_due_at: str | None = None,
        source_action_revision: str | None = None,
        expected_effect_hash: str | None = None,
        terminal_reason: str | None = None,
        material_activity_at: str | None = None,
    ) -> dict[str, Any] | None:
        fields = ["status = ?", "version = version + 1", "last_material_activity_at = ?"]
        values: list[Any] = [status.value, material_activity_at or iso_utc()]
        optional = {
            "resource_key": resource_key,
            "completed_at": completed_at,
            "review_due_at": review_due_at,
            "source_action_revision": source_action_revision,
            "expected_effect_hash": expected_effect_hash,
            "terminal_reason": terminal_reason,
        }
        for column, value in optional.items():
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        where = "ticket_id = ?"
        values.append(ticket_id)
        if expected_version is not None:
            where += " AND version = ?"
            values.append(int(expected_version))
        with self._transaction(immediate=True) as cur:
            current = cur.execute(
                "SELECT status FROM work_tickets WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
            if current is None:
                return None
            current_status = str(current["status"])
            allowed = self._ALLOWED_TRANSITIONS.get(current_status, self._TERMINAL_REOPEN_TARGETS)
            if status.value != current_status and status.value not in allowed:
                raise ValueError(f"Invalid ticket transition: {current_status} -> {status.value}")
            cur.execute(
                f"UPDATE work_tickets SET {', '.join(fields)} WHERE {where}",
                tuple(values),
            )
            if int(cur.rowcount or 0) != 1:
                return None
        return self.get_ticket(ticket_id)

    def append_entry(
        self,
        *,
        ticket_id: str,
        request_id: str,
        entry_type: str,
        actor_type: str,
        actor_id: str | None = None,
        verbatim_text: str | None = None,
        structured_payload: dict[str, Any] | list[Any] | None = None,
        dedupe_key: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        payload = structured_payload if structured_payload is not None else {}
        entry_hash = content_hash({"text": verbatim_text, "payload": payload})
        stable_dedupe = dedupe_key or content_hash(
            {
                "ticket_id": ticket_id,
                "request_id": request_id,
                "entry_type": entry_type,
                "hash": entry_hash,
            }
        )
        with self._transaction(immediate=True) as cur:
            return self._append_entry_cursor(
                cur,
                ticket_id=ticket_id,
                request_id=request_id,
                entry_type=entry_type,
                actor_type=actor_type,
                actor_id=actor_id,
                verbatim_text=verbatim_text,
                structured_payload=payload,
                dedupe_key=stable_dedupe,
                created_at=created_at,
                conflict_code=None,
            )

    def _append_entry_cursor(
        self,
        cursor: sqlite3.Cursor,
        *,
        ticket_id: str,
        request_id: str,
        entry_type: str,
        actor_type: str,
        actor_id: str | None,
        verbatim_text: str | None,
        structured_payload: dict[str, Any] | list[Any],
        dedupe_key: str,
        created_at: str | None,
        conflict_code: str | None,
    ) -> dict[str, Any]:
        payload_json = _json_dump(structured_payload)
        entry_hash = content_hash({"text": verbatim_text, "payload": structured_payload})
        existing = cursor.execute(
            "SELECT * FROM ticket_entries WHERE dedupe_key = ?",
            (dedupe_key,),
        ).fetchone()
        if existing is not None:
            if conflict_code and (
                str(existing["ticket_id"]) != ticket_id
                or str(existing["request_id"]) != request_id
                or str(existing["entry_type"]) != entry_type
                or str(existing["actor_type"]) != actor_type
                or (str(existing["actor_id"]) if existing["actor_id"] is not None else None)
                != actor_id
                or (
                    str(existing["verbatim_text"])
                    if existing["verbatim_text"] is not None
                    else None
                )
                != verbatim_text
                or str(existing["structured_payload_json"]) != payload_json
                or str(existing["content_hash"]) != entry_hash
            ):
                raise ValueError(conflict_code)
            return self._entry_row(existing)
        sequence = int(
            cursor.execute(
                "SELECT COALESCE(MAX(sequence_number), 0) + 1 FROM ticket_entries WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()[0]
        )
        entry_id = new_id()
        cursor.execute(
            """
            INSERT INTO ticket_entries (
                entry_id, ticket_id, sequence_number, request_id, entry_type,
                actor_type, actor_id, created_at, verbatim_text,
                structured_payload_json, content_hash, dedupe_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                ticket_id,
                sequence,
                request_id,
                entry_type,
                actor_type,
                actor_id,
                created_at or iso_utc(),
                verbatim_text,
                payload_json,
                entry_hash,
                dedupe_key,
            ),
        )
        row = cursor.execute(
            "SELECT * FROM ticket_entries WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("ticket entry insert did not produce a row")
        return self._entry_row(row)

    def append_entry_exact(
        self,
        *,
        ticket_id: str,
        request_id: str,
        entry_type: str,
        actor_type: str,
        actor_id: str | None = None,
        structured_payload: dict[str, Any] | list[Any] | None = None,
        dedupe_key: str,
        conflict_code: str,
    ) -> dict[str, Any]:
        """Append an immutable control entry or reject changed dedupe content."""

        payload = structured_payload if structured_payload is not None else {}
        with self._transaction(immediate=True) as cur:
            return self._append_entry_cursor(
                cur,
                ticket_id=ticket_id,
                request_id=request_id,
                entry_type=entry_type,
                actor_type=actor_type,
                actor_id=actor_id,
                verbatim_text=None,
                structured_payload=payload,
                dedupe_key=dedupe_key,
                created_at=None,
                conflict_code=conflict_code,
            )

    def get_entry_by_dedupe(self, dedupe_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM ticket_entries WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
        return self._entry_row(row) if row is not None else None

    def list_execution_manifests(self, ticket_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM ticket_entries
                WHERE ticket_id = ? AND entry_type = 'execution_manifest'
                ORDER BY sequence_number
                """,
                (ticket_id,),
            ).fetchall()
        return [self._entry_row(row) for row in rows]

    def reserve_execution_manifest_atomic(
        self,
        *,
        ticket_id: str,
        request_id: str,
        manifest: dict[str, Any],
        manifest_hash: str,
        domain_reservation: Callable[
            [sqlite3.Cursor, Mapping[str, Any], str, str | None], Mapping[str, Any]
        ]
        | None = None,
    ) -> dict[str, Any]:
        """Persist a redacted ticket manifest with an optional domain row in one transaction."""

        if content_hash(manifest) != manifest_hash:
            raise ValueError("tool_execution_manifest_hash_mismatch")
        parent_operation_id = str(manifest.get("parent_operation_id") or "")
        recovery_hash = manifest.get("recovery_manifest_hash")
        dedupe_key = f"tool-execution-manifest:v1:{parent_operation_id}"
        with self._transaction(immediate=True) as cur:
            ticket = cur.execute(
                "SELECT * FROM work_tickets WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
            if ticket is None:
                raise KeyError(ticket_id)
            projection = {
                key: ticket[key]
                for key in (
                    "ticket_id",
                    "origin_request_id",
                    "user_id",
                    "agent_id",
                    "source",
                    "intent",
                    "skill_id",
                    "route",
                )
            }
            entry = self._append_entry_cursor(
                cur,
                ticket_id=ticket_id,
                request_id=request_id,
                entry_type="execution_manifest",
                actor_type="jarvis",
                actor_id=str(ticket["agent_id"]),
                verbatim_text=None,
                structured_payload=manifest,
                dedupe_key=dedupe_key,
                created_at=None,
                conflict_code="tool_execution_manifest_conflict",
            )
            domain_result: Mapping[str, Any] | None = None
            if domain_reservation is not None:
                if not isinstance(recovery_hash, str) or not recovery_hash:
                    raise ValueError("domain_recovery_manifest_hash_required")
                domain_result = domain_reservation(
                    cur,
                    projection,
                    manifest_hash,
                    recovery_hash,
                )
                if not isinstance(domain_result, Mapping) or set(domain_result) != {
                    "status",
                    "recovery_manifest_hash",
                }:
                    raise ValueError("domain_manifest_reservation_result_invalid")
                if str(domain_result.get("status") or "") not in {"created", "existing"}:
                    raise ValueError("domain_manifest_reservation_status_invalid")
                if str(domain_result.get("recovery_manifest_hash") or "") != recovery_hash:
                    raise ValueError("domain_manifest_reservation_hash_conflict")
        return {
            "entry": entry,
            "manifest_hash": manifest_hash,
            "ticket_projection": projection,
            "domain_reservation": dict(domain_result) if domain_result is not None else None,
        }

    def list_entries(self, ticket_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM ticket_entries WHERE ticket_id = ? ORDER BY sequence_number",
                (ticket_id,),
            ).fetchall()
        return [self._entry_row(row) for row in rows]

    def record_operation_receipt(self, *, ticket_id: str, receipt: dict[str, Any]) -> dict[str, Any]:
        with self._transaction(immediate=True) as cur:
            return self._record_operation_receipt_cursor(
                cur,
                ticket_id=ticket_id,
                receipt=receipt,
            )

    def _record_operation_receipt_cursor(
        self,
        cursor: sqlite3.Cursor,
        *,
        ticket_id: str,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        values = (
            receipt["operation_id"],
            ticket_id,
            receipt["capability"],
            receipt["action"],
            receipt["idempotency_key"],
            receipt.get("provider_resource_id"),
            receipt.get("provider_revision"),
            receipt["resource_key"],
            receipt["status"],
            receipt.get("committed_at"),
            _json_dump(receipt.get("expected_effect") or {}),
            receipt["validator_name"],
            receipt["validator_version"],
            _json_dump(receipt.get("resource_locator") or {}),
            _json_dump(receipt.get("execution_observation") or {}),
            _json_dump(receipt.get("result") or {}),
        )
        existing = cursor.execute(
            """
            SELECT * FROM operation_receipts
            WHERE idempotency_key = ? OR operation_id = ?
            """,
            (receipt["idempotency_key"], receipt["operation_id"]),
        ).fetchall()
        if existing:
            expected = {
                "operation_id": values[0],
                "ticket_id": values[1],
                "capability": values[2],
                "action": values[3],
                "idempotency_key": values[4],
                "provider_resource_id": values[5],
                "provider_revision": values[6],
                "resource_key": values[7],
                "outcome": values[8],
                "committed_at": values[9],
                "expected_effect_json": values[10],
                "validator_name": values[11],
                "validator_version": values[12],
                "resource_locator_json": values[13],
                "execution_observation_json": values[14],
                "result_json": values[15],
            }
            if len(existing) != 1 or any(
                existing[0][key] != expected_value
                for key, expected_value in expected.items()
            ):
                raise ValueError("operation_receipt_idempotency_conflict")
            return self._receipt_row(existing[0]) or {}

        cursor.execute(
            """
            INSERT INTO operation_receipts (
                operation_id, ticket_id, capability, action, idempotency_key,
                provider_resource_id, provider_revision, resource_key, outcome,
                committed_at, expected_effect_json, validator_name, validator_version,
                resource_locator_json, execution_observation_json, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        row = cursor.execute(
            "SELECT * FROM operation_receipts WHERE idempotency_key = ?",
            (receipt["idempotency_key"],),
        ).fetchone()
        return self._receipt_row(row) or {}

    def get_latest_receipt(self, ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM operation_receipts WHERE ticket_id = ? ORDER BY committed_at DESC, rowid DESC LIMIT 1",
                (ticket_id,),
            ).fetchone()
        return self._receipt_row(row)

    def list_receipts(self, ticket_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM operation_receipts WHERE ticket_id = ? ORDER BY rowid",
                (ticket_id,),
            ).fetchall()
        return [value for row in rows if (value := self._receipt_row(row)) is not None]

    def create_expectation(
        self,
        *,
        ticket_id: str,
        operation_id: str,
        capability: str,
        validator_name: str,
        validator_version: str,
        resource_locator: dict[str, Any],
        expected_state: dict[str, Any],
        source_revision_at_execution: str | None,
    ) -> dict[str, Any]:
        with self._transaction(immediate=True) as cur:
            return self._create_expectation_cursor(
                cur,
                ticket_id=ticket_id,
                operation_id=operation_id,
                capability=capability,
                validator_name=validator_name,
                validator_version=validator_version,
                resource_locator=resource_locator,
                expected_state=expected_state,
                source_revision_at_execution=source_revision_at_execution,
            )

    def _create_expectation_cursor(
        self,
        cursor: sqlite3.Cursor,
        *,
        ticket_id: str,
        operation_id: str,
        capability: str,
        validator_name: str,
        validator_version: str,
        resource_locator: dict[str, Any],
        expected_state: dict[str, Any],
        source_revision_at_execution: str | None,
    ) -> dict[str, Any]:
        expected_hash = content_hash(expected_state)
        expected = {
            "ticket_id": ticket_id,
            "operation_id": operation_id,
            "capability": capability,
            "validator_name": validator_name,
            "validator_version": validator_version,
            "resource_locator_json": _json_dump(resource_locator),
            "expected_state_json": _json_dump(expected_state),
            "expected_state_hash": expected_hash,
            "source_revision_at_execution": source_revision_at_execution,
        }
        existing = cursor.execute(
            "SELECT * FROM ticket_expectations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if existing is not None:
            if any(existing[key] != value for key, value in expected.items()):
                raise ValueError("ticket_expectation_idempotency_conflict")
            return self._expectation_row(existing) or {}

        expectation_id = new_id()
        cursor.execute(
            """
            INSERT INTO ticket_expectations (
                expectation_id, ticket_id, operation_id, capability,
                validator_name, validator_version, resource_locator_json,
                expected_state_json, expected_state_hash,
                source_revision_at_execution, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                expectation_id,
                ticket_id,
                operation_id,
                capability,
                validator_name,
                validator_version,
                _json_dump(resource_locator),
                _json_dump(expected_state),
                expected_hash,
                source_revision_at_execution,
                iso_utc(),
            ),
        )
        row = cursor.execute(
            "SELECT * FROM ticket_expectations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        return self._expectation_row(row) or {}

    def record_child_outcome_atomic(
        self,
        *,
        ticket_id: str,
        request_id: str,
        outcome: dict[str, Any],
        receipt: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Atomically persist a strict child outcome and its optional effect receipt."""

        child_operation_id = str(outcome["child_operation_id"])
        with self._transaction(immediate=True) as cur:
            recorded_receipt: dict[str, Any] | None = None
            expectation: dict[str, Any] | None = None
            if receipt is not None:
                recorded_receipt = self._record_operation_receipt_cursor(
                    cur,
                    ticket_id=ticket_id,
                    receipt=receipt,
                )
                expectation = self._create_expectation_cursor(
                    cur,
                    ticket_id=ticket_id,
                    operation_id=child_operation_id,
                    capability=str(recorded_receipt["capability"]),
                    validator_name=str(recorded_receipt["validator_name"]),
                    validator_version=str(recorded_receipt["validator_version"]),
                    resource_locator=dict(recorded_receipt.get("resource_locator") or {}),
                    expected_state=dict(recorded_receipt.get("expected_effect") or {}),
                    source_revision_at_execution=(
                        str(recorded_receipt["provider_revision"])
                        if recorded_receipt.get("provider_revision") is not None
                        else None
                    ),
                )
                self._append_entry_cursor(
                    cur,
                    ticket_id=ticket_id,
                    request_id=request_id,
                    entry_type="operation_receipt",
                    actor_type="domain",
                    actor_id=None,
                    verbatim_text=None,
                    structured_payload=recorded_receipt,
                    dedupe_key=f"ticket-effect-receipt:v1:{child_operation_id}",
                    created_at=None,
                    conflict_code="operation_receipt_entry_conflict",
                )
            entry = self._append_entry_cursor(
                cur,
                ticket_id=ticket_id,
                request_id=request_id,
                entry_type="child_outcome",
                actor_type="domain",
                actor_id=None,
                verbatim_text=None,
                structured_payload=outcome,
                dedupe_key=f"ticket-child-outcome:v1:{child_operation_id}",
                created_at=None,
                conflict_code="child_outcome_idempotency_conflict",
            )
            watchdog = self.enqueue_job(
                job_type="ticket_watchdog",
                aggregate_id=ticket_id,
                idempotency_key=f"ticket-child-watchdog:v1:{child_operation_id}",
                payload={
                    "ticket_id": ticket_id,
                    "child_operation_id": child_operation_id,
                },
                max_attempts=1,
                cursor=cur,
            )
        return {
            "entry": entry,
            "receipt": recorded_receipt,
            "expectation": expectation,
            "watchdog": watchdog,
        }

    def record_operation_completion_atomic(
        self,
        *,
        ticket_id: str,
        request_id: str,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist one legacy receipt, expectation, and reference as one unit."""

        with self._transaction(immediate=True) as cur:
            recorded = self._record_operation_receipt_cursor(
                cur,
                ticket_id=ticket_id,
                receipt=receipt,
            )
            expectation = self._create_expectation_cursor(
                cur,
                ticket_id=ticket_id,
                operation_id=str(recorded["operation_id"]),
                capability=str(recorded["capability"]),
                validator_name=str(recorded["validator_name"]),
                validator_version=str(recorded["validator_version"]),
                resource_locator=dict(recorded.get("resource_locator") or {}),
                expected_state=dict(recorded.get("expected_effect") or {}),
                source_revision_at_execution=(
                    str(recorded["provider_revision"])
                    if recorded.get("provider_revision") is not None
                    else None
                ),
            )
            entry = self._append_entry_cursor(
                cur,
                ticket_id=ticket_id,
                request_id=request_id,
                entry_type="operation_receipt",
                actor_type="domain",
                actor_id=None,
                verbatim_text=None,
                structured_payload=recorded,
                dedupe_key=f"ticket:{ticket_id}:operation:{recorded['operation_id']}",
                created_at=None,
                conflict_code="operation_receipt_entry_conflict",
            )
        return {
            "receipt": recorded,
            "expectation": expectation,
            "entry": entry,
        }

    def get_latest_expectation(self, ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM ticket_expectations WHERE ticket_id = ? ORDER BY created_at DESC LIMIT 1",
                (ticket_id,),
            ).fetchone()
        return self._expectation_row(row)

    def list_expectations(self, ticket_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM ticket_expectations WHERE ticket_id = ? ORDER BY created_at, rowid",
                (ticket_id,),
            ).fetchall()
        return [value for row in rows if (value := self._expectation_row(row)) is not None]

    def enqueue_job(
        self,
        *,
        job_type: str,
        aggregate_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        available_at: str | None = None,
        max_attempts: int = 3,
        cursor: sqlite3.Cursor | None = None,
    ) -> dict[str, Any]:
        return self._jobs.enqueue_job(
            job_type=job_type,
            aggregate_id=aggregate_id,
            idempotency_key=idempotency_key,
            payload=payload,
            available_at=available_at,
            max_attempts=max_attempts,
            cursor=cursor,
        )

    def schedule_verification(
        self,
        *,
        ticket_id: str,
        source_action_revision: str,
        delay_seconds: float,
        max_attempts: int,
        completed_at: datetime | None = None,
    ) -> dict[str, Any]:
        completed = completed_at or utc_now()
        due = completed + timedelta(seconds=max(0.0, float(delay_seconds)))
        completed_text = iso_utc(completed)
        due_text = iso_utc(due)
        job_key = f"ticket-review:{ticket_id}:{source_action_revision}"
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE work_tickets
                SET status = ?, version = version + 1,
                    last_material_activity_at = ?, completed_at = ?, review_due_at = ?,
                    source_action_revision = ?
                WHERE ticket_id = ?
                """,
                (
                    TicketStatus.VERIFICATION_PENDING.value,
                    completed_text,
                    completed_text,
                    due_text,
                    source_action_revision,
                    ticket_id,
                ),
            )
            job = self.enqueue_job(
                job_type="ticket_review",
                aggregate_id=ticket_id,
                idempotency_key=job_key,
                payload={"ticket_id": ticket_id, "source_action_revision": source_action_revision},
                available_at=due_text,
                max_attempts=max_attempts,
                cursor=cur,
            )
        return job

    def claim_jobs(
        self,
        *,
        job_type: str,
        worker_id: str,
        limit: int,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        return self._jobs.claim_jobs(
            job_type=job_type,
            worker_id=worker_id,
            limit=limit,
            lease_seconds=lease_seconds,
            now=now,
        )

    def complete_job(self, *, job_id: str, worker_id: str) -> bool:
        return self._jobs.complete_job(job_id=job_id, worker_id=worker_id)

    def retry_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        delay_seconds: float,
    ) -> bool:
        return self._jobs.retry_job(
            job_id=job_id,
            worker_id=worker_id,
            error_code=error_code,
            delay_seconds=delay_seconds,
        )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self._jobs.get_job(job_id)

    def list_jobs(self, *, job_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self._jobs.list_jobs(job_type=job_type, limit=limit)

    def operations_summary(self) -> dict[str, Any]:
        with self._lock:
            job_rows = self._conn.execute(
                "SELECT job_type, status, COUNT(*) AS count FROM durable_jobs GROUP BY job_type, status"
            ).fetchall()
            ticket_rows = self._conn.execute(
                "SELECT status, COUNT(*) AS count FROM work_tickets GROUP BY status"
            ).fetchall()
            heartbeat_rows = self._conn.execute(
                "SELECT * FROM worker_heartbeats ORDER BY worker_type, last_seen_at DESC"
            ).fetchall()
        return {
            "tickets": {str(row["status"]): int(row["count"]) for row in ticket_rows},
            "jobs": {
                f"{row['job_type']}:{row['status']}": int(row["count"])
                for row in job_rows
            },
            "workers": [
                {
                    **dict(row),
                    "metadata": _json_load(row["metadata_json"], {}),
                }
                for row in heartbeat_rows
            ],
        }

    def has_recent_live_input(self, *, within_seconds: float) -> bool:
        if within_seconds <= 0:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT timestamp FROM events WHERE event_type = 'input.received' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return False
        try:
            observed = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=UTC)
        except ValueError:
            return False
        return (utc_now() - observed.astimezone(UTC)).total_seconds() < within_seconds

    def record_worker_heartbeat(
        self,
        *,
        worker_type: str,
        worker_id: str,
        status: str,
        last_error_code: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._jobs.record_worker_heartbeat(
            worker_type=worker_type,
            worker_id=worker_id,
            status=status,
            last_error_code=last_error_code,
            metadata=metadata,
        )

    def start_review_run(
        self,
        *,
        ticket_id: str,
        source_action_revision: str,
        attempt_number: int,
        prompt_version: str,
    ) -> dict[str, Any]:
        run_id = new_id()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO ticket_review_runs (
                    review_run_id, ticket_id, source_action_revision, attempt_number,
                    status, prompt_version, started_at
                ) VALUES (?, ?, ?, ?, 'running', ?, ?)
                """,
                (run_id, ticket_id, source_action_revision, int(attempt_number), prompt_version, iso_utc()),
            )
            row = cur.execute(
                """
                SELECT * FROM ticket_review_runs
                WHERE ticket_id = ? AND source_action_revision = ? AND attempt_number = ?
                """,
                (ticket_id, source_action_revision, int(attempt_number)),
            ).fetchone()
        return self._review_row(row)

    def complete_review_run(
        self,
        *,
        review_run_id: str,
        status: str,
        deterministic_verdict: str | None,
        model_verdict: str | None,
        model_name: str | None,
        context_pack_hash: str | None,
        source_evidence: dict[str, Any],
        source_evidence_hash: str | None,
        discrepancy: list[dict[str, Any]],
        proposed_repair: dict[str, Any] | None,
        error_code: str | None = None,
    ) -> dict[str, Any] | None:
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE ticket_review_runs
                SET status = ?, deterministic_verdict = ?, model_verdict = ?,
                    model_name = ?, context_pack_hash = ?, source_evidence_json = ?,
                    source_evidence_hash = ?, discrepancy_json = ?,
                    proposed_repair_json = ?, completed_at = ?, error_code = ?
                WHERE review_run_id = ?
                """,
                (
                    status,
                    deterministic_verdict,
                    model_verdict,
                    model_name,
                    context_pack_hash,
                    _json_dump(source_evidence),
                    source_evidence_hash,
                    _json_dump(discrepancy),
                    _json_dump(proposed_repair) if proposed_repair is not None else None,
                    iso_utc(),
                    error_code,
                    review_run_id,
                ),
            )
            row = cur.execute(
                "SELECT * FROM ticket_review_runs WHERE review_run_id = ?",
                (review_run_id,),
            ).fetchone()
        return self._review_row(row) if row is not None else None

    def list_review_runs(self, ticket_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM ticket_review_runs WHERE ticket_id = ? ORDER BY started_at",
                (ticket_id,),
            ).fetchall()
        return [self._review_row(row) for row in rows]

    def find_later_tickets(self, *, resource_key: str, completed_after: str, exclude_ticket_id: str) -> list[dict[str, Any]]:
        if not resource_key:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM work_tickets
                WHERE resource_key = ? AND ticket_id != ? AND completed_at > ?
                  AND status IN (?, ?, ?, ?)
                ORDER BY completed_at
                """,
                (
                    resource_key,
                    exclude_ticket_id,
                    completed_after,
                    TicketStatus.VERIFICATION_PENDING.value,
                    TicketStatus.VERIFYING.value,
                    TicketStatus.VERIFIED.value,
                    TicketStatus.SUPERSEDED.value,
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_lineage(self, ticket_id: str) -> list[dict[str, Any]]:
        ticket = self.get_ticket(ticket_id)
        if ticket is None:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM work_tickets WHERE root_ticket_id = ? ORDER BY remediation_generation, created_at",
                (ticket["root_ticket_id"],),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_plane_mapping(
        self,
        *,
        ticket_id: str,
        plane_work_item_id: str | None,
        sync_status: str,
    ) -> dict[str, Any] | None:
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE work_tickets
                SET plane_work_item_id = COALESCE(?, plane_work_item_id),
                    plane_sync_status = ?, version = version + 1
                WHERE ticket_id = ?
                """,
                (plane_work_item_id, sync_status, ticket_id),
            )
        return self.get_ticket(ticket_id)

    def upsert_identity_binding(
        self,
        *,
        source: str,
        external_user_id: str,
        user_id: str,
        agent_id: str,
        external_display_name: str | None = None,
        age_band: str | None = None,
        presentation_profile: str = "default",
        policy_profile: str = "adult",
        active: bool = True,
    ) -> dict[str, Any]:
        now = iso_utc()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                INSERT INTO external_identity_bindings (
                    source, external_user_id, external_display_name, user_id, agent_id,
                    age_band, presentation_profile, policy_profile, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, external_user_id) DO UPDATE SET
                    external_display_name = excluded.external_display_name,
                    user_id = excluded.user_id,
                    agent_id = excluded.agent_id,
                    age_band = excluded.age_band,
                    presentation_profile = excluded.presentation_profile,
                    policy_profile = excluded.policy_profile,
                    active = excluded.active,
                    updated_at = excluded.updated_at
                """,
                (
                    source.strip().lower(),
                    external_user_id.strip(),
                    external_display_name.strip() if external_display_name else None,
                    user_id.strip(),
                    agent_id.strip().lower(),
                    age_band.strip().lower() if age_band else None,
                    presentation_profile.strip().lower() or "default",
                    policy_profile.strip().lower() or "adult",
                    1 if active else 0,
                    now,
                    now,
                ),
            )
            row = cur.execute(
                "SELECT * FROM external_identity_bindings WHERE source = ? AND external_user_id = ?",
                (source.strip().lower(), external_user_id.strip()),
            ).fetchone()
        payload = dict(row)
        payload["active"] = bool(payload["active"])
        return payload

    def get_identity_binding(self, *, source: str, external_user_id: str, active_only: bool = True) -> dict[str, Any] | None:
        sql = "SELECT * FROM external_identity_bindings WHERE source = ? AND external_user_id = ?"
        values: list[Any] = [source.strip().lower(), external_user_id.strip()]
        if active_only:
            sql += " AND active = 1"
        with self._lock:
            row = self._conn.execute(sql, tuple(values)).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["active"] = bool(payload["active"])
        return payload

    def list_identity_bindings(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM external_identity_bindings"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY source, external_user_id"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["active"] = bool(payload["active"])
            result.append(payload)
        return result

    def clear_ticket_data(self) -> None:
        with self._transaction(immediate=True) as cur:
            for table in (
                "worker_heartbeats",
                "ticket_review_runs",
                "ticket_expectations",
                "operation_receipts",
                "ticket_entries",
                "durable_jobs",
                "work_tickets",
                "external_identity_bindings",
            ):
                cur.execute(f"DELETE FROM {table}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
