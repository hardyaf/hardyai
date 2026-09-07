from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from app.db.connection import open_readonly_sqlite_connection, open_sqlite_connection
from app.db.domain_schema import ensure_email_agent_schema
from app.db.migrations import initialize_schema
from app.db.migrations import LATEST_SCHEMA_VERSION
from app.skills.domains.email_agent.query import EmailQuery


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def inspect_email_operations_worker_database(
    database_path: str,
    *,
    now: str,
) -> tuple[dict[str, Any], set[str]]:
    """Read the worker schema/row-kind/lease gate without migrations or writes."""

    _, connection = open_readonly_sqlite_connection(database_path)
    try:
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        mailbox_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(email_mailbox_operations)").fetchall()
        }
        parent_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(email_tool_operations)").fetchall()
        }
        required_mailbox_columns = {
            "parent_operation_id",
            "parent_manifest_hash",
            "child_index",
            "arguments_hash",
        }
        required_parent_columns = {
            "idempotency_key",
            "operation_identity_hash",
            "parent_manifest_hash",
            "expected_child_count",
            "recovery_manifest_json",
            "recovery_manifest_hash",
        }
        schema_ready = (
            schema_version == LATEST_SCHEMA_VERSION
            and required_mailbox_columns <= mailbox_columns
            and required_parent_columns <= parent_columns
        )
        if not schema_ready:
            return {
                "schema_version": schema_version,
                "schema_ready": False,
                "supported_row_kinds": False,
                "unsupported_row_count": 0,
                "active_lease_owner_count": 0,
                "single_worker_ownership": False,
            }, set()
        unsupported_managed = int(
            connection.execute(
                "SELECT COUNT(*) FROM email_managed_label_operations "
                "WHERE action NOT IN ('apply','remove')"
            ).fetchone()[0]
        )
        unsupported_mailbox = int(
            connection.execute(
                "SELECT COUNT(*) FROM email_mailbox_operations "
                "WHERE parent_operation_id IS NOT NULL AND operation_type <> 'move_to_spam'"
            ).fetchone()[0]
        )
        active_owners = int(
            connection.execute(
                """
                SELECT COUNT(DISTINCT lease_owner) FROM (
                    SELECT lease_owner FROM email_managed_label_operations
                    WHERE status='claimed' AND lease_owner IS NOT NULL
                      AND lease_expires_at IS NOT NULL AND lease_expires_at>?
                    UNION ALL
                    SELECT lease_owner FROM email_mailbox_operations
                    WHERE parent_operation_id IS NOT NULL AND status='claimed'
                      AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL
                      AND lease_expires_at>?
                )
                """,
                (str(now), str(now)),
            ).fetchone()[0]
        )
        label_refs = {
            str(row[0])
            for row in connection.execute(
                "SELECT label_ref FROM email_managed_labels WHERE enabled=1"
            ).fetchall()
        }
        return {
            "schema_version": schema_version,
            "schema_ready": True,
            "supported_row_kinds": unsupported_managed == 0 and unsupported_mailbox == 0,
            "unsupported_row_count": unsupported_managed + unsupported_mailbox,
            "active_lease_owner_count": active_owners,
            "single_worker_ownership": active_owners <= 1,
        }, label_refs
    finally:
        connection.close()


class EmailAgentSQLiteStorage:
    """Domain-owned email metadata store. Raw message bodies are never persisted."""

    def __init__(self, database_path: str) -> None:
        _, self._conn = open_sqlite_connection(database_path)
        self._lock = RLock()
        with self._lock:
            initialize_schema(self._conn)
            ensure_email_agent_schema(self._conn)


    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def operations_worker_readiness(self, *, now: str) -> dict[str, Any]:
        """Inspect the exact P5F/P8D worker rows without claiming or mutating them."""

        required_mailbox_columns = {
            "parent_operation_id",
            "parent_manifest_hash",
            "child_index",
            "arguments_hash",
        }
        required_parent_columns = {
            "idempotency_key",
            "operation_identity_hash",
            "parent_manifest_hash",
            "expected_child_count",
            "recovery_manifest_json",
            "recovery_manifest_hash",
        }
        with self._lock:
            schema_version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            mailbox_columns = {
                str(row[1])
                for row in self._conn.execute(
                    "PRAGMA table_info(email_mailbox_operations)"
                ).fetchall()
            }
            parent_columns = {
                str(row[1])
                for row in self._conn.execute("PRAGMA table_info(email_tool_operations)").fetchall()
            }
            unsupported_managed = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM email_managed_label_operations "
                    "WHERE action NOT IN ('apply','remove')"
                ).fetchone()[0]
            )
            unsupported_mailbox = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM email_mailbox_operations "
                    "WHERE parent_operation_id IS NOT NULL AND operation_type <> 'move_to_spam'"
                ).fetchone()[0]
            )
            active_owners = int(
                self._conn.execute(
                    """
                    SELECT COUNT(DISTINCT lease_owner) FROM (
                        SELECT lease_owner FROM email_managed_label_operations
                        WHERE status='claimed' AND lease_owner IS NOT NULL
                          AND lease_expires_at IS NOT NULL AND lease_expires_at>?
                        UNION ALL
                        SELECT lease_owner FROM email_mailbox_operations
                        WHERE parent_operation_id IS NOT NULL AND status='claimed'
                          AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL
                          AND lease_expires_at>?
                    )
                    """,
                    (str(now), str(now)),
                ).fetchone()[0]
            )
        schema_ready = (
            schema_version == LATEST_SCHEMA_VERSION
            and required_mailbox_columns <= mailbox_columns
            and required_parent_columns <= parent_columns
        )
        return {
            "schema_version": schema_version,
            "schema_ready": schema_ready,
            "supported_row_kinds": unsupported_managed == 0 and unsupported_mailbox == 0,
            "unsupported_row_count": unsupported_managed + unsupported_mailbox,
            "active_lease_owner_count": active_owners,
            "single_worker_ownership": active_owners <= 1,
        }

    def get_sync_state(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM email_sync_state WHERE state_key = 'primary'"
            ).fetchone()
        return dict(row) if row is not None else None

    def activate(self, *, now: str, history_id: str) -> dict[str, Any]:
        cursor = str(history_id or "").strip()
        if not cursor:
            raise ValueError("A Gmail history ID is required for activation.")
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO email_sync_state(
                    state_key, activation_at, history_id, updated_at
                ) VALUES ('primary', ?, ?, ?)
                """,
                (now, cursor, now),
            )
            self._conn.commit()
        return self.get_sync_state() or {}

    def claim_sync_run(
        self,
        *,
        bucket_key: str,
        run_kind: str,
        lease_owner: str,
        now: str,
        lease_expires_at: str,
        stale_before: str,
        max_attempts: int,
    ) -> dict[str, Any]:
        if run_kind not in {"scheduled", "on_demand", "recovery"}:
            raise ValueError("Unsupported email sync run kind.")
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            row = cur.execute(
                "SELECT * FROM email_sync_runs WHERE bucket_key = ?",
                (bucket_key,),
            ).fetchone()
            if row is None:
                run_id = str(uuid4())
                cur.execute(
                    """
                    INSERT INTO email_sync_runs(
                        run_id, bucket_key, run_kind, status, attempt_count,
                        lease_owner, lease_expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, 'running', 1, ?, ?, ?, ?)
                    """,
                    (run_id, bucket_key, run_kind, lease_owner, lease_expires_at, now, now),
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
                        "UPDATE email_sync_runs SET status='dead_letter', updated_at=? WHERE run_id=?",
                        (now, existing["run_id"]),
                    )
                self._conn.commit()
                return {"claimed": False, "reason": "max_attempts", **existing}
            if status == "running" and str(existing.get("lease_expires_at") or "") > now:
                self._conn.commit()
                return {"claimed": False, "reason": "leased", **existing}
            if status == "running" and str(existing.get("updated_at") or "") > stale_before:
                self._conn.commit()
                return {"claimed": False, "reason": "leased", **existing}
            attempts += 1
            cur.execute(
                """
                UPDATE email_sync_runs
                SET status='running', attempt_count=?, lease_owner=?, lease_expires_at=?,
                    last_error_code=NULL, updated_at=?
                WHERE run_id=?
                """,
                (attempts, lease_owner, lease_expires_at, now, existing["run_id"]),
            )
            self._conn.commit()
            return {"claimed": True, "run_id": existing["run_id"], "attempt_count": attempts}

    def update_run_counts(self, *, run_id: str, counts: dict[str, int], now: str) -> None:
        values = self._bounded_counts(counts)
        with self._lock:
            self._conn.execute(
                """
                UPDATE email_sync_runs SET
                    page_count=?, candidate_count=?, accepted_count=?, ignored_count=?,
                    failed_count=?, summary_count=?, classification_count=?, updated_at=?
                WHERE run_id=?
                """,
                (*values, now, run_id),
            )
            self._conn.commit()

    def complete_sync_run(
        self,
        *,
        run_id: str,
        counts: dict[str, int],
        now: str,
        history_id: str,
        continuation_token: str | None,
        recovered: bool = False,
    ) -> None:
        values = self._bounded_counts(counts)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                """
                UPDATE email_sync_runs SET
                    status='completed', page_count=?, candidate_count=?, accepted_count=?,
                    ignored_count=?, failed_count=?, summary_count=?, classification_count=?,
                    lease_owner=NULL, lease_expires_at=NULL, last_error_code=NULL,
                    updated_at=?, completed_at=?
                WHERE run_id=?
                """,
                (*values, now, now, run_id),
            )
            cur.execute(
                """
                UPDATE email_sync_state SET history_id=?, continuation_token=?,
                    last_success_at=?, last_recovery_at=CASE WHEN ? THEN ? ELSE last_recovery_at END,
                    updated_at=? WHERE state_key='primary'
                """,
                (history_id, continuation_token, now, int(bool(recovered)), now, now),
            )
            self._conn.commit()

    def fail_sync_run(
        self,
        *,
        run_id: str,
        error_code: str,
        now: str,
        max_attempts: int,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT attempt_count FROM email_sync_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            attempts = int(row["attempt_count"] if row is not None else 0)
            status = "dead_letter" if attempts >= max(1, int(max_attempts)) else "failed"
            self._conn.execute(
                """
                UPDATE email_sync_runs SET status=?, lease_owner=NULL, lease_expires_at=NULL,
                    last_error_code=?, updated_at=? WHERE run_id=?
                """,
                (status, str(error_code or "unknown")[:120], now, run_id),
            )
            self._conn.commit()
        return {"status": status, "attempt_count": attempts}

    @staticmethod
    def _bounded_counts(counts: dict[str, int]) -> tuple[int, ...]:
        return tuple(
            max(0, int(counts.get(key) or 0))
            for key in (
                "page_count",
                "candidate_count",
                "accepted_count",
                "ignored_count",
                "failed_count",
                "summary_count",
                "classification_count",
            )
        )

    def upsert_message(self, *, record: dict[str, Any], now: str) -> dict[str, Any]:
        message_id = str(record.get("gmail_message_id") or "").strip()
        if not message_id:
            raise ValueError("gmail_message_id is required.")
        with self._lock:
            existing = self._conn.execute(
                "SELECT canonical_body_hash FROM email_messages WHERE gmail_message_id=?",
                (message_id,),
            ).fetchone()
            previous_hash = str(existing["canonical_body_hash"] or "") if existing is not None else None
            current_hash = str(record.get("canonical_body_hash") or "")
            changed = existing is None or previous_hash != current_hash
            self._conn.execute(
                """
                INSERT INTO email_messages(
                    gmail_message_id, gmail_thread_id, rfc_message_id, source_route_key,
                    gmail_history_id, internal_date, sender_name, sender_email,
                    recipient_headers_json, subject, snippet, gmail_label_ids_json,
                    attachment_metadata_json, canonical_body_hash, list_id,
                    first_seen_at, last_seen_at, content_changed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gmail_message_id) DO UPDATE SET
                    gmail_thread_id=excluded.gmail_thread_id,
                    rfc_message_id=excluded.rfc_message_id,
                    source_route_key=excluded.source_route_key,
                    gmail_history_id=excluded.gmail_history_id,
                    internal_date=excluded.internal_date,
                    sender_name=excluded.sender_name,
                    sender_email=excluded.sender_email,
                    recipient_headers_json=excluded.recipient_headers_json,
                    subject=excluded.subject,
                    snippet=excluded.snippet,
                    gmail_label_ids_json=excluded.gmail_label_ids_json,
                    attachment_metadata_json=excluded.attachment_metadata_json,
                    canonical_body_hash=excluded.canonical_body_hash,
                    list_id=excluded.list_id,
                    last_seen_at=excluded.last_seen_at,
                    content_changed_at=CASE
                        WHEN email_messages.canonical_body_hash <> excluded.canonical_body_hash
                        THEN excluded.last_seen_at ELSE email_messages.content_changed_at END
                """,
                (
                    message_id,
                    str(record.get("gmail_thread_id") or ""),
                    record.get("rfc_message_id"),
                    str(record.get("source_route_key") or ""),
                    str(record.get("gmail_history_id") or ""),
                    max(0, int(record.get("internal_date") or 0)),
                    record.get("sender_name"),
                    record.get("sender_email"),
                    str(record.get("recipient_headers_json") or "[]"),
                    str(record.get("subject") or "(no subject)"),
                    str(record.get("snippet") or ""),
                    str(record.get("gmail_label_ids_json") or "[]"),
                    str(record.get("attachment_metadata_json") or "[]"),
                    current_hash,
                    record.get("list_id"),
                    now,
                    now,
                    now if changed else None,
                ),
            )
            self._rebuild_thread_locked(str(record.get("gmail_thread_id") or ""), now=now)
            self._conn.commit()
        return {"created": existing is None, "content_changed": changed}

    def record_message_failure(
        self,
        *,
        gmail_message_id: str,
        error_code: str,
        now: str,
        max_attempts: int,
    ) -> dict[str, Any]:
        message_id = str(gmail_message_id or "").strip()
        if not message_id:
            raise ValueError("gmail_message_id is required.")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO email_sync_message_failures(
                    gmail_message_id, attempt_count, status, last_error_code,
                    first_failed_at, updated_at
                ) VALUES (?, 1, 'failed', ?, ?, ?)
                ON CONFLICT(gmail_message_id) DO UPDATE SET
                    attempt_count=email_sync_message_failures.attempt_count + 1,
                    last_error_code=excluded.last_error_code,
                    updated_at=excluded.updated_at
                """,
                (message_id, str(error_code or "unknown")[:120], now, now),
            )
            row = self._conn.execute(
                "SELECT * FROM email_sync_message_failures WHERE gmail_message_id=?",
                (message_id,),
            ).fetchone()
            attempts = int(row["attempt_count"] if row is not None else 1)
            status = "dead_letter" if attempts >= max(1, int(max_attempts)) else "failed"
            self._conn.execute(
                "UPDATE email_sync_message_failures SET status=? WHERE gmail_message_id=?",
                (status, message_id),
            )
            self._conn.commit()
        return {"status": status, "attempt_count": attempts}

    def clear_message_failure(self, *, gmail_message_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM email_sync_message_failures WHERE gmail_message_id=?",
                (gmail_message_id,),
            )
            self._conn.commit()

    def _rebuild_thread_locked(self, thread_id: str, *, now: str) -> None:
        if not thread_id:
            return
        rows = self._conn.execute(
            """
            SELECT gmail_message_id, internal_date, sender_email, subject, canonical_body_hash,
                   first_seen_at, last_seen_at
            FROM email_messages WHERE gmail_thread_id=? ORDER BY internal_date ASC, gmail_message_id ASC
            """,
            (thread_id,),
        ).fetchall()
        if not rows:
            return
        latest = rows[-1]
        participants = sorted(
            {str(row["sender_email"] or "").strip() for row in rows if str(row["sender_email"] or "").strip()}
        )[:50]
        subject_normalized = " ".join(str(latest["subject"] or "").casefold().split())[:998]
        import hashlib

        thread_hash = hashlib.sha256(
            "\n".join(str(row["canonical_body_hash"] or "") for row in rows).encode("ascii", errors="ignore")
        ).hexdigest()
        self._conn.execute(
            """
            INSERT INTO email_threads(
                gmail_thread_id, latest_message_id, latest_internal_date, message_count,
                participant_summary_json, subject_normalized, thread_content_hash,
                first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(gmail_thread_id) DO UPDATE SET
                latest_message_id=excluded.latest_message_id,
                latest_internal_date=excluded.latest_internal_date,
                message_count=excluded.message_count,
                participant_summary_json=excluded.participant_summary_json,
                subject_normalized=excluded.subject_normalized,
                thread_content_hash=excluded.thread_content_hash,
                last_seen_at=excluded.last_seen_at
            """,
            (
                thread_id,
                latest["gmail_message_id"],
                int(latest["internal_date"] or 0),
                len(rows),
                json.dumps(participants, sort_keys=True),
                subject_normalized,
                thread_hash,
                min(str(row["first_seen_at"] or now) for row in rows),
                max(str(row["last_seen_at"] or now) for row in rows),
            ),
        )

    def store_summary(
        self,
        *,
        scope_type: str,
        scope_id: str,
        source_hash: str,
        summary_text: str,
        structured_summary: dict[str, Any],
        model_provider: str,
        model_name: str,
        prompt_version: str,
        taxonomy_version: str,
        now: str,
    ) -> dict[str, Any]:
        summary_id = str(uuid4())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO email_summaries(
                    summary_id, scope_type, scope_id, source_hash, summary_text,
                    structured_summary_json, model_provider, model_name, prompt_version,
                    taxonomy_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_type, scope_id, source_hash, prompt_version) DO UPDATE SET
                    summary_text=excluded.summary_text,
                    structured_summary_json=excluded.structured_summary_json,
                    model_provider=excluded.model_provider,
                    model_name=excluded.model_name,
                    taxonomy_version=excluded.taxonomy_version,
                    created_at=excluded.created_at
                """,
                (
                    summary_id,
                    scope_type,
                    scope_id,
                    source_hash,
                    str(summary_text or "")[:8000],
                    json.dumps(structured_summary, sort_keys=True),
                    model_provider,
                    model_name,
                    prompt_version,
                    taxonomy_version,
                    now,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                """
                SELECT * FROM email_summaries
                WHERE scope_type=? AND scope_id=? AND source_hash=? AND prompt_version=?
                """,
                (scope_type, scope_id, source_hash, prompt_version),
            ).fetchone()
        return self._decode_row(dict(row)) if row is not None else {}

    def store_classification(
        self,
        *,
        gmail_message_id: str,
        taxonomy_version: str,
        logical_category_key: str,
        confidence: float,
        decision_source: str,
        evidence: dict[str, Any],
        review_required: bool,
        corrected_by_user_id: str | None,
        now: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO email_classifications(
                    classification_id, gmail_message_id, taxonomy_version,
                    logical_category_key, audience, confidence, decision_source,
                    evidence_json, review_required, corrected_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'shared', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gmail_message_id, taxonomy_version) DO UPDATE SET
                    logical_category_key=CASE
                        WHEN email_classifications.decision_source='correction'
                             AND excluded.decision_source<>'correction'
                        THEN email_classifications.logical_category_key
                        ELSE excluded.logical_category_key END,
                    confidence=CASE
                        WHEN email_classifications.decision_source='correction'
                             AND excluded.decision_source<>'correction'
                        THEN email_classifications.confidence ELSE excluded.confidence END,
                    decision_source=CASE
                        WHEN email_classifications.decision_source='correction'
                             AND excluded.decision_source<>'correction'
                        THEN email_classifications.decision_source ELSE excluded.decision_source END,
                    evidence_json=CASE
                        WHEN email_classifications.decision_source='correction'
                             AND excluded.decision_source<>'correction'
                        THEN email_classifications.evidence_json ELSE excluded.evidence_json END,
                    review_required=CASE
                        WHEN email_classifications.decision_source='correction'
                             AND excluded.decision_source<>'correction'
                        THEN email_classifications.review_required ELSE excluded.review_required END,
                    corrected_by_user_id=COALESCE(excluded.corrected_by_user_id,
                                                  email_classifications.corrected_by_user_id),
                    updated_at=excluded.updated_at
                """,
                (
                    str(uuid4()),
                    gmail_message_id,
                    taxonomy_version,
                    logical_category_key,
                    max(0.0, min(float(confidence), 1.0)),
                    decision_source,
                    json.dumps(evidence, sort_keys=True),
                    int(bool(review_required)),
                    corrected_by_user_id,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                """
                SELECT * FROM email_classifications
                WHERE gmail_message_id=? AND taxonomy_version=?
                """,
                (gmail_message_id, taxonomy_version),
            ).fetchone()
        return self._decode_row(dict(row)) if row is not None else {}

    def list_messages(
        self,
        *,
        taxonomy_version: str,
        limit: int,
        since_internal_date: int | None = None,
        source_route_key: str | None = None,
        category_key: str | None = None,
        query_text: str | None = None,
        user_id: str | None = None,
        discord_channel_id: str | None = None,
        visibility: str = "active",
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        scope_user = str(user_id or "").strip().casefold()
        scope_channel = str(discord_channel_id or "").strip()
        params: list[Any] = [taxonomy_version, scope_user, scope_channel]
        visibility_key = str(visibility or "active").strip().casefold()
        if visibility_key not in {"active", "unseen", "needs_reply", "completed", "spam", "all"}:
            raise ValueError("Unsupported email visibility filter.")
        if visibility_key == "active":
            clauses.append(
                "(us.gmail_message_id IS NULL OR us.disposition='needs_reply' "
                "OR us.review_state IN ('new','presented') "
                "OR (us.review_state='snoozed' AND us.snoozed_until IS NOT NULL AND us.snoozed_until<=?))"
            )
            params.append(str(now or _utc_iso()))
        elif visibility_key == "unseen":
            clauses.append("us.gmail_message_id IS NULL")
        elif visibility_key == "needs_reply":
            clauses.append("us.disposition='needs_reply'")
        elif visibility_key == "completed":
            clauses.append("(us.disposition='complete' OR (us.disposition IS NULL AND us.review_state='reviewed'))")
        elif visibility_key == "spam":
            clauses.append("(us.disposition='spam' OR c.logical_category_key='spam')")
        if since_internal_date is not None:
            clauses.append("m.internal_date >= ?")
            params.append(max(0, int(since_internal_date)))
        if source_route_key:
            clauses.append("m.source_route_key = ?")
            params.append(str(source_route_key))
        if category_key:
            clauses.append("c.logical_category_key = ?")
            params.append(str(category_key))
        search = " ".join(str(query_text or "").split()).strip()
        if search:
            clauses.append(
                "(LOWER(m.subject) LIKE ? OR LOWER(m.sender_email) LIKE ? OR LOWER(m.sender_name) LIKE ? "
                "OR LOWER(m.snippet) LIKE ?)"
            )
            pattern = f"%{search.casefold()[:200]}%"
            params.extend([pattern, pattern, pattern, pattern])
        params.append(max(1, min(int(limit), 50)))
        sql = f"""
            SELECT m.*, c.logical_category_key, c.audience, c.confidence,
                   c.decision_source, c.review_required,
                   s.summary_text, s.structured_summary_json, s.model_provider, s.model_name,
                   us.review_state AS user_review_state,
                   us.disposition AS user_disposition,
                   us.snoozed_until AS user_snoozed_until
            FROM email_messages m
            LEFT JOIN email_classifications c
              ON c.gmail_message_id=m.gmail_message_id AND c.taxonomy_version=?
            LEFT JOIN email_user_state us
              ON us.gmail_message_id=m.gmail_message_id
             AND us.user_id=? AND us.discord_channel_id=?
            LEFT JOIN email_summaries s ON s.summary_id=(
                SELECT s2.summary_id FROM email_summaries s2
                WHERE s2.scope_type='message' AND s2.scope_id=m.gmail_message_id
                      AND s2.source_hash=m.canonical_body_hash
                ORDER BY s2.created_at DESC LIMIT 1
            )
            WHERE {' AND '.join(clauses)}
            ORDER BY m.internal_date DESC, m.gmail_message_id DESC
            LIMIT ?
        """
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [self._decode_row(dict(row)) for row in rows]

    def query_messages(
        self,
        *,
        query: EmailQuery,
        taxonomy_version: str,
        user_id: str,
        discord_channel_id: str,
        allowed_source_keys: tuple[str, ...],
        allowed_category_keys: tuple[str, ...],
        selected_source_keys: tuple[str, ...] | None = None,
        selected_label_refs: tuple[str, ...] = (),
        now: str,
    ) -> list[dict[str, Any]]:
        """Read one typed, bounded query from the local Email projection."""

        if not isinstance(query, EmailQuery):
            raise ValueError("A validated EmailQuery is required.")
        source_keys = tuple(
            dict.fromkeys(
                str(item or "").strip().casefold()
                for item in allowed_source_keys
                if str(item or "").strip()
            )
        )
        category_keys = tuple(
            dict.fromkeys(
                str(item or "").strip().casefold()
                for item in allowed_category_keys
                if str(item or "").strip()
            )
        )
        if not source_keys or len(source_keys) > 64:
            raise ValueError("Email source allowlist is invalid.")
        if not category_keys or len(category_keys) > 64:
            raise ValueError("Email category allowlist is invalid.")
        selected_sources = tuple(selected_source_keys or source_keys)
        if not selected_sources or any(item not in source_keys for item in selected_sources):
            raise ValueError("Unsupported email source filter.")
        if query.classification is not None and query.classification not in category_keys:
            raise ValueError("Unsupported email classification filter.")

        scope_user = str(user_id or "").strip().casefold()
        scope_channel = str(discord_channel_id or "").strip()
        clauses = [
            f"m.source_route_key IN ({','.join('?' for _ in selected_sources)})",
            (
                "(c.logical_category_key IS NULL OR "
                f"c.logical_category_key IN ({','.join('?' for _ in category_keys)}))"
            ),
        ]
        params: list[Any] = [
            taxonomy_version,
            scope_user,
            scope_channel,
            *selected_sources,
            *category_keys,
        ]
        if query.start is not None and query.end is not None:
            clauses.extend(["m.internal_date >= ?", "m.internal_date < ?"])
            params.extend([query.start_internal_date, query.end_internal_date])

        if query.visibility == "active":
            clauses.append(
                "(us.gmail_message_id IS NULL OR us.disposition='needs_reply' "
                "OR us.review_state IN ('new','presented') "
                "OR (us.review_state='snoozed' AND us.snoozed_until IS NOT NULL "
                "AND us.snoozed_until<=?))"
            )
            params.append(str(now))
        elif query.visibility == "unseen":
            clauses.append("us.gmail_message_id IS NULL")
        elif query.visibility == "needs_reply":
            clauses.append("us.disposition='needs_reply'")
        elif query.visibility == "completed":
            clauses.append(
                "(us.disposition='complete' OR "
                "(us.disposition IS NULL AND us.review_state='reviewed'))"
            )
        elif query.visibility == "spam":
            clauses.append("(us.disposition='spam' OR c.logical_category_key='spam')")
        elif query.visibility != "all":
            raise ValueError("Unsupported email visibility filter.")

        if query.classification is not None:
            clauses.append("c.logical_category_key = ?")
            params.append(query.classification)
        if query.sender_addresses:
            clauses.append(
                f"LOWER(m.sender_email) IN ({','.join('?' for _ in query.sender_addresses)})"
            )
            params.extend(query.sender_addresses)
        if query.sender_domains:
            clauses.append(
                "LOWER(SUBSTR(m.sender_email, INSTR(m.sender_email, '@') + 1)) IN "
                f"({','.join('?' for _ in query.sender_domains)})"
            )
            params.extend(query.sender_domains)
        if query.sender_text:
            escaped = (
                query.sender_text.casefold()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            pattern = f"%{escaped}%"
            clauses.append(
                "(LOWER(m.sender_name) LIKE ? ESCAPE '\\' "
                "OR LOWER(m.sender_email) LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern, pattern])
        if query.recipient_addresses:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(m.recipient_headers_json) recipients "
                f"WHERE LOWER(CAST(recipients.value AS TEXT)) IN "
                f"({','.join('?' for _ in query.recipient_addresses)}))"
            )
            params.extend(query.recipient_addresses)
        if selected_label_refs:
            placeholders = ",".join("?" for _ in selected_label_refs)
            if query.label_match == "all":
                clauses.append(
                    "(SELECT COUNT(DISTINCT ml.label_ref) "
                    "FROM email_message_managed_labels ml "
                    "WHERE ml.gmail_message_id=m.gmail_message_id AND ml.present=1 "
                    f"AND ml.label_ref IN ({placeholders})) = ?"
                )
                params.extend([*selected_label_refs, len(selected_label_refs)])
            else:
                clauses.append(
                    "EXISTS (SELECT 1 FROM email_message_managed_labels ml "
                    "WHERE ml.gmail_message_id=m.gmail_message_id AND ml.present=1 "
                    f"AND ml.label_ref IN ({placeholders}))"
                )
                params.extend(selected_label_refs)
        for term in query.text_terms:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            clauses.append(
                "(LOWER(m.subject) LIKE ? ESCAPE '\\' "
                "OR LOWER(m.sender_email) LIKE ? ESCAPE '\\' "
                "OR LOWER(m.sender_name) LIKE ? ESCAPE '\\' "
                "OR LOWER(m.snippet) LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern, pattern, pattern, pattern])
        if query.has_attachment is True:
            clauses.append(
                "json_valid(m.attachment_metadata_json) "
                "AND json_array_length(m.attachment_metadata_json) > 0"
            )
        elif query.has_attachment is False:
            clauses.append(
                "json_valid(m.attachment_metadata_json) "
                "AND json_array_length(m.attachment_metadata_json) = 0"
            )

        direction = "ASC" if query.order == "oldest" else "DESC"
        if query.cursor_internal_date is not None and query.cursor_message_id is not None:
            comparator = ">" if query.order == "oldest" else "<"
            clauses.append(
                f"(m.internal_date {comparator} ? OR "
                f"(m.internal_date = ? AND m.gmail_message_id {comparator} ?))"
            )
            params.extend(
                [query.cursor_internal_date, query.cursor_internal_date, query.cursor_message_id]
            )
        params.append(min(query.limit + 1, 101))
        sql = f"""
            SELECT m.*, c.logical_category_key, c.audience, c.confidence,
                   c.decision_source, c.review_required,
                   s.summary_text, s.structured_summary_json, s.model_provider, s.model_name,
                   us.review_state AS user_review_state,
                   us.disposition AS user_disposition,
                   us.snoozed_until AS user_snoozed_until
            FROM email_messages m
            LEFT JOIN email_classifications c
              ON c.gmail_message_id=m.gmail_message_id AND c.taxonomy_version=?
            LEFT JOIN email_user_state us
              ON us.gmail_message_id=m.gmail_message_id
             AND us.user_id=? AND us.discord_channel_id=?
            LEFT JOIN email_summaries s ON s.summary_id=(
                SELECT s2.summary_id FROM email_summaries s2
                WHERE s2.scope_type='message' AND s2.scope_id=m.gmail_message_id
                      AND s2.source_hash=m.canonical_body_hash
                ORDER BY s2.created_at DESC LIMIT 1
            )
            WHERE {' AND '.join(clauses)}
            ORDER BY m.internal_date {direction}, m.gmail_message_id {direction}
            LIMIT ?
        """
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [self._decode_row(dict(row)) for row in rows]

    def mailbox_catalog_stats(
        self,
        *,
        allowed_source_keys: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        source_keys = tuple(dict.fromkeys(str(item).strip().casefold() for item in allowed_source_keys))
        if not source_keys:
            return []
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT source_route_key AS route_key, COUNT(*) AS message_count,
                       MIN(internal_date) AS earliest_internal_date,
                       MAX(internal_date) AS latest_internal_date
                FROM email_messages
                WHERE source_route_key IN ({','.join('?' for _ in source_keys)})
                GROUP BY source_route_key
                """,
                source_keys,
            ).fetchall()
        return [
            {
                "route_key": str(row["route_key"]),
                "message_count": int(row["message_count"] or 0),
                "earliest_indexed_at": self._internal_date_iso(row["earliest_internal_date"]),
                "latest_indexed_at": self._internal_date_iso(row["latest_internal_date"]),
            }
            for row in rows
        ]

    def projection_coverage(
        self,
        *,
        allowed_source_keys: tuple[str, ...],
    ) -> dict[str, Any]:
        rows = self.mailbox_catalog_stats(allowed_source_keys=allowed_source_keys)
        earliest_values = [row["earliest_indexed_at"] for row in rows if row["earliest_indexed_at"]]
        latest_values = [row["latest_indexed_at"] for row in rows if row["latest_indexed_at"]]
        return {
            "earliest_indexed_at": min(earliest_values) if earliest_values else None,
            "latest_indexed_at": max(latest_values) if latest_values else None,
            "message_count": sum(int(row["message_count"]) for row in rows),
        }

    def managed_labels_for_messages(
        self,
        *,
        gmail_message_ids: list[str],
    ) -> dict[str, list[dict[str, str]]]:
        message_ids = tuple(dict.fromkeys(str(item).strip() for item in gmail_message_ids if str(item).strip()))
        if not message_ids:
            return {}
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT ml.gmail_message_id, labels.label_ref, labels.display_name
                FROM email_message_managed_labels ml
                JOIN email_managed_labels labels ON labels.label_ref=ml.label_ref
                WHERE ml.gmail_message_id IN ({','.join('?' for _ in message_ids)})
                  AND ml.present=1 AND labels.enabled=1
                ORDER BY labels.display_name COLLATE NOCASE, labels.label_ref
                """,
                message_ids,
            ).fetchall()
        result: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            result.setdefault(str(row["gmail_message_id"]), []).append(
                {
                    "label_ref": str(row["label_ref"]),
                    "display_name": str(row["display_name"]),
                }
            )
        return result

    def sync_managed_label_catalog(
        self,
        *,
        labels: list[dict[str, Any]],
        now: str,
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen_refs: set[str] = set()
        seen_keys: set[str] = set()
        for item in labels:
            label_ref = str(item.get("label_ref") or "").strip()
            policy_key = str(item.get("policy_key") or "").strip().casefold()
            display_name = str(item.get("display_name") or "").strip()
            gmail_label_name = str(item.get("gmail_label_name") or "").strip()
            enabled = bool(item.get("enabled", True))
            if (
                not label_ref
                or not policy_key
                or not display_name
                or not gmail_label_name.casefold().startswith("jarvis/")
                or label_ref in seen_refs
                or policy_key in seen_keys
            ):
                raise ValueError("Invalid Email managed-label catalog.")
            normalized.append(
                {
                    "label_ref": label_ref,
                    "policy_key": policy_key,
                    "display_name": display_name,
                    "gmail_label_name": gmail_label_name,
                    "enabled": 1 if enabled else 0,
                }
            )
            seen_refs.add(label_ref)
            seen_keys.add(policy_key)
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                if seen_refs:
                    cursor.execute(
                        f"""
                        UPDATE email_managed_labels SET enabled=0, updated_at=?
                        WHERE origin='protected_config'
                          AND label_ref NOT IN ({','.join('?' for _ in seen_refs)})
                        """,
                        (now, *sorted(seen_refs)),
                    )
                else:
                    cursor.execute(
                        "UPDATE email_managed_labels SET enabled=0, updated_at=? "
                        "WHERE origin='protected_config'",
                        (now,),
                    )
                for item in normalized:
                    cursor.execute(
                        """
                        INSERT INTO email_managed_labels(
                            label_ref, policy_key, display_name, gmail_label_name,
                            enabled, origin, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'protected_config', ?, ?)
                        ON CONFLICT(label_ref) DO UPDATE SET
                            policy_key=excluded.policy_key,
                            display_name=excluded.display_name,
                            gmail_label_name=excluded.gmail_label_name,
                            enabled=excluded.enabled,
                            updated_at=excluded.updated_at
                        """,
                        (
                            item["label_ref"],
                            item["policy_key"],
                            item["display_name"],
                            item["gmail_label_name"],
                            item["enabled"],
                            now,
                            now,
                        ),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            rows = self._conn.execute(
                "SELECT * FROM email_managed_labels ORDER BY display_name COLLATE NOCASE, label_ref"
            ).fetchall()
        return [dict(row) for row in rows]

    def enabled_managed_labels(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM email_managed_labels
                WHERE enabled=1
                ORDER BY display_name COLLATE NOCASE, label_ref
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def commit_local_tool_batch(
        self,
        *,
        operation_id: str,
        tool_id: str,
        owner_user_id: str,
        discord_channel_id: str,
        arguments_hash: str,
        gmail_message_ids: list[str],
        taxonomy_version: str,
        review_state: str | None,
        category_key: str | None,
        now: str,
    ) -> dict[str, Any]:
        if tool_id not in {"email.set_review_state", "email.correct_local_category"}:
            raise ValueError("email_local_tool_invalid")
        targets = sorted(str(item).strip() for item in gmail_message_ids if str(item).strip())
        if not targets or len(targets) != len(set(targets)) or len(targets) > 50:
            raise ValueError("email_local_targets_invalid")
        if tool_id == "email.set_review_state":
            if review_state not in {"reviewed", "dismissed", "actioned"} or category_key is not None:
                raise ValueError("email_local_review_state_invalid")
        elif not category_key or review_state is not None:
            raise ValueError("email_local_category_invalid")
        identity_hash = str(operation_id).removeprefix("toolop_v1_")
        if len(identity_hash) != 64 or len(str(arguments_hash)) != 64:
            raise ValueError("email_local_operation_identity_invalid")
        idempotency_key = f"main-email-parent:v1:{operation_id}"
        result = {
            "target_count": len(targets),
            "review_state": review_state,
            "category_key": category_key,
        }
        result_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
        empty_manifest = "{}"
        empty_hash = hashlib.sha256(empty_manifest.encode("utf-8")).hexdigest()
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                existing = cursor.execute(
                    "SELECT * FROM email_tool_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if existing is not None:
                    row = dict(existing)
                    if any(
                        (
                            str(row.get("tool_id") or "") != tool_id,
                            str(row.get("owner_user_id") or "") != owner_user_id,
                            str(row.get("discord_channel_id") or "") != discord_channel_id,
                            str(row.get("arguments_hash") or "") != arguments_hash,
                            int(row.get("expected_child_count") or 0) != len(targets),
                            str(row.get("idempotency_key") or "") != idempotency_key,
                            str(row.get("operation_identity_hash") or "") != identity_hash,
                            str(row.get("result_json") or "") != result_json,
                        )
                    ):
                        raise ValueError("email_operation_id_conflict")
                    if str(row.get("status") or "") != "committed":
                        raise ValueError("email_local_reserved_invariant_violation")
                    self._conn.commit()
                    return {**self._decode_row(row), "idempotent_replay": True}
                placeholders = ",".join("?" for _ in targets)
                existing_count = int(
                    cursor.execute(
                        f"SELECT COUNT(*) FROM email_messages WHERE gmail_message_id IN ({placeholders})",
                        tuple(targets),
                    ).fetchone()[0]
                )
                if existing_count != len(targets):
                    raise ValueError("email_message_target_missing")
                cursor.execute(
                    """
                    INSERT INTO email_tool_operations(
                        operation_id, tool_id, contract_version, owner_user_id,
                        discord_channel_id, arguments_hash, effect_cardinality,
                        expected_child_count, recovery_manifest_json,
                        recovery_manifest_hash, status, result_json, idempotency_key,
                        operation_identity_hash, created_at
                    ) VALUES (?, ?, 1, ?, ?, ?, 'atomic_batch', ?, ?, ?,
                              'reserved', ?, ?, ?, ?)
                    """,
                    (
                        operation_id,
                        tool_id,
                        owner_user_id,
                        discord_channel_id,
                        arguments_hash,
                        len(targets),
                        empty_manifest,
                        empty_hash,
                        result_json,
                        idempotency_key,
                        identity_hash,
                        now,
                    ),
                )
                for message_id in targets:
                    if tool_id == "email.set_review_state":
                        disposition = {
                            "reviewed": "complete",
                            "dismissed": "dismissed",
                            "actioned": "needs_reply",
                        }[str(review_state)]
                        cursor.execute(
                            """
                            INSERT INTO email_user_state(
                                user_id, discord_channel_id, gmail_message_id,
                                review_state, disposition, snoozed_until,
                                last_presented_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)
                            ON CONFLICT(user_id, discord_channel_id, gmail_message_id)
                            DO UPDATE SET review_state=excluded.review_state,
                                          disposition=excluded.disposition,
                                          snoozed_until=NULL,
                                          updated_at=excluded.updated_at
                            """,
                            (
                                owner_user_id,
                                discord_channel_id,
                                message_id,
                                review_state,
                                disposition,
                                now,
                            ),
                        )
                    else:
                        cursor.execute(
                            """
                            INSERT INTO email_classifications(
                                classification_id, gmail_message_id, taxonomy_version,
                                logical_category_key, audience, confidence, decision_source,
                                evidence_json, review_required, corrected_by_user_id,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, 'shared', 1.0, 'correction', '{}', 0, ?, ?, ?)
                            ON CONFLICT(gmail_message_id, taxonomy_version) DO UPDATE SET
                                logical_category_key=excluded.logical_category_key,
                                confidence=1.0, decision_source='correction', evidence_json='{}',
                                review_required=0,
                                corrected_by_user_id=excluded.corrected_by_user_id,
                                updated_at=excluded.updated_at
                            """,
                            (
                                str(uuid4()),
                                message_id,
                                taxonomy_version,
                                category_key,
                                owner_user_id,
                                now,
                                now,
                            ),
                        )
                cursor.execute(
                    """
                    UPDATE email_tool_operations
                    SET status='committed', completed_at=?
                    WHERE operation_id=? AND status='reserved'
                    """,
                    (now, operation_id),
                )
                if int(cursor.rowcount or 0) != 1:
                    raise ValueError("email_local_operation_commit_conflict")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return {
            "operation_id": operation_id,
            "tool_id": tool_id,
            "status": "committed",
            "result": result,
            "idempotent_replay": False,
        }

    def reserve_mailbox_tool_operation(
        self,
        *,
        operation_id: str,
        owner_user_id: str,
        discord_channel_id: str,
        arguments_hash: str,
        recovery_manifest: dict[str, Any],
        recovery_manifest_hash: str,
        parent_manifest_hash: str,
        children: list[dict[str, Any]],
        taxonomy_version: str,
        external_request_id: str,
        max_attempts: int,
        now: str,
    ) -> dict[str, Any]:
        if not 1 <= len(children) <= 5:
            raise ValueError("email_spam_children_invalid")
        manifest_json = json.dumps(recovery_manifest, sort_keys=True, separators=(",", ":"))
        identity_hash = str(operation_id).removeprefix("toolop_v1_")
        idempotency_key = f"main-email-parent:v1:{operation_id}"
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                existing = cursor.execute(
                    "SELECT * FROM email_tool_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                parent_exists = existing is not None
                if existing is not None:
                    parent = dict(existing)
                    if any(
                        (
                            str(parent.get("tool_id") or "") != "email.move_to_spam",
                            str(parent.get("owner_user_id") or "") != owner_user_id,
                            str(parent.get("discord_channel_id") or "") != discord_channel_id,
                            str(parent.get("arguments_hash") or "") != arguments_hash,
                            int(parent.get("expected_child_count") or 0) != len(children),
                            str(parent.get("recovery_manifest_hash") or "") != recovery_manifest_hash,
                            str(parent.get("parent_manifest_hash") or "") != parent_manifest_hash,
                        )
                    ):
                        raise ValueError("email_operation_id_conflict")
                    rows = cursor.execute(
                        "SELECT * FROM email_mailbox_operations "
                        "WHERE parent_operation_id=? ORDER BY child_index",
                        (operation_id,),
                    ).fetchall()
                    if len(rows) == len(children):
                        for row, child in zip(rows, children, strict=True):
                            actual = dict(row)
                            if any(
                                (
                                    str(actual.get("operation_id") or "")
                                    != str(child["child_operation_id"]),
                                    str(actual.get("gmail_message_id") or "")
                                    != str(child["gmail_message_id"]),
                                    str(actual.get("taxonomy_version") or "") != taxonomy_version,
                                    str(actual.get("requested_by_user_id") or "") != owner_user_id,
                                    str(actual.get("discord_channel_id") or "")
                                    != discord_channel_id,
                                    str(actual.get("external_request_id") or "")
                                    != external_request_id,
                                    str(actual.get("parent_manifest_hash") or "")
                                    != parent_manifest_hash,
                                    int(actual.get("child_index") or 0)
                                    != int(child["child_index"]),
                                    str(actual.get("arguments_hash") or "")
                                    != str(child["arguments_hash"]),
                                    int(actual.get("max_attempts") or 0)
                                    != max(1, min(int(max_attempts), 5)),
                                )
                            ):
                                raise ValueError("email_operation_child_set_conflict")
                        self._conn.commit()
                        return {**self._decode_row(parent), "idempotent_replay": True}
                    count = len(rows)
                    if count != 0 or str(parent.get("status") or "") != "reserved":
                        raise ValueError("email_operation_child_set_incomplete")
                if not parent_exists:
                    cursor.execute(
                    """
                    INSERT INTO email_tool_operations(
                        operation_id, tool_id, contract_version, owner_user_id,
                        discord_channel_id, arguments_hash, effect_cardinality,
                        expected_child_count, recovery_manifest_json,
                        recovery_manifest_hash, status, result_json, idempotency_key,
                        operation_identity_hash, parent_manifest_hash, created_at
                    ) VALUES (?, 'email.move_to_spam', 1, ?, ?, ?, 'independent_batch',
                              ?, ?, ?, 'reserved', '{}', ?, ?, ?, ?)
                    """,
                        (
                        operation_id, owner_user_id, discord_channel_id, arguments_hash,
                        len(children), manifest_json, recovery_manifest_hash,
                        idempotency_key, identity_hash, parent_manifest_hash, now,
                        ),
                    )
                for child in children:
                    cursor.execute(
                        """
                        INSERT INTO email_mailbox_operations(
                            operation_id, gmail_message_id, taxonomy_version,
                            requested_by_user_id, discord_channel_id, external_request_id,
                            idempotency_key, operation_type, status, attempt_count,
                            max_attempts, next_attempt_at, created_at, updated_at,
                            parent_operation_id, parent_manifest_hash, child_index,
                            arguments_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'move_to_spam', 'queued', 0,
                                  ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            child["child_operation_id"], child["gmail_message_id"],
                            taxonomy_version, owner_user_id, discord_channel_id,
                            external_request_id,
                            f"main-email-child:v1:{child['child_operation_id']}",
                            max(1, min(int(max_attempts), 5)), now, now, now,
                            operation_id, parent_manifest_hash, int(child["child_index"]),
                            child["arguments_hash"],
                        ),
                    )
                cursor.execute(
                    "UPDATE email_tool_operations SET status='queued' WHERE operation_id=?",
                    (operation_id,),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return {
            "operation_id": operation_id,
            "status": "queued",
            "expected_child_count": len(children),
            "idempotent_replay": False,
        }

    def reserve_mailbox_parent_cursor(
        self,
        cursor: Any,
        *,
        ticket_projection: dict[str, Any],
        manifest_hash: str,
        expected_recovery_hash: str,
        operation_id: str,
        owner_user_id: str,
        discord_channel_id: str,
        arguments_hash: str,
        expected_child_count: int,
        recovery_manifest: dict[str, Any],
        now: str,
    ) -> dict[str, str]:
        if (
            str(ticket_projection.get("origin_request_id") or "") == ""
            or str(ticket_projection.get("user_id") or "") != owner_user_id
            or str(ticket_projection.get("agent_id") or "") == ""
        ):
            raise ValueError("email_ticket_projection_conflict")
        manifest_json = json.dumps(recovery_manifest, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(manifest_json.encode("utf-8")).hexdigest() != expected_recovery_hash:
            raise ValueError("email_recovery_manifest_hash_conflict")
        identity_hash = str(operation_id).removeprefix("toolop_v1_")
        idempotency_key = f"main-email-parent:v1:{operation_id}"
        existing = cursor.execute(
            "SELECT * FROM email_tool_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if existing is not None:
            row = dict(existing)
            if any(
                (
                    str(row.get("tool_id") or "") != "email.move_to_spam",
                    str(row.get("owner_user_id") or "") != owner_user_id,
                    str(row.get("discord_channel_id") or "") != discord_channel_id,
                    str(row.get("arguments_hash") or "") != arguments_hash,
                    int(row.get("expected_child_count") or 0) != expected_child_count,
                    str(row.get("recovery_manifest_hash") or "") != expected_recovery_hash,
                    str(row.get("parent_manifest_hash") or "") != manifest_hash,
                )
            ):
                raise ValueError("email_operation_id_conflict")
            return {"status": "existing", "recovery_manifest_hash": expected_recovery_hash}
        cursor.execute(
            """
            INSERT INTO email_tool_operations(
                operation_id, tool_id, contract_version, owner_user_id,
                discord_channel_id, arguments_hash, effect_cardinality,
                expected_child_count, recovery_manifest_json,
                recovery_manifest_hash, status, result_json, idempotency_key,
                operation_identity_hash, parent_manifest_hash, created_at
            ) VALUES (?, 'email.move_to_spam', 1, ?, ?, ?, 'independent_batch',
                      ?, ?, ?, 'reserved', '{}', ?, ?, ?, ?)
            """,
            (
                operation_id, owner_user_id, discord_channel_id, arguments_hash,
                expected_child_count, manifest_json, expected_recovery_hash,
                idempotency_key, identity_hash, manifest_hash, now,
            ),
        )
        return {"status": "created", "recovery_manifest_hash": expected_recovery_hash}

    def get_email_tool_operation(self, *, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM email_tool_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        return self._decode_row(dict(row)) if row is not None else None

    def reserve_managed_label_operation(
        self,
        *,
        operation_id: str,
        tool_id: str,
        owner_user_id: str,
        discord_channel_id: str,
        arguments_hash: str,
        recovery_manifest: dict[str, Any],
        recovery_manifest_hash: str,
        children: list[dict[str, Any]],
        now: str,
        max_attempts: int,
    ) -> dict[str, Any]:
        if tool_id not in {
            "email.apply_labels",
            "email.remove_labels",
            "email.set_read_state",
            "email.archive_messages",
            "email.restore_to_inbox",
        }:
            raise ValueError("email_operation_tool_invalid")
        if not 1 <= len(children) <= 50:
            raise ValueError("email_operation_children_invalid")
        manifest_json = json.dumps(recovery_manifest, sort_keys=True, separators=(",", ":"))
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                existing = cursor.execute(
                    "SELECT * FROM email_tool_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if existing is not None:
                    current = dict(existing)
                    identity = (
                        str(current.get("tool_id") or "") == tool_id
                        and str(current.get("owner_user_id") or "") == owner_user_id
                        and str(current.get("discord_channel_id") or "") == discord_channel_id
                        and str(current.get("arguments_hash") or "") == arguments_hash
                        and int(current.get("expected_child_count") or 0) == len(children)
                        and str(current.get("recovery_manifest_hash") or "")
                        == recovery_manifest_hash
                    )
                    if not identity:
                        raise ValueError("email_operation_id_conflict")
                    child_count = int(
                        cursor.execute(
                            "SELECT COUNT(*) FROM email_managed_label_operations "
                            "WHERE parent_operation_id=?",
                            (operation_id,),
                        ).fetchone()[0]
                    )
                    if child_count != len(children):
                        raise ValueError("email_operation_child_set_incomplete")
                    self._conn.commit()
                    return {**self._decode_row(current), "idempotent_replay": True}

                cursor.execute(
                    """
                    INSERT INTO email_tool_operations(
                        operation_id, tool_id, contract_version, owner_user_id,
                        discord_channel_id, arguments_hash, effect_cardinality,
                        expected_child_count, recovery_manifest_json,
                        recovery_manifest_hash, status, result_json, created_at
                    ) VALUES (?, ?, 1, ?, ?, ?, 'independent_batch', ?, ?, ?,
                              'reserved', '{}', ?)
                    """,
                    (
                        operation_id,
                        tool_id,
                        owner_user_id,
                        discord_channel_id,
                        arguments_hash,
                        len(children),
                        manifest_json,
                        recovery_manifest_hash,
                        now,
                    ),
                )
                for child in children:
                    cursor.execute(
                        """
                        INSERT INTO email_managed_label_operations(
                            child_operation_id, parent_operation_id, child_index,
                            gmail_message_id, action, managed_label_refs_json,
                            arguments_hash, idempotency_key, status, attempt_count,
                            max_attempts, lease_fencing_token, next_attempt_at,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, 0, ?, ?, ?)
                        """,
                        (
                            child["child_operation_id"],
                            operation_id,
                            int(child["child_index"]),
                            child["gmail_message_id"],
                            child["action"],
                            json.dumps(child["managed_label_refs"], separators=(",", ":")),
                            child["arguments_hash"],
                            child["idempotency_key"],
                            max(1, min(int(max_attempts), 10)),
                            now,
                            now,
                            now,
                        ),
                    )
                cursor.execute(
                    "UPDATE email_tool_operations SET status='queued' WHERE operation_id=?",
                    (operation_id,),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return {
            "operation_id": operation_id,
            "tool_id": tool_id,
            "status": "queued",
            "expected_child_count": len(children),
            "idempotent_replay": False,
        }

    def recover_reserved_managed_label_operations(self, *, now: str) -> dict[str, int]:
        recovered = 0
        failed = 0
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                rows = cursor.execute(
                    "SELECT * FROM email_tool_operations WHERE status='reserved' "
                    "AND tool_id IN ('email.apply_labels','email.remove_labels',"
                    "'email.set_read_state','email.archive_messages','email.restore_to_inbox')"
                ).fetchall()
                for raw in rows:
                    parent = self._decode_row(dict(raw))
                    expected = int(parent.get("expected_child_count") or 0)
                    manifest_json = str(parent.get("recovery_manifest_json") or "")
                    manifest = parent.get("recovery_manifest")
                    manifest_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
                    action = str((manifest or {}).get("action") or "")
                    children = (manifest or {}).get("children")
                    max_attempts = max(
                        1,
                        min(int((manifest or {}).get("max_attempts") or 4), 10),
                    )
                    valid = (
                        isinstance(manifest, dict)
                        and manifest_hash == str(parent.get("recovery_manifest_hash") or "")
                        and action in {"apply", "remove"}
                        and isinstance(children, list)
                        and expected == len(children)
                        and 1 <= expected <= 50
                    )
                    normalized_children: list[dict[str, Any]] = []
                    if valid:
                        seen_ids: set[str] = set()
                        seen_indexes: set[int] = set()
                        for child in children:
                            if not isinstance(child, dict):
                                valid = False
                                break
                            child_id = str(child.get("child_operation_id") or "")
                            child_index = int(child.get("child_index") or 0)
                            gmail_message_id = str(child.get("gmail_message_id") or "")
                            arguments_hash = str(child.get("arguments_hash") or "")
                            label_refs = child.get("managed_label_refs")
                            if (
                                not child_id
                                or not gmail_message_id
                                or not arguments_hash
                                or child_index < 1
                                or child_index > 50
                                or child_id in seen_ids
                                or child_index in seen_indexes
                                or not isinstance(label_refs, list)
                                or not label_refs
                            ):
                                valid = False
                                break
                            seen_ids.add(child_id)
                            seen_indexes.add(child_index)
                            normalized_children.append(
                                {
                                    "child_operation_id": child_id,
                                    "child_index": child_index,
                                    "gmail_message_id": gmail_message_id,
                                    "arguments_hash": arguments_hash,
                                    "managed_label_refs": [str(item) for item in label_refs],
                                }
                            )
                    if valid:
                        existing_rows = cursor.execute(
                            "SELECT * FROM email_managed_label_operations "
                            "WHERE parent_operation_id=?",
                            (parent["operation_id"],),
                        ).fetchall()
                        expected_by_id = {
                            child["child_operation_id"]: child for child in normalized_children
                        }
                        for existing_raw in existing_rows:
                            existing = self._decode_row(dict(existing_raw))
                            declared = expected_by_id.get(str(existing["child_operation_id"]))
                            if declared is None or any(
                                (
                                    int(existing["child_index"]) != declared["child_index"],
                                    str(existing["gmail_message_id"])
                                    != declared["gmail_message_id"],
                                    str(existing["action"]) != action,
                                    list(existing.get("managed_label_refs") or [])
                                    != declared["managed_label_refs"],
                                    str(existing["arguments_hash"])
                                    != declared["arguments_hash"],
                                )
                            ):
                                valid = False
                                break
                    if valid:
                        for child in normalized_children:
                            cursor.execute(
                                """
                                INSERT OR IGNORE INTO email_managed_label_operations(
                                    child_operation_id, parent_operation_id, child_index,
                                    gmail_message_id, action, managed_label_refs_json,
                                    arguments_hash, idempotency_key, status, attempt_count,
                                    max_attempts, lease_fencing_token, next_attempt_at,
                                    created_at, updated_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, 0, ?, ?, ?)
                                """,
                                (
                                    child["child_operation_id"],
                                    parent["operation_id"],
                                    child["child_index"],
                                    child["gmail_message_id"],
                                    action,
                                    json.dumps(child["managed_label_refs"], separators=(",", ":")),
                                    child["arguments_hash"],
                                    child["child_operation_id"],
                                    max_attempts,
                                    now,
                                    now,
                                    now,
                                ),
                            )
                        actual = int(
                            cursor.execute(
                                "SELECT COUNT(*) FROM email_managed_label_operations "
                                "WHERE parent_operation_id=?",
                                (parent["operation_id"],),
                            ).fetchone()[0]
                        )
                        valid = actual == expected
                    if valid:
                        cursor.execute(
                            "UPDATE email_tool_operations SET status='queued', error_code=NULL "
                            "WHERE operation_id=?",
                            (parent["operation_id"],),
                        )
                        recovered += 1
                    else:
                        cursor.execute(
                            "UPDATE email_tool_operations SET status='failed', "
                            "error_code='email_operation_child_set_incomplete', completed_at=? "
                            "WHERE operation_id=?",
                            (now, parent["operation_id"]),
                        )
                        failed += 1
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return {"recovered_count": recovered, "failed_count": failed}

    def claim_managed_label_operations(
        self,
        *,
        lease_owner: str,
        now: str,
        lease_expires_at: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        claimed: list[dict[str, Any]] = []
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                expired_parents = [
                    str(row[0])
                    for row in cursor.execute(
                        """
                        SELECT DISTINCT parent_operation_id
                        FROM email_managed_label_operations
                        WHERE status='claimed' AND lease_expires_at<=?
                        """,
                        (now,),
                    ).fetchall()
                ]
                cursor.execute(
                    """
                    UPDATE email_managed_label_operations
                    SET status=CASE WHEN attempt_count>=max_attempts
                                    THEN 'dead_letter' ELSE 'queued' END,
                        lease_owner=NULL, lease_expires_at=NULL,
                        last_error_code=CASE WHEN attempt_count>=max_attempts
                                             THEN 'email_operation_lease_expired'
                                             ELSE last_error_code END,
                        updated_at=?,
                        completed_at=CASE WHEN attempt_count>=max_attempts THEN ? ELSE NULL END
                    WHERE status='claimed' AND lease_expires_at<=?
                    """,
                    (now, now, now),
                )
                for parent_id in expired_parents:
                    self._refresh_parent_status(cursor, parent_id, now=now)
                rows = cursor.execute(
                    """
                    SELECT child_operation_id
                    FROM email_managed_label_operations
                    WHERE status='queued' AND next_attempt_at<=? AND attempt_count<max_attempts
                    ORDER BY next_attempt_at, created_at, child_index
                    LIMIT ?
                    """,
                    (now, max(0, min(int(limit), 50))),
                ).fetchall()
                for row in rows:
                    child_id = str(row["child_operation_id"])
                    updated = cursor.execute(
                        """
                        UPDATE email_managed_label_operations
                        SET status='claimed', attempt_count=attempt_count+1,
                            lease_owner=?, lease_expires_at=?,
                            lease_fencing_token=lease_fencing_token+1, updated_at=?
                        WHERE child_operation_id=? AND status='queued'
                        """,
                        (lease_owner, lease_expires_at, now, child_id),
                    ).rowcount
                    if updated:
                        claimed_row = cursor.execute(
                            "SELECT * FROM email_managed_label_operations "
                            "WHERE child_operation_id=?",
                            (child_id,),
                        ).fetchone()
                        if claimed_row is not None:
                            claimed.append(self._decode_row(dict(claimed_row)))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return claimed

    def complete_managed_label_operation(
        self,
        *,
        child_operation_id: str,
        lease_owner: str,
        lease_fencing_token: int,
        provider_labels_before: list[str],
        provider_labels_after: list[str],
        gmail_label_ids: list[str],
        managed_label_state: list[dict[str, Any]],
        now: str,
    ) -> dict[str, Any]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                row = cursor.execute(
                    "SELECT * FROM email_managed_label_operations WHERE child_operation_id=?",
                    (child_operation_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("email_operation_child_missing")
                child = self._decode_row(dict(row))
                if child.get("status") == "verified":
                    self._conn.commit()
                    return child
                updated = cursor.execute(
                    """
                    UPDATE email_managed_label_operations
                    SET status='verified', lease_owner=NULL, lease_expires_at=NULL,
                        provider_labels_before_json=?, provider_labels_after_json=?,
                        last_error_code=NULL, updated_at=?, completed_at=?
                    WHERE child_operation_id=? AND status='claimed'
                      AND lease_owner=? AND lease_fencing_token=?
                    """,
                    (
                        json.dumps(sorted(set(provider_labels_before))),
                        json.dumps(sorted(set(provider_labels_after))),
                        now,
                        now,
                        child_operation_id,
                        lease_owner,
                        int(lease_fencing_token),
                    ),
                ).rowcount
                if updated != 1:
                    raise ValueError("email_operation_lease_lost")
                cursor.execute(
                    """
                    UPDATE email_messages
                    SET gmail_label_ids_json=?, last_seen_at=?
                    WHERE gmail_message_id=?
                    """,
                    (
                        json.dumps(sorted(set(gmail_label_ids))),
                        now,
                        child["gmail_message_id"],
                    ),
                )
                for state in managed_label_state:
                    cursor.execute(
                        """
                        UPDATE email_managed_labels
                        SET provider_label_id=COALESCE(?, provider_label_id), updated_at=?
                        WHERE label_ref=? AND enabled=1
                        """,
                        (state.get("provider_label_id"), now, state["label_ref"]),
                    )
                    cursor.execute(
                        """
                        INSERT INTO email_message_managed_labels(
                            gmail_message_id, label_ref, present, provider_label_id, last_verified_at
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(gmail_message_id, label_ref) DO UPDATE SET
                            present=excluded.present,
                            provider_label_id=COALESCE(excluded.provider_label_id,
                                                       email_message_managed_labels.provider_label_id),
                            last_verified_at=excluded.last_verified_at
                        """,
                        (
                            child["gmail_message_id"],
                            state["label_ref"],
                            1 if state.get("present") else 0,
                            state.get("provider_label_id"),
                            now,
                        ),
                    )
                self._refresh_parent_status(cursor, str(child["parent_operation_id"]), now=now)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            current = self._conn.execute(
                "SELECT * FROM email_managed_label_operations WHERE child_operation_id=?",
                (child_operation_id,),
            ).fetchone()
        return self._decode_row(dict(current)) if current is not None else {}

    def fail_managed_label_operation(
        self,
        *,
        child_operation_id: str,
        lease_owner: str,
        lease_fencing_token: int,
        error_code: str,
        next_attempt_at: str,
        now: str,
    ) -> dict[str, Any]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                row = cursor.execute(
                    "SELECT * FROM email_managed_label_operations WHERE child_operation_id=?",
                    (child_operation_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("email_operation_child_missing")
                child = dict(row)
                terminal = int(child.get("attempt_count") or 0) >= int(
                    child.get("max_attempts") or 1
                )
                status = "dead_letter" if terminal else "queued"
                completed_at = now if terminal else None
                updated = cursor.execute(
                    """
                    UPDATE email_managed_label_operations
                    SET status=?, lease_owner=NULL, lease_expires_at=NULL,
                        next_attempt_at=?, last_error_code=?, updated_at=?, completed_at=?
                    WHERE child_operation_id=? AND status='claimed'
                      AND lease_owner=? AND lease_fencing_token=?
                    """,
                    (
                        status,
                        next_attempt_at,
                        str(error_code or "email_operation_failed").strip()[:120],
                        now,
                        completed_at,
                        child_operation_id,
                        lease_owner,
                        int(lease_fencing_token),
                    ),
                ).rowcount
                if updated != 1:
                    raise ValueError("email_operation_lease_lost")
                self._refresh_parent_status(cursor, str(child["parent_operation_id"]), now=now)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            current = self._conn.execute(
                "SELECT * FROM email_managed_label_operations WHERE child_operation_id=?",
                (child_operation_id,),
            ).fetchone()
        return self._decode_row(dict(current)) if current is not None else {}

    def get_managed_label_operation(
        self,
        *,
        operation_id: str,
        owner_user_id: str,
        discord_channel_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            parent = self._conn.execute(
                """
                SELECT * FROM email_tool_operations
                WHERE operation_id=? AND owner_user_id=? AND discord_channel_id=?
                """,
                (operation_id, owner_user_id, discord_channel_id),
            ).fetchone()
            if parent is None:
                return None
            counts = self._conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM email_managed_label_operations
                WHERE parent_operation_id=? GROUP BY status
                """,
                (operation_id,),
            ).fetchall()
        result = self._decode_row(dict(parent))
        result["child_counts"] = {str(row["status"]): int(row["count"]) for row in counts}
        result.pop("recovery_manifest_json", None)
        result.pop("recovery_manifest", None)
        return result

    def managed_label_started_count_since(self, *, since: str) -> int:
        with self._lock:
            return int(
                self._conn.execute(
                    """
                    SELECT COALESCE(SUM(attempt_count), 0)
                    FROM email_managed_label_operations
                    WHERE attempt_count > 0 AND updated_at >= ?
                    """,
                    (since,),
                ).fetchone()[0]
            )

    @staticmethod
    def _refresh_parent_status(cursor: Any, operation_id: str, *, now: str) -> None:
        rows = cursor.execute(
            """
            SELECT status, COUNT(*) AS count FROM email_managed_label_operations
            WHERE parent_operation_id=? GROUP BY status
            """,
            (operation_id,),
        ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        open_count = counts.get("queued", 0) + counts.get("claimed", 0)
        if open_count:
            status = "queued"
            completed_at = None
        else:
            verified = counts.get("verified", 0)
            failed = counts.get("dead_letter", 0) + counts.get("cancelled", 0)
            status = "completed" if verified and not failed else ("partial" if verified else "failed")
            completed_at = now
        cursor.execute(
            """
            UPDATE email_tool_operations
            SET status=?, result_json=?, completed_at=?,
                recovery_manifest_json=CASE WHEN ? IS NULL
                    THEN recovery_manifest_json ELSE '{}' END
            WHERE operation_id=?
            """,
            (
                status,
                json.dumps({"child_counts": counts}, sort_keys=True),
                completed_at,
                completed_at,
                operation_id,
            ),
        )

    def get_reference_set(
        self,
        *,
        reference_set_id: str,
        user_id: str,
        discord_channel_id: str,
        now: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM email_reference_sets
                WHERE reference_set_id=? AND user_id=? AND discord_channel_id=? AND expires_at>?
                """,
                (reference_set_id, user_id, discord_channel_id, now),
            ).fetchone()
        return self._decode_row(dict(row)) if row is not None else None

    def get_message(self, *, gmail_message_id: str, taxonomy_version: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT m.*, c.logical_category_key, c.audience, c.confidence,
                       c.decision_source, c.review_required,
                       s.summary_text, s.structured_summary_json, s.model_provider, s.model_name
                FROM email_messages m
                LEFT JOIN email_classifications c
                  ON c.gmail_message_id=m.gmail_message_id AND c.taxonomy_version=?
                LEFT JOIN email_summaries s ON s.summary_id=(
                    SELECT s2.summary_id FROM email_summaries s2
                    WHERE s2.scope_type='message' AND s2.scope_id=m.gmail_message_id
                          AND s2.source_hash=m.canonical_body_hash
                    ORDER BY s2.created_at DESC LIMIT 1
                )
                WHERE m.gmail_message_id=?
                """,
                (taxonomy_version, gmail_message_id),
            ).fetchone()
        return self._decode_row(dict(row)) if row is not None else None

    def get_thread(
        self,
        *,
        gmail_thread_id: str,
        taxonomy_version: str,
        limit: int = 50,
        after_internal_date: int | None = None,
        after_message_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["m.gmail_thread_id=?"]
        params: list[Any] = [taxonomy_version, gmail_thread_id]
        if after_internal_date is not None and after_message_id:
            clauses.append(
                "(m.internal_date > ? OR (m.internal_date = ? AND m.gmail_message_id > ?))"
            )
            params.extend([after_internal_date, after_internal_date, after_message_id])
        params.append(max(1, min(int(limit), 100)))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT m.*, c.logical_category_key, c.audience, c.confidence,
                       c.decision_source, c.review_required,
                       s.summary_text, s.structured_summary_json, s.model_provider, s.model_name
                FROM email_messages m
                LEFT JOIN email_classifications c
                  ON c.gmail_message_id=m.gmail_message_id AND c.taxonomy_version=?
                LEFT JOIN email_summaries s ON s.summary_id=(
                    SELECT s2.summary_id FROM email_summaries s2
                    WHERE s2.scope_type='message' AND s2.scope_id=m.gmail_message_id
                          AND s2.source_hash=m.canonical_body_hash
                    ORDER BY s2.created_at DESC LIMIT 1
                )
                WHERE {' AND '.join(clauses)}
                ORDER BY m.internal_date ASC, m.gmail_message_id ASC LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._decode_row(dict(row)) for row in rows]

    def set_user_state(
        self,
        *,
        user_id: str,
        discord_channel_id: str,
        gmail_message_id: str,
        review_state: str,
        disposition: str | None = None,
        snoozed_until: str | None,
        presented: bool,
        now: str,
    ) -> dict[str, Any]:
        if review_state not in {"new", "presented", "reviewed", "dismissed", "snoozed", "actioned"}:
            raise ValueError("Unsupported email review state.")
        disposition_value = str(disposition or "").strip().casefold() or None
        if disposition_value not in {None, "active", "needs_reply", "complete", "dismissed", "snoozed", "spam"}:
            raise ValueError("Unsupported email disposition.")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO email_user_state(
                    user_id, discord_channel_id, gmail_message_id, review_state, disposition,
                    snoozed_until, last_presented_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, discord_channel_id, gmail_message_id) DO UPDATE SET
                    review_state=CASE
                        WHEN excluded.review_state='presented'
                             AND email_user_state.review_state IN ('reviewed','dismissed','actioned')
                        THEN email_user_state.review_state
                        ELSE excluded.review_state END,
                    disposition=COALESCE(excluded.disposition, email_user_state.disposition),
                    snoozed_until=CASE
                        WHEN excluded.review_state='presented' THEN email_user_state.snoozed_until
                        ELSE excluded.snoozed_until END,
                    last_presented_at=COALESCE(excluded.last_presented_at,
                                               email_user_state.last_presented_at),
                    updated_at=excluded.updated_at
                """,
                (
                    user_id,
                    discord_channel_id,
                    gmail_message_id,
                    review_state,
                    disposition_value,
                    snoozed_until,
                    now if presented else None,
                    now,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                """
                SELECT * FROM email_user_state
                WHERE user_id=? AND discord_channel_id=? AND gmail_message_id=?
                """,
                (user_id, discord_channel_id, gmail_message_id),
            ).fetchone()
        return dict(row) if row is not None else {}

    def create_reference_set(
        self,
        *,
        user_id: str,
        discord_channel_id: str,
        query_text: str,
        message_ids: list[str],
        thread_ids: list[str],
        focused_message_id: str | None,
        focused_thread_id: str | None,
        created_at: str,
        expires_at: str,
    ) -> dict[str, Any]:
        reference_set_id = str(uuid4())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO email_reference_sets(
                    reference_set_id, user_id, discord_channel_id, query_text,
                    ordered_message_ids_json, ordered_thread_ids_json,
                    focused_message_id, focused_thread_id, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reference_set_id,
                    user_id,
                    discord_channel_id,
                    " ".join(str(query_text or "").split())[:4000],
                    json.dumps(message_ids[:50]),
                    json.dumps(thread_ids[:50]),
                    focused_message_id,
                    focused_thread_id,
                    created_at,
                    expires_at,
                ),
            )
            self._conn.commit()
        return {
            "reference_set_id": reference_set_id,
            "message_ids": list(message_ids[:50]),
            "thread_ids": list(thread_ids[:50]),
            "focused_message_id": focused_message_id,
            "focused_thread_id": focused_thread_id,
            "created_at": created_at,
            "expires_at": expires_at,
        }

    def latest_reference_set(
        self,
        *,
        user_id: str,
        discord_channel_id: str,
        now: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM email_reference_sets
                WHERE user_id=? AND discord_channel_id=? AND expires_at>?
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (user_id, discord_channel_id, now),
            ).fetchone()
        return self._decode_row(dict(row)) if row is not None else None

    def latest_cursor_reference_set(
        self,
        *,
        kind: str,
        user_id: str,
        discord_channel_id: str,
        now: str,
    ) -> dict[str, Any] | None:
        prefix = f"cursor:{str(kind or '').strip().casefold()}:v1:"
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM email_reference_sets
                WHERE user_id=? AND discord_channel_id=? AND expires_at>?
                  AND query_text LIKE ? ESCAPE '\\'
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (
                    str(user_id or "").strip().casefold(),
                    str(discord_channel_id or "").strip(),
                    now,
                    prefix.replace("%", "\\%").replace("_", "\\_") + "%",
                ),
            ).fetchone()
        return self._decode_row(dict(row)) if row is not None else None

    def resolve_reference(
        self,
        *,
        user_id: str,
        discord_channel_id: str,
        reference: str | None,
        now: str,
    ) -> dict[str, Any] | None:
        current = self.latest_reference_set(
            user_id=user_id,
            discord_channel_id=discord_channel_id,
            now=now,
        )
        if current is None:
            return None
        message_ids = current.get("ordered_message_ids")
        thread_ids = current.get("ordered_thread_ids")
        if not isinstance(message_ids, list):
            message_ids = []
        if not isinstance(thread_ids, list):
            thread_ids = []
        normalized = str(reference or "").strip().casefold()
        if normalized in {"", "it", "that", "this", "the message", "that message", "the email"}:
            message_id = str(current.get("focused_message_id") or "").strip()
            thread_id = str(current.get("focused_thread_id") or "").strip()
            if not message_id and message_ids:
                message_id = str(message_ids[0])
            if not thread_id and thread_ids:
                thread_id = str(thread_ids[0])
            return {
                "reference": None,
                "gmail_message_id": message_id or None,
                "gmail_thread_id": thread_id or None,
                "reference_set_id": current.get("reference_set_id"),
            }
        import re

        match = re.fullmatch(r"e(\d{1,2})", normalized)
        if not match:
            return None
        index = int(match.group(1)) - 1
        if index < 0 or index >= len(message_ids):
            return None
        return {
            "reference": f"E{index + 1}",
            "gmail_message_id": str(message_ids[index]),
            "gmail_thread_id": str(thread_ids[index]) if index < len(thread_ids) else None,
            "reference_set_id": current.get("reference_set_id"),
        }

    def list_category_label_candidates(
        self,
        *,
        taxonomy_version: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        cap = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT m.gmail_message_id, c.logical_category_key, c.updated_at AS classification_updated_at
                FROM email_messages m
                JOIN email_classifications c
                  ON c.gmail_message_id=m.gmail_message_id AND c.taxonomy_version=?
                WHERE c.logical_category_key<>'spam'
                  AND NOT EXISTS (
                    SELECT 1 FROM email_label_operations op
                    WHERE op.gmail_message_id=m.gmail_message_id
                      AND op.taxonomy_version=c.taxonomy_version
                      AND op.logical_category_key=c.logical_category_key
                      AND op.operation_type='add'
                      AND op.status IN ('queued','claimed','verified')
                      AND op.created_at>=c.updated_at
                  )
                ORDER BY m.internal_date ASC, m.gmail_message_id ASC
                LIMIT ?
                """,
                (taxonomy_version, cap),
            ).fetchall()
        return [dict(row) for row in rows]

    def enqueue_label_operation(
        self,
        *,
        gmail_message_id: str,
        taxonomy_version: str,
        logical_category_key: str,
        gmail_label_name: str,
        operation_type: str,
        idempotency_key: str,
        max_attempts: int,
        now: str,
    ) -> dict[str, Any]:
        operation_key = str(operation_type or "").strip().casefold()
        if operation_key not in {"add", "remove_managed"}:
            raise ValueError("Unsupported managed-label operation type.")
        operation_id = str(uuid4())
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO email_label_operations(
                    operation_id, gmail_message_id, taxonomy_version,
                    logical_category_key, gmail_label_id, gmail_label_name,
                    operation_type, idempotency_key, status, attempt_count,
                    max_attempts, next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, '', ?, ?, ?, 'queued', 0, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    gmail_message_id,
                    taxonomy_version,
                    logical_category_key,
                    gmail_label_name,
                    operation_key,
                    idempotency_key,
                    max(1, min(int(max_attempts), 5)),
                    now,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM email_label_operations WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        return self._decode_row(dict(row)) if row is not None else {}

    def claim_label_operations(
        self,
        *,
        lease_owner: str,
        now: str,
        lease_expires_at: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        owner = str(lease_owner or "").strip()
        if not owner:
            raise ValueError("A managed-label worker lease owner is required.")
        cap = max(1, min(int(limit), 25))
        claimed: list[dict[str, Any]] = []
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    UPDATE email_label_operations
                    SET status='dead_letter', lease_owner=NULL, lease_expires_at=NULL,
                        last_error_code=COALESCE(last_error_code, 'lease_expired_after_final_attempt'),
                        updated_at=?, completed_at=?
                    WHERE status='claimed' AND lease_expires_at IS NOT NULL
                      AND lease_expires_at<=? AND attempt_count>=max_attempts
                    """,
                    (now, now, now),
                )
                rows = self._conn.execute(
                    """
                    SELECT * FROM email_label_operations
                    WHERE attempt_count < max_attempts
                      AND (
                        (status='queued' AND next_attempt_at<=?)
                        OR (status='claimed' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?)
                      )
                    ORDER BY created_at ASC, operation_id ASC
                    LIMIT ?
                    """,
                    (now, now, cap),
                ).fetchall()
                for row in rows:
                    updated = self._conn.execute(
                        """
                        UPDATE email_label_operations
                        SET status='claimed', attempt_count=attempt_count+1,
                            lease_owner=?, lease_expires_at=?,
                            first_claimed_at=COALESCE(first_claimed_at, ?), updated_at=?
                        WHERE operation_id=?
                          AND attempt_count < max_attempts
                          AND (
                            (status='queued' AND next_attempt_at<=?)
                            OR (status='claimed' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?)
                          )
                        """,
                        (owner, lease_expires_at, now, now, row["operation_id"], now, now),
                    )
                    if updated.rowcount:
                        current = self._conn.execute(
                            "SELECT * FROM email_label_operations WHERE operation_id=?",
                            (row["operation_id"],),
                        ).fetchone()
                        if current is not None:
                            claimed.append(self._decode_row(dict(current)))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return claimed

    def complete_label_operation(
        self,
        *,
        operation_id: str,
        lease_owner: str,
        gmail_label_id: str,
        labels_before: list[str],
        labels_after: list[str],
        now: str,
    ) -> dict[str, Any]:
        with self._lock:
            updated = self._conn.execute(
                """
                UPDATE email_label_operations
                SET status='verified', gmail_label_id=?, labels_before_json=?, labels_after_json=?,
                    lease_owner=NULL, lease_expires_at=NULL, last_error_code=NULL,
                    updated_at=?, completed_at=?
                WHERE operation_id=? AND status='claimed' AND lease_owner=?
                """,
                (
                    gmail_label_id,
                    json.dumps(sorted(set(labels_before))),
                    json.dumps(sorted(set(labels_after))),
                    now,
                    now,
                    operation_id,
                    lease_owner,
                ),
            )
            if not updated.rowcount:
                self._conn.rollback()
                raise RuntimeError("Managed-label operation lease was lost before completion.")
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM email_label_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        return self._decode_row(dict(row)) if row is not None else {}

    def fail_label_operation(
        self,
        *,
        operation_id: str,
        lease_owner: str,
        error_code: str,
        next_attempt_at: str,
        now: str,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT attempt_count, max_attempts FROM email_label_operations
                WHERE operation_id=? AND status='claimed' AND lease_owner=?
                """,
                (operation_id, lease_owner),
            ).fetchone()
            if row is None:
                raise RuntimeError("Managed-label operation lease was lost before failure recording.")
            exhausted = int(row["attempt_count"] or 0) >= int(row["max_attempts"] or 1)
            status = "dead_letter" if exhausted else "queued"
            completed_at = now if exhausted else None
            self._conn.execute(
                """
                UPDATE email_label_operations
                SET status=?, lease_owner=NULL, lease_expires_at=NULL,
                    next_attempt_at=?, last_error_code=?, updated_at=?, completed_at=?
                WHERE operation_id=?
                """,
                (status, next_attempt_at, str(error_code or "worker_error")[:120], now, completed_at, operation_id),
            )
            self._conn.commit()
            current = self._conn.execute(
                "SELECT * FROM email_label_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        return self._decode_row(dict(current)) if current is not None else {}

    def label_started_count_since(self, *, since: str) -> int:
        with self._lock:
            return int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM email_label_operations "
                    "WHERE first_claimed_at IS NOT NULL AND first_claimed_at>=?",
                    (since,),
                ).fetchone()[0]
            )

    def get_label_operation(self, *, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM email_label_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        return self._decode_row(dict(row)) if row is not None else None

    def enqueue_mailbox_operation(
        self,
        *,
        operation_type: str,
        gmail_message_id: str,
        taxonomy_version: str,
        requested_by_user_id: str,
        discord_channel_id: str,
        external_request_id: str,
        idempotency_key: str,
        max_attempts: int,
        now: str,
    ) -> dict[str, Any]:
        operation_key = str(operation_type or "").strip().casefold()
        if operation_key not in {"move_to_spam", "mark_read_complete"}:
            raise ValueError("Unsupported mailbox operation type.")
        operation_id = str(uuid4())
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO email_mailbox_operations(
                    operation_id, gmail_message_id, taxonomy_version,
                    requested_by_user_id, discord_channel_id, external_request_id,
                    idempotency_key, operation_type, status, attempt_count,
                    max_attempts, next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    gmail_message_id,
                    taxonomy_version,
                    requested_by_user_id,
                    discord_channel_id,
                    external_request_id,
                    idempotency_key,
                    operation_key,
                    max(1, min(int(max_attempts), 5)),
                    now,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM email_mailbox_operations WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        return self._decode_row(dict(row)) if row is not None else {}

    def enqueue_spam_operation(self, **kwargs: Any) -> dict[str, Any]:
        return self.enqueue_mailbox_operation(operation_type="move_to_spam", **kwargs)

    def claim_mailbox_operations(
        self,
        *,
        lease_owner: str,
        now: str,
        lease_expires_at: str,
        limit: int,
        parent_bound: bool | None = None,
    ) -> list[dict[str, Any]]:
        owner = str(lease_owner or "").strip()
        if not owner:
            raise ValueError("A mailbox-worker lease owner is required.")
        cap = max(1, min(int(limit), 10))
        claimed: list[dict[str, Any]] = []
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    UPDATE email_mailbox_operations
                    SET status='dead_letter', lease_owner=NULL, lease_expires_at=NULL,
                        last_error_code=COALESCE(last_error_code, 'lease_expired_after_final_attempt'),
                        updated_at=?, completed_at=?
                    WHERE status='claimed' AND lease_expires_at IS NOT NULL
                      AND lease_expires_at<=? AND attempt_count>=max_attempts
                    """,
                    (now, now, now),
                )
                binding_clause = (
                    "AND parent_operation_id IS NOT NULL"
                    if parent_bound is True
                    else "AND parent_operation_id IS NULL"
                    if parent_bound is False
                    else ""
                )
                rows = self._conn.execute(
                    f"""
                    SELECT * FROM email_mailbox_operations
                    WHERE attempt_count < max_attempts
                      {binding_clause}
                      AND (
                        (status='queued' AND next_attempt_at<=?)
                        OR (status='claimed' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?)
                      )
                    ORDER BY created_at ASC, operation_id ASC
                    LIMIT ?
                    """,
                    (now, now, cap),
                ).fetchall()
                for row in rows:
                    updated = self._conn.execute(
                        """
                        UPDATE email_mailbox_operations
                        SET status='claimed', attempt_count=attempt_count+1,
                            lease_owner=?, lease_expires_at=?,
                            first_claimed_at=COALESCE(first_claimed_at, ?), updated_at=?
                        WHERE operation_id=?
                          AND attempt_count < max_attempts
                          AND (
                            (status='queued' AND next_attempt_at<=?)
                            OR (status='claimed' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?)
                          )
                        """,
                        (owner, lease_expires_at, now, now, row["operation_id"], now, now),
                    )
                    if updated.rowcount:
                        current = self._conn.execute(
                            "SELECT * FROM email_mailbox_operations WHERE operation_id=?",
                            (row["operation_id"],),
                        ).fetchone()
                        if current is not None:
                            claimed.append(self._decode_row(dict(current)))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return claimed

    def claim_spam_operations(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.claim_mailbox_operations(parent_bound=False, **kwargs)

    def recover_mailbox_tool_operations(self, *, now: str, limit: int = 50) -> dict[str, int]:
        recovered = 0
        failed = 0
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                parents = cursor.execute(
                    """
                    SELECT * FROM email_tool_operations
                    WHERE tool_id='email.move_to_spam' AND status IN ('reserved','queued')
                    ORDER BY created_at LIMIT ?
                    """,
                    (max(1, min(int(limit), 100)),),
                ).fetchall()
                for raw in parents:
                    parent = self._decode_row(dict(raw))
                    recovery = parent.get("recovery_manifest") or {}
                    children = recovery.get("children")
                    taxonomy_version = str(recovery.get("taxonomy_version") or "")
                    external_request_id = str(recovery.get("external_request_id") or "")
                    source_interface = str(recovery.get("source_interface") or "")
                    external_user_id = str(recovery.get("external_user_id") or "")
                    agent_id = str(recovery.get("agent_id") or "")
                    max_attempts = recovery.get("max_attempts")
                    valid = (
                        isinstance(children, list)
                        and len(children) == int(parent.get("expected_child_count") or 0)
                        and bool(taxonomy_version)
                        and bool(external_request_id)
                        and bool(source_interface)
                        and bool(external_user_id)
                        and bool(agent_id)
                        and isinstance(max_attempts, int)
                        and not isinstance(max_attempts, bool)
                        and 1 <= max_attempts <= 5
                        and hashlib.sha256(
                            str(parent.get("recovery_manifest_json") or "").encode("utf-8")
                        ).hexdigest() == str(parent.get("recovery_manifest_hash") or "")
                    )
                    if valid:
                        for child in children:
                            if not isinstance(child, dict):
                                valid = False
                                break
                            expected = {
                                "operation_id": str(child.get("child_operation_id") or ""),
                                "gmail_message_id": str(child.get("gmail_message_id") or ""),
                                "child_index": int(child.get("child_index") or 0),
                                "arguments_hash": str(child.get("arguments_hash") or ""),
                            }
                            if (
                                not expected["operation_id"]
                                or not expected["gmail_message_id"]
                                or expected["child_index"] < 1
                                or len(expected["arguments_hash"]) != 64
                            ):
                                valid = False
                                break
                            row = cursor.execute(
                                "SELECT * FROM email_mailbox_operations WHERE operation_id=?",
                                (expected["operation_id"],),
                            ).fetchone()
                            if row is None:
                                cursor.execute(
                                    """
                                    INSERT INTO email_mailbox_operations(
                                        operation_id, gmail_message_id, taxonomy_version,
                                        requested_by_user_id, discord_channel_id,
                                        external_request_id, idempotency_key, operation_type,
                                        status, attempt_count, max_attempts, next_attempt_at,
                                        created_at, updated_at, parent_operation_id,
                                        parent_manifest_hash, child_index, arguments_hash
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'move_to_spam', 'queued',
                                              0, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        expected["operation_id"], expected["gmail_message_id"],
                                        taxonomy_version,
                                        parent["owner_user_id"], parent["discord_channel_id"],
                                        external_request_id,
                                        f"main-email-child:v1:{expected['operation_id']}",
                                        max_attempts, now, now, now, parent["operation_id"],
                                        parent["parent_manifest_hash"], expected["child_index"],
                                        expected["arguments_hash"],
                                    ),
                                )
                            else:
                                actual = dict(row)
                                if any(
                                    (
                                        str(actual.get("gmail_message_id") or "") != expected["gmail_message_id"],
                                        int(actual.get("child_index") or 0) != expected["child_index"],
                                        str(actual.get("arguments_hash") or "") != expected["arguments_hash"],
                                        str(actual.get("parent_operation_id") or "") != str(parent["operation_id"]),
                                        str(actual.get("parent_manifest_hash") or "") != str(parent["parent_manifest_hash"]),
                                        str(actual.get("taxonomy_version") or "") != taxonomy_version,
                                        str(actual.get("external_request_id") or "") != external_request_id,
                                        int(actual.get("max_attempts") or 0) != max_attempts,
                                    )
                                ):
                                    valid = False
                                    break
                    if valid:
                        count = int(cursor.execute(
                            "SELECT COUNT(*) FROM email_mailbox_operations WHERE parent_operation_id=?",
                            (parent["operation_id"],),
                        ).fetchone()[0])
                        valid = count == int(parent["expected_child_count"])
                    if valid:
                        cursor.execute(
                            "UPDATE email_tool_operations SET status='queued', error_code=NULL "
                            "WHERE operation_id=? AND status='reserved'",
                            (parent["operation_id"],),
                        )
                        recovered += 1
                    else:
                        cursor.execute(
                            "UPDATE email_tool_operations SET status='failed', "
                            "error_code='email_operation_child_set_incomplete', completed_at=? "
                            "WHERE operation_id=?",
                            (now, parent["operation_id"]),
                        )
                        failed += 1
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return {"recovered_count": recovered, "failed_count": failed}

    def cancel_claimed_mailbox_operation(
        self,
        *,
        operation_id: str,
        lease_owner: str,
        reason_code: str,
        now: str,
    ) -> dict[str, Any]:
        if reason_code not in {"policy_denied", "execution_cancelled"}:
            raise ValueError("email_mailbox_cancellation_reason_invalid")
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                changed = cursor.execute(
                    """
                    UPDATE email_mailbox_operations
                    SET status='cancelled', last_error_code=?, completed_at=?,
                        updated_at=?, lease_owner=NULL, lease_expires_at=NULL
                    WHERE operation_id=? AND status='claimed' AND lease_owner=?
                    """,
                    (reason_code, now, now, operation_id, lease_owner),
                ).rowcount
                if int(changed or 0) != 1:
                    raise ValueError("email_mailbox_operation_lease_lost")
                row = cursor.execute(
                    "SELECT * FROM email_mailbox_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self._decode_row(dict(row))

    def cancel_mailbox_tool_operation(
        self,
        *,
        parent_operation_id: str,
        reason_code: str,
        now: str,
    ) -> dict[str, Any]:
        """Materialize and cancel a quiesced provider batch without a Gmail call."""

        if reason_code not in {"policy_denied", "execution_cancelled"}:
            raise ValueError("email_mailbox_cancellation_reason_invalid")
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                raw_parent = cursor.execute(
                    "SELECT * FROM email_tool_operations WHERE operation_id=?",
                    (parent_operation_id,),
                ).fetchone()
                if raw_parent is None:
                    raise ValueError("email_operation_not_found")
                parent = self._decode_row(dict(raw_parent))
                if str(parent.get("tool_id") or "") != "email.move_to_spam":
                    raise ValueError("email_operation_tool_invalid")
                recovery = parent.get("recovery_manifest") or {}
                children = recovery.get("children")
                taxonomy_version = str(recovery.get("taxonomy_version") or "")
                external_request_id = str(recovery.get("external_request_id") or "")
                max_attempts = recovery.get("max_attempts")
                if (
                    not isinstance(children, list)
                    or len(children) != int(parent.get("expected_child_count") or 0)
                    or not taxonomy_version
                    or not external_request_id
                    or not isinstance(max_attempts, int)
                    or isinstance(max_attempts, bool)
                    or not 1 <= max_attempts <= 5
                    or hashlib.sha256(
                        str(parent.get("recovery_manifest_json") or "").encode("utf-8")
                    ).hexdigest()
                    != str(parent.get("recovery_manifest_hash") or "")
                ):
                    raise ValueError("email_operation_recovery_manifest_invalid")
                cancelled = 0
                for child in children:
                    if not isinstance(child, dict):
                        raise ValueError("email_operation_recovery_manifest_invalid")
                    child_id = str(child.get("child_operation_id") or "")
                    message_id = str(child.get("gmail_message_id") or "")
                    child_index = int(child.get("child_index") or 0)
                    child_hash = str(child.get("arguments_hash") or "")
                    existing = cursor.execute(
                        "SELECT * FROM email_mailbox_operations WHERE operation_id=?",
                        (child_id,),
                    ).fetchone()
                    if existing is None:
                        cursor.execute(
                            """
                            INSERT INTO email_mailbox_operations(
                                operation_id, gmail_message_id, taxonomy_version,
                                requested_by_user_id, discord_channel_id,
                                external_request_id, idempotency_key, operation_type,
                                status, attempt_count, max_attempts, next_attempt_at,
                                last_error_code, created_at, updated_at, completed_at,
                                parent_operation_id, parent_manifest_hash, child_index,
                                arguments_hash
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'move_to_spam', 'cancelled',
                                      0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                child_id,
                                message_id,
                                taxonomy_version,
                                parent["owner_user_id"],
                                parent["discord_channel_id"],
                                external_request_id,
                                f"main-email-child:v1:{child_id}",
                                max_attempts,
                                now,
                                reason_code,
                                now,
                                now,
                                now,
                                parent_operation_id,
                                parent["parent_manifest_hash"],
                                child_index,
                                child_hash,
                            ),
                        )
                        cancelled += 1
                        continue
                    row = dict(existing)
                    if any(
                        (
                            str(row.get("gmail_message_id") or "") != message_id,
                            str(row.get("parent_operation_id") or "") != parent_operation_id,
                            str(row.get("parent_manifest_hash") or "")
                            != str(parent.get("parent_manifest_hash") or ""),
                            int(row.get("child_index") or 0) != child_index,
                            str(row.get("arguments_hash") or "") != child_hash,
                        )
                    ):
                        raise ValueError("email_operation_child_set_conflict")
                    status = str(row.get("status") or "")
                    if status == "claimed":
                        raise ValueError("email_mailbox_claim_reconciliation_required")
                    if status == "queued":
                        cursor.execute(
                            """
                            UPDATE email_mailbox_operations
                            SET status='cancelled', last_error_code=?, completed_at=?,
                                updated_at=?, lease_owner=NULL, lease_expires_at=NULL
                            WHERE operation_id=? AND status='queued'
                            """,
                            (reason_code, now, now, child_id),
                        )
                        cancelled += int(cursor.rowcount or 0)
                    elif status == "cancelled" and str(row.get("last_error_code") or "") != reason_code:
                        raise ValueError("email_mailbox_cancellation_conflict")
                cursor.execute(
                    "UPDATE email_tool_operations SET status='queued' "
                    "WHERE operation_id=? AND status='reserved'",
                    (parent_operation_id,),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return {
            "parent_operation_id": parent_operation_id,
            "cancelled_count": cancelled,
            "reason_code": reason_code,
        }

    def terminal_parent_mailbox_children(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT child.*, parent.parent_manifest_hash AS p7_manifest_hash,
                       parent.status AS parent_status
                FROM email_mailbox_operations AS child
                JOIN email_tool_operations AS parent
                  ON parent.operation_id=child.parent_operation_id
                WHERE child.parent_operation_id IS NOT NULL
                  AND child.status IN ('verified','dead_letter','cancelled')
                  AND parent.status IN ('reserved','queued','completed','partial','failed','cancelled')
                  AND parent.recovery_manifest_json<>'{}'
                ORDER BY child.updated_at, child.operation_id LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [self._decode_row(dict(row)) for row in rows]

    def reduce_mailbox_tool_parent(
        self,
        *,
        parent_operation_id: str,
        outcomes_reconciled: bool,
        now: str,
    ) -> dict[str, Any]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                rows = cursor.execute(
                    "SELECT status, last_error_code FROM email_mailbox_operations "
                    "WHERE parent_operation_id=?",
                    (parent_operation_id,),
                ).fetchall()
                expected_row = cursor.execute(
                    "SELECT expected_child_count FROM email_tool_operations WHERE operation_id=?",
                    (parent_operation_id,),
                ).fetchone()
                if expected_row is None or len(rows) != int(expected_row[0]):
                    raise ValueError("email_operation_child_set_incomplete")
                states = [str(row["status"]) for row in rows]
                if any(state in {"queued", "claimed"} for state in states):
                    status = "queued"
                    completed_at = None
                elif all(state == "verified" for state in states):
                    status = "completed"
                    completed_at = now
                elif any(state == "verified" for state in states):
                    status = "partial"
                    completed_at = now
                elif all(
                    state == "cancelled" and str(row["last_error_code"] or "") == "execution_cancelled"
                    for state, row in zip(states, rows, strict=True)
                ):
                    status = "cancelled"
                    completed_at = now
                else:
                    status = "failed"
                    completed_at = now
                cursor.execute(
                    """
                    UPDATE email_tool_operations
                    SET status=?, completed_at=?, result_json=?,
                        recovery_manifest_json=CASE WHEN ? THEN '{}' ELSE recovery_manifest_json END
                    WHERE operation_id=?
                    """,
                    (
                        status, completed_at,
                        json.dumps({"child_counts": {key: states.count(key) for key in set(states)}}, sort_keys=True),
                        int(bool(outcomes_reconciled and completed_at)),
                        parent_operation_id,
                    ),
                )
                row = cursor.execute(
                    "SELECT * FROM email_tool_operations WHERE operation_id=?",
                    (parent_operation_id,),
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self._decode_row(dict(row)) if row is not None else {}

    def complete_mailbox_operation(
        self,
        *,
        operation_id: str,
        lease_owner: str,
        labels_before: list[str],
        labels_after: list[str],
        now: str,
    ) -> dict[str, Any]:
        with self._lock:
            updated = self._conn.execute(
                """
                UPDATE email_mailbox_operations
                SET status='verified', labels_before_json=?, labels_after_json=?,
                    lease_owner=NULL, lease_expires_at=NULL, last_error_code=NULL,
                    updated_at=?, completed_at=?
                WHERE operation_id=? AND status='claimed' AND lease_owner=?
                """,
                (
                    json.dumps(sorted(set(labels_before))),
                    json.dumps(sorted(set(labels_after))),
                    now,
                    now,
                    operation_id,
                    lease_owner,
                ),
            )
            if not updated.rowcount:
                self._conn.rollback()
                raise RuntimeError("Mailbox operation lease was lost before completion.")
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM email_mailbox_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        return self._decode_row(dict(row)) if row is not None else {}

    def complete_spam_operation(self, **kwargs: Any) -> dict[str, Any]:
        return self.complete_mailbox_operation(**kwargs)

    def fail_mailbox_operation(
        self,
        *,
        operation_id: str,
        lease_owner: str,
        error_code: str,
        next_attempt_at: str,
        now: str,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT attempt_count, max_attempts FROM email_mailbox_operations
                WHERE operation_id=? AND status='claimed' AND lease_owner=?
                """,
                (operation_id, lease_owner),
            ).fetchone()
            if row is None:
                raise RuntimeError("Mailbox operation lease was lost before failure recording.")
            exhausted = int(row["attempt_count"] or 0) >= int(row["max_attempts"] or 1)
            status = "dead_letter" if exhausted else "queued"
            completed_at = now if exhausted else None
            self._conn.execute(
                """
                UPDATE email_mailbox_operations
                SET status=?, lease_owner=NULL, lease_expires_at=NULL,
                    next_attempt_at=?, last_error_code=?, updated_at=?, completed_at=?
                WHERE operation_id=?
                """,
                (status, next_attempt_at, str(error_code or "worker_error")[:120], now, completed_at, operation_id),
            )
            self._conn.commit()
            current = self._conn.execute(
                "SELECT * FROM email_mailbox_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        return self._decode_row(dict(current)) if current is not None else {}

    def fail_spam_operation(self, **kwargs: Any) -> dict[str, Any]:
        return self.fail_mailbox_operation(**kwargs)

    def get_mailbox_operation(self, *, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM email_mailbox_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        return self._decode_row(dict(row)) if row is not None else None

    def get_spam_operation(self, *, operation_id: str) -> dict[str, Any] | None:
        return self.get_mailbox_operation(operation_id=operation_id)

    def mailbox_verified_count_since(self, *, since: str) -> int:
        with self._lock:
            return int(
                self._conn.execute(
                    """
                    SELECT COUNT(*) FROM email_mailbox_operations
                    WHERE status='verified' AND completed_at>=?
                    """,
                    (since,),
                ).fetchone()[0]
            )

    def spam_verified_count_since(self, *, since: str) -> int:
        return self.mailbox_verified_count_since(since=since)

    def mailbox_started_count_since(self, *, since: str) -> int:
        """Count unique operations admitted to the provider-write lane in a rolling window."""
        with self._lock:
            return int(
                self._conn.execute(
                    """
                    SELECT COUNT(*) FROM email_mailbox_operations
                    WHERE first_claimed_at IS NOT NULL AND first_claimed_at>=?
                    """,
                    (since,),
                ).fetchone()[0]
            )

    def spam_started_count_since(self, *, since: str) -> int:
        return self.mailbox_started_count_since(since=since)

    def update_message_labels(
        self,
        *,
        gmail_message_id: str,
        label_ids: list[str],
        now: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE email_messages
                SET gmail_label_ids_json=?, last_seen_at=?
                WHERE gmail_message_id=?
                """,
                (json.dumps(sorted(set(label_ids))), now, gmail_message_id),
            )
            self._conn.commit()

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = self._conn.execute(
                "SELECT * FROM email_sync_state WHERE state_key='primary'"
            ).fetchone()
            message_count = int(self._conn.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0])
            review_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM email_classifications WHERE review_required=1"
                ).fetchone()[0]
            )
            failed_runs = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM email_sync_runs WHERE status IN ('failed','dead_letter')"
                ).fetchone()[0]
            )
            dead_message_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM email_sync_message_failures WHERE status='dead_letter'"
                ).fetchone()[0]
            )
            mailbox_queued_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM email_mailbox_operations WHERE status IN ('queued','claimed')"
                ).fetchone()[0]
            )
            mailbox_dead_letter_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM email_mailbox_operations WHERE status='dead_letter'"
                ).fetchone()[0]
            )
            label_queued_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM email_label_operations WHERE status IN ('queued','claimed')"
                ).fetchone()[0]
            )
            label_dead_letter_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM email_label_operations WHERE status='dead_letter'"
                ).fetchone()[0]
            )
            managed_label_queued_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM email_managed_label_operations "
                    "WHERE status IN ('queued','claimed')"
                ).fetchone()[0]
            )
            managed_label_dead_letter_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM email_managed_label_operations "
                    "WHERE status='dead_letter'"
                ).fetchone()[0]
            )
            managed_label_verified_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM email_managed_label_operations "
                    "WHERE status='verified'"
                ).fetchone()[0]
            )
            managed_label_parent_open_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM email_tool_operations "
                    "WHERE status IN ('reserved','queued')"
                ).fetchone()[0]
            )
            heartbeat_table = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_heartbeats'"
            ).fetchone()
            heartbeat = (
                self._conn.execute(
                    """
                    SELECT status, last_seen_at, last_error_code
                    FROM worker_heartbeats WHERE worker_type='email_operations'
                    ORDER BY last_seen_at DESC LIMIT 1
                    """
                ).fetchone()
                if heartbeat_table is not None
                else None
            )
        result = dict(state) if state is not None else {}
        result.update(
            {
                "message_count": message_count,
                "needs_review_count": review_count,
                "failed_run_count": failed_runs,
                "dead_letter_message_count": dead_message_count,
                "mailbox_queued_count": mailbox_queued_count,
                "mailbox_dead_letter_count": mailbox_dead_letter_count,
                "label_queued_count": label_queued_count,
                "label_dead_letter_count": label_dead_letter_count,
                "managed_label_queued_count": managed_label_queued_count,
                "managed_label_dead_letter_count": managed_label_dead_letter_count,
                "managed_label_verified_count": managed_label_verified_count,
                "managed_label_parent_open_count": managed_label_parent_open_count,
                "operations_worker_status": str(heartbeat["status"]) if heartbeat is not None else None,
                "operations_worker_last_seen_at": (
                    str(heartbeat["last_seen_at"]) if heartbeat is not None else None
                ),
                "operations_worker_last_error_code": (
                    str(heartbeat["last_error_code"] or "") or None
                    if heartbeat is not None
                    else None
                ),
                "spam_queued_count": mailbox_queued_count,
                "spam_dead_letter_count": mailbox_dead_letter_count,
            }
        )
        return result

    @staticmethod
    def _internal_date_iso(value: Any) -> str | None:
        try:
            instant = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None
        return instant.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
        decoded = dict(row)
        mapping = {
            "recipient_headers_json": "recipient_headers",
            "gmail_label_ids_json": "gmail_label_ids",
            "attachment_metadata_json": "attachment_metadata",
            "structured_summary_json": "structured_summary",
            "evidence_json": "evidence",
            "ordered_message_ids_json": "ordered_message_ids",
            "ordered_thread_ids_json": "ordered_thread_ids",
            "participant_summary_json": "participant_summary",
            "labels_before_json": "labels_before",
            "labels_after_json": "labels_after",
            "managed_label_refs_json": "managed_label_refs",
            "provider_labels_before_json": "provider_labels_before",
            "provider_labels_after_json": "provider_labels_after",
            "recovery_manifest_json": "recovery_manifest",
            "result_json": "result",
        }
        for source, target in mapping.items():
            if source not in decoded:
                continue
            try:
                decoded[target] = json.loads(str(decoded.get(source) or "null"))
            except (TypeError, json.JSONDecodeError):
                decoded[target] = [] if source.endswith("ids_json") else {}
        for key in ("review_required",):
            if key in decoded:
                decoded[key] = bool(decoded[key])
        return decoded
