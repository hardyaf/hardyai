from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest

from app.skills.domains.email_agent.query import EmailQuery
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


def _stored_message(
    message_id: str,
    *,
    received_at: datetime,
    source: str = "work",
    sender: str = "person@example.edu",
    recipients: tuple[str, ...] = ("jarvis.house+work@example.com",),
    subject: str = "Budget review",
    snippet: str = "Please review the budget.",
    attachments: tuple[str, ...] = (),
) -> dict:
    record = message_record(message_id)
    record.update(
        {
            "gmail_thread_id": f"thread-{message_id}",
            "source_route_key": source,
            "internal_date": int(received_at.timestamp() * 1000),
            "sender_email": sender,
            "recipient_headers_json": json.dumps(list(recipients)),
            "subject": subject,
            "snippet": snippet,
            "attachment_metadata_json": json.dumps(
                [{"filename": name, "mime_type": "application/pdf"} for name in attachments]
            ),
        }
    )
    return record


def _typed_query(**overrides) -> EmailQuery:
    values = {
        "start": datetime(2026, 8, 20, 4, tzinfo=UTC),
        "end": datetime(2026, 8, 25, 4, tzinfo=UTC),
        "timezone_name": "America/New_York",
        "visibility": "all",
        "order": "oldest",
        "limit": 10,
    }
    values.update(overrides)
    return EmailQuery(**values)


def test_typed_query_uses_inclusive_start_exclusive_end_and_requested_order(tmp_path):
    storage = EmailAgentSQLiteStorage(str(tmp_path / "email.db"))
    start = datetime(2026, 8, 20, 4, tzinfo=UTC)
    middle = datetime(2026, 8, 22, 12, tzinfo=UTC)
    end = datetime(2026, 8, 25, 4, tzinfo=UTC)
    for message_id, received_at in (("start", start), ("middle", middle), ("end", end)):
        storage.upsert_message(
            record=_stored_message(message_id, received_at=received_at),
            now=NOW,
        )
        storage.store_classification(
            gmail_message_id=message_id,
            taxonomy_version="shared-v1",
            logical_category_key="work_mail",
            confidence=1.0,
            decision_source="rule",
            evidence={},
            review_required=False,
            corrected_by_user_id=None,
            now=NOW,
        )

    oldest = storage.query_messages(
        query=_typed_query(order="oldest"),
        taxonomy_version="shared-v1",
        user_id="jordan",
        discord_channel_id="100",
        allowed_source_keys=("work", "personal"),
        allowed_category_keys=("work_mail", "needs_review"),
        now=NOW,
    )
    newest = storage.query_messages(
        query=_typed_query(order="newest"),
        taxonomy_version="shared-v1",
        user_id="jordan",
        discord_channel_id="100",
        allowed_source_keys=("work", "personal"),
        allowed_category_keys=("work_mail", "needs_review"),
        now=NOW,
    )

    assert [row["gmail_message_id"] for row in oldest] == ["start", "middle"]
    assert [row["gmail_message_id"] for row in newest] == ["middle", "start"]
    storage.close()


def test_typed_query_combines_filters_and_fails_closed_for_invalid_allowlists(tmp_path):
    storage = EmailAgentSQLiteStorage(str(tmp_path / "email.db"))
    received_at = datetime(2026, 8, 22, 12, tzinfo=UTC)
    storage.upsert_message(
        record=_stored_message(
            "match",
            received_at=received_at,
            sender="boss@example.edu",
            recipients=("jarvis.house+work@example.com", "jordan@example.com"),
            subject="Quarterly budget review",
            snippet="Review the attached budget before Tuesday.",
            attachments=("budget.pdf",),
        ),
        now=NOW,
    )
    storage.store_classification(
        gmail_message_id="match",
        taxonomy_version="shared-v1",
        logical_category_key="work_mail",
        confidence=1.0,
        decision_source="rule",
        evidence={},
        review_required=False,
        corrected_by_user_id=None,
        now=NOW,
    )
    query = _typed_query(
        senders=("boss@example.edu",),
        recipients=("jordan@example.com",),
        source="work",
        category="work_mail",
        text="budget Tuesday",
        has_attachment=True,
    )

    rows = storage.query_messages(
        query=query,
        taxonomy_version="shared-v1",
        user_id="jordan",
        discord_channel_id="100",
        allowed_source_keys=("work", "personal"),
        allowed_category_keys=("work_mail", "needs_review"),
        now=NOW,
    )
    injection = storage.query_messages(
        query=_typed_query(text="' OR 1=1 --"),
        taxonomy_version="shared-v1",
        user_id="jordan",
        discord_channel_id="100",
        allowed_source_keys=("work", "personal"),
        allowed_category_keys=("work_mail", "needs_review"),
        now=NOW,
    )

    assert [row["gmail_message_id"] for row in rows] == ["match"]
    assert rows[0]["attachment_metadata"][0]["filename"] == "budget.pdf"
    assert injection == []
    with pytest.raises(ValueError, match="Unsupported email source"):
        storage.query_messages(
            query=_typed_query(source="unknown"),
            taxonomy_version="shared-v1",
            user_id="jordan",
            discord_channel_id="100",
            allowed_source_keys=("work", "personal"),
            allowed_category_keys=("work_mail", "needs_review"),
            now=NOW,
        )
    with pytest.raises(ValueError, match="allowlist"):
        storage.query_messages(
            query=query,
            taxonomy_version="shared-v1",
            user_id="jordan",
            discord_channel_id="100",
            allowed_source_keys=(),
            allowed_category_keys=("work_mail",),
            now=NOW,
        )
    storage.close()
