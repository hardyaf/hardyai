from __future__ import annotations

import sqlite3

import pytest

from app.db.domain_schema import ensure_email_agent_schema
from app.db.migrations import initialize_schema
from app.skills.domains.email_agent.storage import EmailAgentSQLiteStorage


def test_fresh_email_storage_uses_current_core_schema_authority(tmp_path):
    path = tmp_path / "email.db"
    storage = EmailAgentSQLiteStorage(str(path))
    storage.close()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 15
        parent_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(email_tool_operations)")
        }
        child_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(email_mailbox_operations)")
        }
        assert {"idempotency_key", "operation_identity_hash", "parent_manifest_hash"} <= parent_columns
        assert {"parent_operation_id", "parent_manifest_hash", "child_index", "arguments_hash"} <= child_columns
        before = connection.total_changes
        ensure_email_agent_schema(connection)
        assert connection.total_changes == before


def test_email_mailbox_grouping_guard_preserves_legacy_null_rows(tmp_path):
    path = tmp_path / "email.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        initialize_schema(connection)
        connection.execute(
            """
            INSERT INTO email_messages(
                gmail_message_id, gmail_thread_id, source_route_key, gmail_history_id,
                internal_date, sender_name, sender_email, recipient_headers_json,
                subject, snippet, gmail_label_ids_json, attachment_metadata_json,
                canonical_body_hash, first_seen_at, last_seen_at
            ) VALUES ('m1','t1','work','1',1,'Sender','sender@example.com','[]',
                      'Subject','Snippet','[]','[]','hash','now','now')
            """
        )
        connection.execute(
            """
            INSERT INTO email_mailbox_operations(
                operation_id, gmail_message_id, taxonomy_version,
                requested_by_user_id, discord_channel_id, external_request_id,
                idempotency_key, operation_type, status, attempt_count,
                max_attempts, next_attempt_at, created_at, updated_at
            ) VALUES ('legacy','m1','v1','operator','100','request','legacy-key',
                      'move_to_spam','queued',0,3,'now','now','now')
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="email_mailbox_grouping_invalid"):
            connection.execute(
                """
                INSERT INTO email_mailbox_operations(
                    operation_id, gmail_message_id, taxonomy_version,
                    requested_by_user_id, discord_channel_id, external_request_id,
                    idempotency_key, operation_type, status, attempt_count,
                    max_attempts, next_attempt_at, created_at, updated_at,
                    parent_operation_id
                ) VALUES ('bad','m1','v1','operator','100','request','bad-key',
                          'move_to_spam','queued',0,3,'now','now','now','parent')
                """
            )
        row = connection.execute(
            "SELECT parent_operation_id, parent_manifest_hash, child_index, arguments_hash "
            "FROM email_mailbox_operations WHERE operation_id='legacy'"
        ).fetchone()
        assert tuple(row) == (None, None, None, None)
    finally:
        connection.close()
