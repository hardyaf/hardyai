from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from app.db.connection import open_sqlite_connection
from app.db.domain_schema import ensure_email_agent_schema


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EmailAgentSQLiteStorage:
    """Domain-owned email metadata store. Raw message bodies are never persisted."""

    def __init__(self, database_path: str) -> None:
        _, self._conn = open_sqlite_connection(database_path)
        self._lock = RLock()
        with self._lock:
            ensure_email_agent_schema(self._conn)


    def close(self) -> None:
        with self._lock:
            self._conn.close()

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

    def get_thread(self, *, gmail_thread_id: str, taxonomy_version: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
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
                WHERE m.gmail_thread_id=?
                ORDER BY m.internal_date ASC, m.gmail_message_id ASC LIMIT ?
                """,
                (taxonomy_version, gmail_thread_id, max(1, min(int(limit), 100))),
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
                    " ".join(str(query_text or "").split())[:500],
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
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id, discord_channel_id, now),
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
                rows = self._conn.execute(
                    """
                    SELECT * FROM email_mailbox_operations
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
        return self.claim_mailbox_operations(**kwargs)

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
                "spam_queued_count": mailbox_queued_count,
                "spam_dead_letter_count": mailbox_dead_letter_count,
            }
        )
        return result

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
