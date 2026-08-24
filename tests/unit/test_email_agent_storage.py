from __future__ import annotations

import sqlite3

from app.skills.domains.email_agent.storage import EmailAgentSQLiteStorage


NOW = "2026-08-16T14:00:00+00:00"
LATER = "2026-08-16T15:00:00+00:00"


def message_record(message_id: str = "m1") -> dict:
    return {
        "gmail_message_id": message_id,
        "gmail_thread_id": "t1",
        "rfc_message_id": f"<{message_id}@example.test>",
        "source_route_key": "work",
        "gmail_history_id": "11",
        "internal_date": 1786900000000,
        "sender_name": "WORK Person",
        "sender_email": "person@example.edu",
        "recipient_headers_json": '["jarvis.house+work@example.com"]',
        "subject": "Budget review",
        "snippet": "Please review the budget.",
        "gmail_label_ids_json": '["INBOX"]',
        "attachment_metadata_json": "[]",
        "canonical_body_hash": "abc123",
        "list_id": None,
    }


def test_schema_never_persists_raw_body_and_sync_bucket_is_idempotent(tmp_path):
    database = tmp_path / "email.db"
    storage = EmailAgentSQLiteStorage(str(database))
    storage.activate(now=NOW, history_id="10")
    claim = storage.claim_sync_run(
        bucket_key="scheduled:1",
        run_kind="scheduled",
        lease_owner="worker",
        now=NOW,
        lease_expires_at=LATER,
        stale_before=NOW,
        max_attempts=3,
    )
    assert claim["claimed"] is True
    storage.complete_sync_run(
        run_id=claim["run_id"],
        counts={},
        now=NOW,
        history_id="11",
        continuation_token=None,
    )
    duplicate = storage.claim_sync_run(
        bucket_key="scheduled:1",
        run_kind="scheduled",
        lease_owner="worker-2",
        now=LATER,
        lease_expires_at="2026-08-16T16:00:00+00:00",
        stale_before=NOW,
        max_attempts=3,
    )
    storage.close()

    assert duplicate["claimed"] is False
    assert duplicate["reason"] == "completed"
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(email_messages)").fetchall()
        }
    assert "raw_body" not in columns
    assert "body_text" not in columns


def test_reference_sets_are_channel_scoped_and_expire(tmp_path):
    storage = EmailAgentSQLiteStorage(str(tmp_path / "email.db"))
    storage.upsert_message(record=message_record(), now=NOW)
    storage.create_reference_set(
        user_id="jordan",
        discord_channel_id="100",
        query_text="recent",
        message_ids=["m1"],
        thread_ids=["t1"],
        focused_message_id="m1",
        focused_thread_id="t1",
        created_at=NOW,
        expires_at=LATER,
    )

    assert storage.resolve_reference(
        user_id="jordan", discord_channel_id="100", reference="E1", now=NOW
    )["gmail_message_id"] == "m1"
    assert storage.resolve_reference(
        user_id="jordan", discord_channel_id="999", reference="E1", now=NOW
    ) is None
    assert storage.resolve_reference(
        user_id="jordan", discord_channel_id="100", reference="E1", now="2026-08-16T16:00:00+00:00"
    ) is None
    storage.close()


def test_explicit_category_correction_wins_over_later_automatic_proposal(tmp_path):
    storage = EmailAgentSQLiteStorage(str(tmp_path / "email.db"))
    storage.upsert_message(record=message_record(), now=NOW)
    storage.store_classification(
        gmail_message_id="m1",
        taxonomy_version="shared-v1",
        logical_category_key="work_mail",
        confidence=1.0,
        decision_source="correction",
        evidence={"explicit": True},
        review_required=False,
        corrected_by_user_id="jordan",
        now=NOW,
    )
    stored = storage.store_classification(
        gmail_message_id="m1",
        taxonomy_version="shared-v1",
        logical_category_key="needs_review",
        confidence=0.0,
        decision_source="fallback",
        evidence={"automatic": True},
        review_required=True,
        corrected_by_user_id=None,
        now=LATER,
    )

    assert stored["logical_category_key"] == "work_mail"
    assert stored["decision_source"] == "correction"
    assert stored["review_required"] is False
    storage.close()

