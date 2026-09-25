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
from app.jobs.repository import DurableJobRepository
from app.jobs.types import (
    REVIEW_ACTION_EXECUTION_JOB,
    REVIEW_NOTIFICATION_DISCORD_JOB,
    REVIEW_OUTCOME_DISCORD_JOB,
)
from app.reviews.types import (
    ActionApprovalProposalRequest,
    ActionProposalState,
    ReviewDecisionKind,
    ReviewKind,
    ReviewRequest,
    ReviewState,
)


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
        self._jobs = DurableJobRepository(connection=self._conn, lock=self._lock)

    def _transaction(self, *, immediate: bool = False) -> ContextManager[sqlite3.Cursor]:
        return sqlite_transaction(conn=self._conn, lock=self._lock, immediate=immediate)

    @property
    def job_repository(self) -> DurableJobRepository:
        return self._jobs

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

    @staticmethod
    def _proposal(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for source, target in (
            ("destination_arguments_json", "destination_arguments"),
            ("batch_manifest_json", "batch_manifest"),
            ("transfer_manifest_json", "transfer_manifest"),
        ):
            raw = value.pop(source)
            if raw is None:
                value[target] = None
                continue
            try:
                value[target] = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                value[target] = None
        return value

    @staticmethod
    def _proposal_insert_values(
        *,
        proposal_id: str,
        review_id: str,
        request: ActionApprovalProposalRequest,
        observed: str,
    ) -> tuple[Any, ...]:
        return (
            proposal_id,
            review_id,
            request.idempotency_key,
            request.proposal_hash,
            request.root_request_id,
            request.operation_id,
            request.call_ordinal,
            request.session_id,
            request.principal_kind,
            request.principal_subject,
            request.external_user_id,
            request.requester_user_id,
            request.agent_id,
            request.source_interface,
            request.channel_scope,
            request.skill_id,
            request.tool_id,
            request.contract_version,
            request.descriptor_hash,
            request.resource_version,
            request.authorization_binding,
            request.arguments_hash,
            _json(request.destination_arguments),
            request.destination_arguments_hash,
            request.effect,
            request.effect_cardinality,
            request.sensitivity,
            request.persistence,
            request.destination_purpose,
            request.approver_principal,
            request.safe_action_summary,
            request.risk_summary,
            _json(request.batch_manifest) if request.batch_manifest is not None else None,
            request.batch_manifest_hash,
            _json(request.transfer_manifest) if request.transfer_manifest is not None else None,
            request.transfer_binding_hash,
            ActionProposalState.PENDING.value,
            request.expires_at,
            observed,
            observed,
        )

    def expire_due(self, *, now: str | None = None) -> int:
        observed = now or _now()
        with self._transaction(immediate=True) as cur:
            expired_proposals = cur.execute(
                """
                SELECT proposal_id, review_id FROM action_proposals
                WHERE state = ? AND expires_at <= ?
                """,
                (ActionProposalState.PENDING.value, observed),
            ).fetchall()
            if expired_proposals:
                proposal_ids = [str(row["proposal_id"]) for row in expired_proposals]
                cur.executemany(
                    """
                    UPDATE action_proposals
                    SET state=?, destination_arguments_json=NULL,
                        terminal_reason_code='approval_expired', terminal_at=?, updated_at=?
                    WHERE proposal_id=? AND state=?
                    """,
                    [
                        (
                            ActionProposalState.EXPIRED.value,
                            observed,
                            observed,
                            proposal_id,
                            ActionProposalState.PENDING.value,
                        )
                        for proposal_id in proposal_ids
                    ],
                )
                cur.executemany(
                    """
                    UPDATE durable_jobs
                    SET status='cancelled', cancel_requested_at=COALESCE(cancel_requested_at, ?),
                        cancelled_at=COALESCE(cancelled_at, ?), lease_owner=NULL,
                        lease_expires_at=NULL, updated_at=?
                    WHERE aggregate_id=? AND job_type=? AND status IN ('pending', 'retry')
                    """,
                    [
                        (
                            observed,
                            observed,
                            observed,
                            proposal_id,
                            REVIEW_NOTIFICATION_DISCORD_JOB,
                        )
                        for proposal_id in proposal_ids
                    ],
                )
            expired_approved = cur.execute(
                """
                SELECT p.proposal_id, p.execution_job_id
                FROM action_proposals AS p
                WHERE p.state=? AND p.expires_at<=?
                """,
                (ActionProposalState.APPROVED.value, observed),
            ).fetchall()
            if expired_approved:
                cur.executemany(
                    """
                    UPDATE action_proposals
                    SET state=?, destination_arguments_json=NULL,
                        terminal_reason_code='approval_expired', terminal_at=?, updated_at=?
                    WHERE proposal_id=? AND state=?
                    """,
                    [
                        (
                            ActionProposalState.EXPIRED.value,
                            observed,
                            observed,
                            str(row["proposal_id"]),
                            ActionProposalState.APPROVED.value,
                        )
                        for row in expired_approved
                    ],
                )
                job_ids = [str(row["execution_job_id"] or "") for row in expired_approved]
                cur.executemany(
                    """
                    UPDATE durable_jobs
                    SET status='cancelled', cancel_requested_at=COALESCE(cancel_requested_at, ?),
                        cancelled_at=COALESCE(cancelled_at, ?), lease_owner=NULL,
                        lease_expires_at=NULL, updated_at=?
                    WHERE job_id=? AND status IN ('pending', 'retry')
                    """,
                    [
                        (observed, observed, observed, job_id)
                        for job_id in job_ids
                        if job_id
                    ],
                )
            cur.execute(
                """
                UPDATE review_items
                SET state = ?, updated_at = ?
                WHERE state = ? AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (ReviewState.EXPIRED.value, observed, ReviewState.PENDING.value, observed),
            )
            return len(expired_proposals) + len(expired_approved) + int(cur.rowcount or 0)

    def create_action_proposal(
        self,
        request: ActionApprovalProposalRequest,
    ) -> dict[str, Any]:
        observed = _now()
        with self._transaction(immediate=True) as cur:
            existing = cur.execute(
                "SELECT * FROM action_proposals WHERE idempotency_key=?",
                (request.idempotency_key,),
            ).fetchone()
            operation_row = cur.execute(
                "SELECT idempotency_key, proposal_hash FROM action_proposals WHERE operation_id=?",
                (request.operation_id,),
            ).fetchone()
            if operation_row is not None and str(operation_row["idempotency_key"]) != request.idempotency_key:
                raise ValueError("action_proposal_operation_conflict")

            if existing is None:
                proposal_id = str(uuid4())
                review_id = str(uuid4())
                cur.execute(
                    """
                    INSERT INTO review_items (
                        review_id, review_kind, subject_type, subject_id, subject_version,
                        item_hash, source_ref, sensitivity, confidence,
                        validator_summary_json, evidence_refs_json, target_operation,
                        authorization_binding, state, expires_at, created_at, updated_at
                    ) VALUES (?, ?, 'action_proposal', ?, ?, ?, NULL, ?, NULL,
                              '[]', '[]', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_id,
                        ReviewKind.DOWNSTREAM_ACTION.value,
                        proposal_id,
                        request.resource_version,
                        request.proposal_hash,
                        request.sensitivity,
                        request.tool_id,
                        request.authorization_binding,
                        ReviewState.PENDING.value,
                        request.expires_at,
                        observed,
                        observed,
                    ),
                )
                try:
                    cur.execute(
                        """
                        INSERT INTO action_proposals (
                            proposal_id, review_id, idempotency_key, proposal_hash,
                            root_request_id, operation_id, call_ordinal, session_id,
                            principal_kind, principal_subject, external_user_id,
                            requester_user_id, agent_id, source_interface, channel_scope,
                            skill_id, tool_id, contract_version, descriptor_hash,
                            resource_version, authorization_binding, arguments_hash,
                            destination_arguments_json, destination_arguments_hash,
                            effect, effect_cardinality, sensitivity, persistence,
                            destination_purpose, approver_principal, safe_action_summary,
                            risk_summary, batch_manifest_json, batch_manifest_hash,
                            transfer_manifest_json, transfer_binding_hash, state,
                            expires_at, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        self._proposal_insert_values(
                            proposal_id=proposal_id,
                            review_id=review_id,
                            request=request,
                            observed=observed,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError("action_proposal_idempotency_conflict") from exc
                proposal_row = cur.execute(
                    "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)
                ).fetchone()
            else:
                if str(existing["proposal_hash"]) != request.proposal_hash:
                    raise ValueError("action_proposal_idempotency_conflict")
                proposal_row = existing
                proposal_id = str(existing["proposal_id"])
                review_id = str(existing["review_id"])

            if proposal_row is None:
                raise RuntimeError("action proposal creation did not produce a row")
            review_row = cur.execute(
                "SELECT * FROM review_items WHERE review_id=?", (review_id,)
            ).fetchone()
            if review_row is None or str(review_row["item_hash"]) != request.proposal_hash:
                raise ValueError("action_proposal_review_conflict")
            notification_job = None
            if request.destination_purpose == "human_reviews":
                notification_payload = {
                    "proposal_id": proposal_id,
                    "review_id": review_id,
                    "operation_id": request.operation_id,
                    "authorization_binding": request.authorization_binding,
                    "batch_manifest_hash": request.batch_manifest_hash,
                    "transfer_binding_hash": request.transfer_binding_hash,
                    "destination_purpose": request.destination_purpose,
                }
                notification_job = self._jobs.enqueue_job(
                    job_type=REVIEW_NOTIFICATION_DISCORD_JOB,
                    aggregate_id=proposal_id,
                    idempotency_key=(
                        "review-notification-discord:v1:"
                        f"{proposal_id}:{review_id}:{request.destination_purpose}"
                    ),
                    payload=notification_payload,
                    max_attempts=5,
                    cursor=cur,
                )
        return {
            "proposal": self._proposal(proposal_row),
            "review": self._item(review_row),
            "notification_job": notification_job,
        }

    def get_action_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        self.expire_due()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM action_proposals WHERE proposal_id=?",
                (str(proposal_id),),
            ).fetchone()
        return self._proposal(row) if row is not None else None

    def action_proposal_for_review(self, review_id: str) -> dict[str, Any] | None:
        self.expire_due()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM action_proposals WHERE review_id=?",
                (str(review_id),),
            ).fetchone()
        return self._proposal(row) if row is not None else None

    def list_action_proposals(
        self,
        *,
        state: ActionProposalState | str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.expire_due()
        values: list[Any] = []
        sql = "SELECT * FROM action_proposals"
        if state is not None:
            sql += " WHERE state=?"
            values.append(ActionProposalState(state).value)
        sql += " ORDER BY created_at DESC LIMIT ?"
        values.append(max(1, min(int(limit), 500)))
        with self._lock:
            rows = self._conn.execute(sql, values).fetchall()
        return [self._proposal(row) for row in rows]

    def mark_action_notification_delivered(
        self,
        *,
        proposal_id: str,
        destination_purpose: str,
        guild_id: str,
        channel_id: str,
        message_id: str,
    ) -> dict[str, Any]:
        with self._transaction(immediate=True) as cur:
            row = cur.execute(
                "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            if str(row["destination_purpose"]) != destination_purpose:
                raise ValueError("action_notification_destination_mismatch")
            existing = (
                row["notification_guild_id"],
                row["notification_channel_id"],
                row["notification_message_id"],
            )
            expected = (guild_id, channel_id, message_id)
            if any(item is not None for item in existing):
                if tuple(str(item or "") for item in existing) != expected:
                    raise ValueError("action_notification_delivery_conflict")
                return self._proposal(row)
            if str(row["state"]) != ActionProposalState.PENDING.value:
                raise ValueError("action_notification_not_pending")
            observed = _now()
            cur.execute(
                """
                UPDATE action_proposals
                SET notification_guild_id=?, notification_channel_id=?,
                    notification_message_id=?, updated_at=?
                WHERE proposal_id=? AND state=?
                  AND notification_message_id IS NULL
                """,
                (
                    guild_id,
                    channel_id,
                    message_id,
                    observed,
                    proposal_id,
                    ActionProposalState.PENDING.value,
                ),
            )
            if int(cur.rowcount or 0) != 1:
                raise ValueError("action_notification_state_changed")
            updated = cur.execute(
                "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        if updated is None:
            raise RuntimeError("action notification binding was lost")
        return self._proposal(updated)

    def decide_action_proposal(
        self,
        *,
        proposal_id: str,
        review_id: str,
        bound_proposal_hash: str,
        decision: ReviewDecisionKind | str,
        actor_principal: str,
        destination_purpose: str,
        guild_id: str,
        channel_id: str,
        message_id: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        observed = _now()
        self.expire_due(now=observed)
        decision_value = ReviewDecisionKind(decision).value
        with self._transaction(immediate=True) as cur:
            proposal = cur.execute(
                "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
            if proposal is None or str(proposal["review_id"]) != review_id:
                raise KeyError(proposal_id)
            existing = cur.execute(
                "SELECT * FROM review_decisions WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["review_id"]) != review_id
                    or str(existing["decision"]) != decision_value
                    or str(existing["actor_principal"]) != actor_principal
                    or str(existing["reason"]) != reason
                    or str(existing["bound_item_hash"]) != bound_proposal_hash
                    or str(proposal["decision_message_id"] or "") != message_id
                    or str(proposal["decision_guild_id"] or "") != guild_id
                    or str(proposal["decision_channel_id"] or "") != channel_id
                ):
                    raise ValueError("action_decision_idempotency_conflict")
                execution_job = None
                if proposal["execution_job_id"] is not None:
                    execution_job = self._jobs.get_job(str(proposal["execution_job_id"]))
                return {
                    "decision": dict(existing),
                    "proposal": self._proposal(proposal),
                    "execution_job": execution_job,
                }
            if str(proposal["state"]) == ActionProposalState.EXPIRED.value:
                raise ValueError("action_proposal_expired")
            if str(proposal["state"]) != ActionProposalState.PENDING.value:
                raise ValueError("action_proposal_not_pending")
            review = cur.execute(
                "SELECT state, item_hash, expires_at FROM review_items WHERE review_id=?",
                (review_id,),
            ).fetchone()
            if review is None or str(review["state"]) != ReviewState.PENDING.value:
                raise ValueError("action_review_not_pending")
            if (
                str(proposal["proposal_hash"]) != bound_proposal_hash
                or str(review["item_hash"]) != bound_proposal_hash
            ):
                raise ValueError("action_proposal_version_changed")
            if str(proposal["destination_purpose"]) != destination_purpose:
                raise ValueError("action_decision_destination_mismatch")
            if str(proposal["approver_principal"]) != actor_principal:
                raise PermissionError("action_decision_actor_denied")
            if destination_purpose != "task_workspace" and (
                str(proposal["notification_guild_id"] or "") != guild_id
                or str(proposal["notification_channel_id"] or "") != channel_id
                or not str(proposal["notification_message_id"] or "")
            ):
                raise PermissionError("action_decision_channel_denied")

            decision_id = str(uuid4())
            cur.execute(
                """
                INSERT INTO review_decisions (
                    decision_id, review_id, decision, actor_principal, reason,
                    decided_at, bound_item_hash, edited_value_ref, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    decision_id,
                    review_id,
                    decision_value,
                    actor_principal,
                    reason,
                    observed,
                    bound_proposal_hash,
                    idempotency_key,
                ),
            )
            target_review_state = (
                ReviewState.APPROVED.value
                if decision_value == ReviewDecisionKind.APPROVE.value
                else ReviewState.REJECTED.value
            )
            cur.execute(
                "UPDATE review_items SET state=?, updated_at=? WHERE review_id=? AND state=?",
                (target_review_state, observed, review_id, ReviewState.PENDING.value),
            )
            if int(cur.rowcount or 0) != 1:
                raise ValueError("action_review_state_changed")

            execution_job: dict[str, Any] | None = None
            target_proposal_state = (
                ActionProposalState.APPROVED.value
                if decision_value == ReviewDecisionKind.APPROVE.value
                else ActionProposalState.REJECTED.value
            )
            if decision_value == ReviewDecisionKind.APPROVE.value:
                execution_payload = {
                    "proposal_id": proposal_id,
                    "review_id": review_id,
                    "operation_id": str(proposal["operation_id"]),
                    "authorization_binding": str(proposal["authorization_binding"]),
                    "batch_manifest_hash": proposal["batch_manifest_hash"],
                    "transfer_binding_hash": proposal["transfer_binding_hash"],
                }
                execution_job = self._jobs.enqueue_job(
                    job_type=REVIEW_ACTION_EXECUTION_JOB,
                    aggregate_id=proposal_id,
                    idempotency_key=(
                        f"review-action-execution:v1:{proposal_id}:{proposal['operation_id']}"
                    ),
                    payload=execution_payload,
                    max_attempts=3,
                    cursor=cur,
                )
            cur.execute(
                """
                UPDATE action_proposals
                SET state=?, decision_id=?, decided_by_principal=?,
                    decision_guild_id=?, decision_channel_id=?, decision_message_id=?,
                    execution_job_id=?,
                    destination_arguments_json=CASE WHEN ?='rejected' THEN NULL
                                                    ELSE destination_arguments_json END,
                    terminal_reason_code=CASE WHEN ?='rejected' THEN 'human_rejected'
                                              ELSE NULL END,
                    terminal_at=CASE WHEN ?='rejected' THEN ? ELSE NULL END,
                    updated_at=?
                WHERE proposal_id=? AND state=?
                """,
                (
                    target_proposal_state,
                    decision_id,
                    actor_principal,
                    guild_id,
                    channel_id,
                    message_id,
                    execution_job["job_id"] if execution_job is not None else None,
                    target_proposal_state,
                    target_proposal_state,
                    target_proposal_state,
                    observed,
                    observed,
                    proposal_id,
                    ActionProposalState.PENDING.value,
                ),
            )
            if int(cur.rowcount or 0) != 1:
                raise ValueError("action_proposal_state_changed")
            decision_row = cur.execute(
                "SELECT * FROM review_decisions WHERE decision_id=?", (decision_id,)
            ).fetchone()
            proposal_row = cur.execute(
                "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        if decision_row is None or proposal_row is None:
            raise RuntimeError("action decision persistence was lost")
        return {
            "decision": dict(decision_row),
            "proposal": self._proposal(proposal_row),
            "execution_job": execution_job,
        }

    def begin_action_execution(
        self,
        *,
        proposal_id: str,
        job_id: str,
        worker_id: str,
        fencing_token: int,
        now: str | None = None,
    ) -> dict[str, Any]:
        observed = now or _now()
        self.expire_due(now=observed)
        with self._transaction(immediate=True) as cur:
            proposal = cur.execute(
                "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
            if proposal is None:
                raise KeyError(proposal_id)
            if str(proposal["state"]) == ActionProposalState.EXECUTED.value:
                return self._proposal(proposal)
            if str(proposal["state"]) == ActionProposalState.EXPIRED.value:
                raise ValueError("action_proposal_expired")
            if str(proposal["state"]) != ActionProposalState.APPROVED.value:
                raise ValueError("action_proposal_not_approved")
            job = cur.execute(
                """
                SELECT status, lease_owner, lease_fencing_token
                FROM durable_jobs WHERE job_id=? AND aggregate_id=? AND job_type=?
                """,
                (job_id, proposal_id, REVIEW_ACTION_EXECUTION_JOB),
            ).fetchone()
            if (
                job is None
                or str(job["status"]) != "running"
                or str(job["lease_owner"] or "") != worker_id
                or int(job["lease_fencing_token"] or 0) != int(fencing_token)
                or str(proposal["execution_job_id"] or "") != job_id
            ):
                raise ValueError("action_execution_lease_mismatch")
            cur.execute(
                """
                UPDATE action_proposals
                SET state=?, execution_fencing_token=?, updated_at=?
                WHERE proposal_id=? AND state=?
                """,
                (
                    ActionProposalState.EXECUTING.value,
                    int(fencing_token),
                    observed,
                    proposal_id,
                    ActionProposalState.APPROVED.value,
                ),
            )
            if int(cur.rowcount or 0) != 1:
                raise ValueError("action_proposal_state_changed")
            row = cur.execute(
                "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("action execution claim was lost")
        return self._proposal(row)

    def finish_action_execution(
        self,
        *,
        proposal_id: str,
        job_id: str,
        worker_id: str,
        fencing_token: int,
        outcome: ActionProposalState | str,
        reason_code: str,
        receipt_ref: str | None = None,
    ) -> dict[str, Any]:
        target = ActionProposalState(outcome)
        if target not in {
            ActionProposalState.EXECUTED,
            ActionProposalState.DENIED,
            ActionProposalState.FAILED_TERMINAL,
        }:
            raise ValueError("action_execution_outcome_invalid")
        receipt = str(receipt_ref or "").strip() or None
        if target is ActionProposalState.EXECUTED and receipt is None:
            raise ValueError("action_execution_receipt_required")
        observed = _now()
        with self._transaction(immediate=True) as cur:
            proposal = cur.execute(
                "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
            if proposal is None:
                raise KeyError(proposal_id)
            if str(proposal["state"]) == target.value:
                if (
                    str(proposal["action_receipt_ref"] or "") != str(receipt or "")
                    or str(proposal["terminal_reason_code"] or "") != str(reason_code)
                ):
                    raise ValueError("action_execution_terminal_conflict")
                return self._proposal(proposal)
            if (
                str(proposal["state"]) != ActionProposalState.EXECUTING.value
                or str(proposal["execution_job_id"] or "") != job_id
                or int(proposal["execution_fencing_token"] or 0) != int(fencing_token)
            ):
                raise ValueError("action_execution_state_mismatch")
            job = cur.execute(
                """
                SELECT status, lease_owner, lease_fencing_token
                FROM durable_jobs WHERE job_id=? AND job_type=?
                """,
                (job_id, REVIEW_ACTION_EXECUTION_JOB),
            ).fetchone()
            if (
                job is None
                or str(job["status"]) != "running"
                or str(job["lease_owner"] or "") != worker_id
                or int(job["lease_fencing_token"] or 0) != int(fencing_token)
            ):
                raise ValueError("action_execution_lease_mismatch")
            cur.execute(
                """
                UPDATE action_proposals
                SET state=?, destination_arguments_json=NULL, action_receipt_ref=?,
                    terminal_reason_code=?, terminal_at=?, updated_at=?
                WHERE proposal_id=? AND state=? AND execution_fencing_token=?
                """,
                (
                    target.value,
                    receipt,
                    str(reason_code or "action_execution_terminal")[:120],
                    observed,
                    observed,
                    proposal_id,
                    ActionProposalState.EXECUTING.value,
                    int(fencing_token),
                ),
            )
            if int(cur.rowcount or 0) != 1:
                raise ValueError("action_execution_state_changed")
            if target is ActionProposalState.EXECUTED:
                cur.execute(
                    "UPDATE review_items SET state=?, updated_at=? WHERE review_id=?",
                    (ReviewState.EXECUTED.value, observed, proposal["review_id"]),
                )
                if proposal["decision_id"] is not None:
                    cur.execute(
                        """
                        UPDATE review_decisions
                        SET applied_at=COALESCE(applied_at, ?),
                            action_receipt_ref=COALESCE(action_receipt_ref, ?)
                        WHERE decision_id=?
                        """,
                        (observed, receipt, proposal["decision_id"]),
                    )
            if str(proposal["destination_purpose"]) == "human_reviews":
                self._jobs.enqueue_job(
                    job_type=REVIEW_OUTCOME_DISCORD_JOB,
                    aggregate_id=proposal_id,
                    idempotency_key=f"review-outcome-discord:v1:{proposal_id}:{target.value}",
                    payload={
                        "proposal_id": proposal_id,
                        "review_id": str(proposal["review_id"]),
                        "operation_id": str(proposal["operation_id"]),
                        "authorization_binding": str(proposal["authorization_binding"]),
                        "state": target.value,
                        "destination_purpose": str(proposal["destination_purpose"]),
                    },
                    max_attempts=5,
                    priority=40,
                    cursor=cur,
                )
            row = cur.execute(
                "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("action execution outcome was lost")
        return self._proposal(row)

    def mark_action_outcome_delivered(
        self,
        *,
        proposal_id: str,
        destination_purpose: str,
        guild_id: str,
        channel_id: str,
        message_id: str,
    ) -> dict[str, Any]:
        observed = _now()
        with self._transaction(immediate=True) as cur:
            row = cur.execute(
                "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            if row["outcome_message_id"] is not None:
                if (
                    str(row["outcome_guild_id"] or "") != str(guild_id)
                    or str(row["outcome_channel_id"] or "") != str(channel_id)
                    or str(row["outcome_message_id"] or "") != str(message_id)
                ):
                    raise ValueError("action_outcome_delivery_conflict")
                return self._proposal(row)
            if str(row["state"]) not in {
                ActionProposalState.EXECUTED.value,
                ActionProposalState.DENIED.value,
                ActionProposalState.FAILED_TERMINAL.value,
            }:
                raise ValueError("action_outcome_not_terminal")
            if str(row["destination_purpose"]) != str(destination_purpose):
                raise ValueError("action_outcome_destination_mismatch")
            cur.execute(
                """
                UPDATE action_proposals
                SET outcome_guild_id=?, outcome_channel_id=?,
                    outcome_message_id=?, updated_at=?
                WHERE proposal_id=? AND outcome_message_id IS NULL
                """,
                (guild_id, channel_id, message_id, observed, proposal_id),
            )
            if int(cur.rowcount or 0) != 1:
                raise ValueError("action_outcome_delivery_race")
            row = cur.execute(
                "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("action outcome delivery binding was lost")
        return self._proposal(row)

    def retry_action_execution_after_no_effect(
        self,
        *,
        proposal_id: str,
        job_id: str,
        worker_id: str,
        fencing_token: int,
        reconciliation: str,
    ) -> dict[str, Any]:
        if str(reconciliation).strip().casefold() != "no_effect":
            raise ValueError("action_execution_reconciliation_unproven")
        observed = _now()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE action_proposals
                SET state=?, execution_fencing_token=NULL, updated_at=?
                WHERE proposal_id=? AND state=? AND execution_job_id=?
                  AND execution_fencing_token=?
                  AND EXISTS (
                      SELECT 1 FROM durable_jobs
                      WHERE job_id=? AND status='running' AND lease_owner=?
                        AND lease_fencing_token=?
                  )
                """,
                (
                    ActionProposalState.APPROVED.value,
                    observed,
                    proposal_id,
                    ActionProposalState.EXECUTING.value,
                    job_id,
                    int(fencing_token),
                    job_id,
                    worker_id,
                    int(fencing_token),
                ),
            )
            if int(cur.rowcount or 0) != 1:
                raise ValueError("action_execution_recovery_mismatch")
            row = cur.execute(
                "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("action execution recovery was lost")
        return self._proposal(row)

    def cancel_approved_action(self, *, proposal_id: str, reason_code: str) -> dict[str, Any]:
        observed = _now()
        with self._transaction(immediate=True) as cur:
            proposal = cur.execute(
                "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
            if proposal is None:
                raise KeyError(proposal_id)
            if str(proposal["state"]) == ActionProposalState.CANCELED.value:
                return self._proposal(proposal)
            if str(proposal["state"]) != ActionProposalState.APPROVED.value:
                raise ValueError("action_proposal_not_cancelable")
            job_id = str(proposal["execution_job_id"] or "")
            job = cur.execute(
                "SELECT status FROM durable_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if job is None or str(job["status"]) not in {"pending", "retry", "cancelled"}:
                raise ValueError("action_execution_claim_or_effect_possible")
            cur.execute(
                """
                UPDATE durable_jobs
                SET status='cancelled', cancel_requested_at=COALESCE(cancel_requested_at, ?),
                    cancelled_at=COALESCE(cancelled_at, ?), lease_owner=NULL,
                    lease_expires_at=NULL, updated_at=?
                WHERE job_id=? AND status IN ('pending', 'retry')
                """,
                (observed, observed, observed, job_id),
            )
            cur.execute(
                """
                UPDATE action_proposals
                SET state=?, destination_arguments_json=NULL, terminal_reason_code=?,
                    terminal_at=?, updated_at=?
                WHERE proposal_id=? AND state=?
                """,
                (
                    ActionProposalState.CANCELED.value,
                    str(reason_code or "operator_canceled")[:120],
                    observed,
                    observed,
                    proposal_id,
                    ActionProposalState.APPROVED.value,
                ),
            )
            if int(cur.rowcount or 0) != 1:
                raise ValueError("action_proposal_state_changed")
            row = cur.execute(
                "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("action cancellation was lost")
        return self._proposal(row)

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
        self.expire_due(now=observed)
        decision_value = ReviewDecisionKind(decision).value
        target_state = (
            ReviewState.APPROVED.value
            if decision_value == ReviewDecisionKind.APPROVE.value
            else ReviewState.REJECTED.value
        )
        with self._transaction(immediate=True) as cur:
            action_link = cur.execute(
                "SELECT 1 FROM action_proposals WHERE review_id=?", (review_id,)
            ).fetchone()
            if action_link is not None:
                raise ValueError("action_review_requires_bound_decision")
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
            if str(row["state"]) == ReviewState.EXPIRED.value:
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
            action = cur.execute(
                "SELECT proposal_id, state FROM action_proposals WHERE review_id=?",
                (review_id,),
            ).fetchone()
            if action is not None:
                if str(action["state"]) != ActionProposalState.PENDING.value:
                    return False
                observed = _now()
                cur.execute(
                    """
                    UPDATE action_proposals
                    SET state=?, destination_arguments_json=NULL,
                        terminal_reason_code='approval_superseded', terminal_at=?, updated_at=?
                    WHERE proposal_id=? AND state=?
                    """,
                    (
                        ActionProposalState.SUPERSEDED.value,
                        observed,
                        observed,
                        action["proposal_id"],
                        ActionProposalState.PENDING.value,
                    ),
                )
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
            if cur.execute(
                "SELECT 1 FROM action_proposals WHERE review_id=?", (row["review_id"],)
            ).fetchone() is not None:
                raise ValueError("action_review_requires_fenced_execution")
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
