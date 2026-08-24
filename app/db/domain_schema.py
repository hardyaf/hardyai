from __future__ import annotations

import sqlite3


class DomainSchemaMigrations:
    """Central schema authority for independently constructed domain repositories."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = _NoopLock()

    def apply_private_notes(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS private_note_digests (
                    digest_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    delivery_channel_id TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    timezone_name TEXT NOT NULL,
                    scheduled_for TEXT NOT NULL,
                    status TEXT NOT NULL,
                    note_count INTEGER NOT NULL DEFAULT 0,
                    summary_text TEXT,
                    discord_message_ids_json TEXT NOT NULL DEFAULT '[]',
                    delivery_attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(channel_id, local_date)
                );

                CREATE TABLE IF NOT EXISTS private_note_entries (
                    note_id TEXT PRIMARY KEY,
                    external_message_id TEXT NOT NULL UNIQUE,
                    owner_user_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    author_external_user_id TEXT NOT NULL,
                    author_display_name TEXT,
                    note_text TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    digest_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (digest_id) REFERENCES private_note_digests(digest_id)
                );

                CREATE INDEX IF NOT EXISTS idx_private_notes_pending
                    ON private_note_entries(owner_user_id, channel_id, status, captured_at);
                CREATE INDEX IF NOT EXISTS idx_private_notes_digest
                    ON private_note_entries(digest_id, captured_at);
                CREATE INDEX IF NOT EXISTS idx_private_digests_delivery
                    ON private_note_digests(status, scheduled_for);
                """
            )
            self._conn.commit()

    def apply_calendar_inbox(self) -> None:
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

    def apply_email_agent(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS email_sync_state (
                state_key TEXT PRIMARY KEY,
                activation_at TEXT NOT NULL,
                history_id TEXT NOT NULL,
                continuation_token TEXT,
                last_success_at TEXT,
                last_recovery_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS email_sync_runs (
                run_id TEXT PRIMARY KEY,
                bucket_key TEXT NOT NULL UNIQUE,
                run_kind TEXT NOT NULL CHECK (run_kind IN ('scheduled','on_demand','recovery')),
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                lease_owner TEXT,
                lease_expires_at TEXT,
                page_count INTEGER NOT NULL DEFAULT 0,
                candidate_count INTEGER NOT NULL DEFAULT 0,
                accepted_count INTEGER NOT NULL DEFAULT 0,
                ignored_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                summary_count INTEGER NOT NULL DEFAULT 0,
                classification_count INTEGER NOT NULL DEFAULT 0,
                last_error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS email_messages (
                gmail_message_id TEXT PRIMARY KEY,
                gmail_thread_id TEXT NOT NULL,
                rfc_message_id TEXT,
                source_route_key TEXT NOT NULL,
                gmail_history_id TEXT NOT NULL,
                internal_date INTEGER NOT NULL,
                sender_name TEXT,
                sender_email TEXT,
                recipient_headers_json TEXT NOT NULL DEFAULT '[]',
                subject TEXT NOT NULL,
                snippet TEXT NOT NULL,
                gmail_label_ids_json TEXT NOT NULL DEFAULT '[]',
                attachment_metadata_json TEXT NOT NULL DEFAULT '[]',
                canonical_body_hash TEXT NOT NULL,
                list_id TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                content_changed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS email_sync_message_failures (
                gmail_message_id TEXT PRIMARY KEY,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                last_error_code TEXT NOT NULL,
                first_failed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS email_threads (
                gmail_thread_id TEXT PRIMARY KEY,
                latest_message_id TEXT NOT NULL,
                latest_internal_date INTEGER NOT NULL,
                message_count INTEGER NOT NULL,
                participant_summary_json TEXT NOT NULL DEFAULT '[]',
                subject_normalized TEXT NOT NULL,
                thread_content_hash TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                FOREIGN KEY (latest_message_id) REFERENCES email_messages(gmail_message_id)
            );

            CREATE TABLE IF NOT EXISTS email_summaries (
                summary_id TEXT PRIMARY KEY,
                scope_type TEXT NOT NULL CHECK (scope_type IN ('message','thread','digest')),
                scope_id TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                summary_text TEXT NOT NULL,
                structured_summary_json TEXT NOT NULL DEFAULT '{}',
                model_provider TEXT NOT NULL,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                taxonomy_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(scope_type, scope_id, source_hash, prompt_version)
            );

            CREATE TABLE IF NOT EXISTS email_classifications (
                classification_id TEXT PRIMARY KEY,
                gmail_message_id TEXT NOT NULL,
                taxonomy_version TEXT NOT NULL,
                logical_category_key TEXT NOT NULL,
                audience TEXT NOT NULL DEFAULT 'shared' CHECK (audience = 'shared'),
                confidence REAL NOT NULL,
                decision_source TEXT NOT NULL CHECK (decision_source IN ('correction','rule','model','fallback')),
                evidence_json TEXT NOT NULL DEFAULT '{}',
                review_required INTEGER NOT NULL DEFAULT 1,
                corrected_by_user_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(gmail_message_id, taxonomy_version),
                FOREIGN KEY (gmail_message_id) REFERENCES email_messages(gmail_message_id)
            );

            CREATE TABLE IF NOT EXISTS email_user_state (
                user_id TEXT NOT NULL,
                discord_channel_id TEXT NOT NULL,
                gmail_message_id TEXT NOT NULL,
                review_state TEXT NOT NULL CHECK (
                    review_state IN ('new','presented','reviewed','dismissed','snoozed','actioned')
                ),
                disposition TEXT,
                snoozed_until TEXT,
                last_presented_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id, discord_channel_id, gmail_message_id),
                FOREIGN KEY (gmail_message_id) REFERENCES email_messages(gmail_message_id)
            );

            CREATE TABLE IF NOT EXISTS email_reference_sets (
                reference_set_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                discord_channel_id TEXT NOT NULL,
                query_text TEXT NOT NULL,
                ordered_message_ids_json TEXT NOT NULL,
                ordered_thread_ids_json TEXT NOT NULL,
                focused_message_id TEXT,
                focused_thread_id TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS email_action_links (
                action_link_id TEXT PRIMARY KEY,
                gmail_message_id TEXT,
                gmail_thread_id TEXT,
                user_id TEXT NOT NULL,
                target_capability TEXT NOT NULL,
                target_operation_id TEXT NOT NULL,
                target_resource_id TEXT,
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS email_label_operations (
                operation_id TEXT PRIMARY KEY,
                gmail_message_id TEXT NOT NULL,
                taxonomy_version TEXT NOT NULL,
                logical_category_key TEXT NOT NULL,
                gmail_label_id TEXT NOT NULL,
                gmail_label_name TEXT NOT NULL DEFAULT '',
                operation_type TEXT NOT NULL CHECK (operation_type IN ('add','remove_managed')),
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                lease_owner TEXT,
                lease_expires_at TEXT,
                first_claimed_at TEXT,
                next_attempt_at TEXT NOT NULL DEFAULT '',
                labels_before_json TEXT NOT NULL DEFAULT '[]',
                labels_after_json TEXT NOT NULL DEFAULT '[]',
                last_error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (gmail_message_id) REFERENCES email_messages(gmail_message_id)
            );

            CREATE TABLE IF NOT EXISTS email_spam_operations (
                operation_id TEXT PRIMARY KEY,
                gmail_message_id TEXT NOT NULL,
                taxonomy_version TEXT NOT NULL,
                requested_by_user_id TEXT NOT NULL,
                discord_channel_id TEXT NOT NULL,
                external_request_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                operation_type TEXT NOT NULL DEFAULT 'move_to_spam'
                    CHECK (operation_type = 'move_to_spam'),
                status TEXT NOT NULL
                    CHECK (status IN ('queued','claimed','verified','dead_letter','cancelled')),
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                lease_owner TEXT,
                lease_expires_at TEXT,
                first_claimed_at TEXT,
                next_attempt_at TEXT NOT NULL,
                labels_before_json TEXT NOT NULL DEFAULT '[]',
                labels_after_json TEXT NOT NULL DEFAULT '[]',
                last_error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (gmail_message_id) REFERENCES email_messages(gmail_message_id)
            );

            CREATE TABLE IF NOT EXISTS email_mailbox_operations (
                operation_id TEXT PRIMARY KEY,
                gmail_message_id TEXT NOT NULL,
                taxonomy_version TEXT NOT NULL,
                requested_by_user_id TEXT NOT NULL,
                discord_channel_id TEXT NOT NULL,
                external_request_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                operation_type TEXT NOT NULL
                    CHECK (operation_type IN ('move_to_spam','mark_read_complete')),
                status TEXT NOT NULL
                    CHECK (status IN ('queued','claimed','verified','dead_letter','cancelled')),
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                lease_owner TEXT,
                lease_expires_at TEXT,
                first_claimed_at TEXT,
                next_attempt_at TEXT NOT NULL,
                labels_before_json TEXT NOT NULL DEFAULT '[]',
                labels_after_json TEXT NOT NULL DEFAULT '[]',
                last_error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (gmail_message_id) REFERENCES email_messages(gmail_message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_email_sync_runs_status
                ON email_sync_runs(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_email_messages_internal_date
                ON email_messages(internal_date DESC);
            CREATE INDEX IF NOT EXISTS idx_email_messages_route
                ON email_messages(source_route_key, internal_date DESC);
            CREATE INDEX IF NOT EXISTS idx_email_messages_thread
                ON email_messages(gmail_thread_id, internal_date ASC);
            CREATE INDEX IF NOT EXISTS idx_email_sync_message_failures_status
                ON email_sync_message_failures(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_email_classifications_category
                ON email_classifications(taxonomy_version, logical_category_key, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_email_reference_sets_scope
                ON email_reference_sets(user_id, discord_channel_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_email_spam_operations_claim
                ON email_spam_operations(status, next_attempt_at, lease_expires_at, created_at);
            CREATE INDEX IF NOT EXISTS idx_email_mailbox_operations_claim
                ON email_mailbox_operations(status, next_attempt_at, lease_expires_at, created_at);
            """
        )
        user_state_columns = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(email_user_state)").fetchall()
        }
        if "disposition" not in user_state_columns:
            self._conn.execute("ALTER TABLE email_user_state ADD COLUMN disposition TEXT")
        label_columns = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(email_label_operations)").fetchall()
        }
        if "gmail_label_name" not in label_columns:
            self._conn.execute(
                "ALTER TABLE email_label_operations ADD COLUMN gmail_label_name TEXT NOT NULL DEFAULT ''"
            )
        if "first_claimed_at" not in label_columns:
            self._conn.execute(
                "ALTER TABLE email_label_operations ADD COLUMN first_claimed_at TEXT"
            )
        if "next_attempt_at" not in label_columns:
            self._conn.execute(
                "ALTER TABLE email_label_operations ADD COLUMN next_attempt_at TEXT NOT NULL DEFAULT ''"
            )
            self._conn.execute(
                "UPDATE email_label_operations SET next_attempt_at=created_at WHERE next_attempt_at=''"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_email_label_operations_claim "
            "ON email_label_operations(status, next_attempt_at, lease_expires_at, created_at)"
        )
        spam_columns = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(email_spam_operations)").fetchall()
        }
        if "first_claimed_at" not in spam_columns:
            self._conn.execute(
                "ALTER TABLE email_spam_operations ADD COLUMN first_claimed_at TEXT"
            )
        self._conn.execute(
            """
            INSERT OR IGNORE INTO email_mailbox_operations(
                operation_id, gmail_message_id, taxonomy_version,
                requested_by_user_id, discord_channel_id, external_request_id,
                idempotency_key, operation_type, status, attempt_count,
                max_attempts, lease_owner, lease_expires_at, first_claimed_at,
                next_attempt_at, labels_before_json, labels_after_json,
                last_error_code, created_at, updated_at, completed_at
            )
            SELECT operation_id, gmail_message_id, taxonomy_version,
                   requested_by_user_id, discord_channel_id, external_request_id,
                   idempotency_key, operation_type, status, attempt_count,
                   max_attempts, lease_owner, lease_expires_at, first_claimed_at,
                   next_attempt_at, labels_before_json, labels_after_json,
                   last_error_code, created_at, updated_at, completed_at
            FROM email_spam_operations
            """
        )
        self._conn.commit()


class _NoopLock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


def ensure_private_notes_schema(conn: sqlite3.Connection) -> None:
    DomainSchemaMigrations(conn).apply_private_notes()


def ensure_calendar_inbox_schema(conn: sqlite3.Connection) -> None:
    DomainSchemaMigrations(conn).apply_calendar_inbox()


def ensure_email_agent_schema(conn: sqlite3.Connection) -> None:
    DomainSchemaMigrations(conn).apply_email_agent()
