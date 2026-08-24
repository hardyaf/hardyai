from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.google.gmail_spam_writer import GmailSpamWriteResult
from app.skills.domains.email_agent.spam_worker import EmailSpamWorker, EmailSpamWorkerConfig
from app.skills.domains.email_agent.storage import EmailAgentSQLiteStorage
from tests.unit.test_email_agent_storage import message_record


NOW = datetime(2026, 8, 16, 14, 0, tzinfo=timezone.utc)


class SuccessfulWriter:
    def __init__(self):
        self.profile_calls = 0
        self.write_calls = []

    def verify_profile(self):
        self.profile_calls += 1

    def move_to_spam(self, *, message_id, operation_id):
        self.write_calls.append((message_id, operation_id))
        return GmailSpamWriteResult(
            message_id=message_id,
            labels_before=("INBOX", "UNREAD"),
            labels_after=("SPAM", "UNREAD"),
            provider_modified=True,
            verified=True,
        )

    def mark_read_complete(self, *, message_id, operation_id):
        self.write_calls.append((message_id, operation_id))
        return GmailSpamWriteResult(
            message_id=message_id,
            labels_before=("INBOX", "UNREAD"),
            labels_after=("INBOX",),
            provider_modified=True,
            verified=True,
        )

    def apply_managed_category(
        self,
        *,
        message_id,
        operation_id,
        label_name,
        managed_label_names,
    ):
        self.write_calls.append((message_id, operation_id, label_name, managed_label_names))
        return GmailSpamWriteResult(
            message_id=message_id,
            labels_before=("INBOX", "Label_Old"),
            labels_after=("INBOX", "Label_SPORTS"),
            provider_modified=True,
            verified=True,
            gmail_label_id="Label_SPORTS",
        )


class FailingWriter(SuccessfulWriter):
    def move_to_spam(self, *, message_id, operation_id):
        self.write_calls.append((message_id, operation_id))
        raise RuntimeError("provider unavailable")


def _enqueue(storage, *, request_id="discord:1", now=NOW):
    storage.upsert_message(record=message_record(), now=now.isoformat())
    return storage.enqueue_spam_operation(
        gmail_message_id="m1",
        taxonomy_version="shared-v1",
        requested_by_user_id="jordan",
        discord_channel_id="100",
        external_request_id=request_id,
        idempotency_key=f"spam:{request_id}:m1",
        max_attempts=3,
        now=now.isoformat(),
    )


def test_spam_worker_verifies_provider_then_updates_local_category_and_state(tmp_path):
    storage = EmailAgentSQLiteStorage(str(tmp_path / "email.db"))
    operation = _enqueue(storage)
    writer = SuccessfulWriter()
    worker = EmailSpamWorker(
        storage=storage,
        writer=writer,
        config=EmailSpamWorkerConfig(enabled=True),
        worker_id="worker-1",
    )

    result = worker.run_once(now=NOW)

    assert result["status"] == "ok"
    assert result["claimed_count"] == 1
    assert result["verified_count"] == 1
    assert result["failed_count"] == 0
    assert result["dead_letter_count"] == 0
    stored = storage.get_spam_operation(operation_id=operation["operation_id"])
    assert stored["status"] == "verified"
    assert stored["labels_before"] == ["INBOX", "UNREAD"]
    assert stored["labels_after"] == ["SPAM", "UNREAD"]
    message = storage.get_message(gmail_message_id="m1", taxonomy_version="shared-v1")
    assert message["logical_category_key"] == "spam"
    assert message["decision_source"] == "correction"
    assert message["gmail_label_ids"] == ["SPAM", "UNREAD"]
    assert writer.profile_calls == 1
    storage.close()


def test_spam_worker_retries_with_backoff_then_dead_letters(tmp_path):
    storage = EmailAgentSQLiteStorage(str(tmp_path / "email.db"))
    operation = _enqueue(storage)
    worker = EmailSpamWorker(
        storage=storage,
        writer=FailingWriter(),
        config=EmailSpamWorkerConfig(enabled=True),
        worker_id="worker-1",
    )

    first = worker.run_once(now=NOW)
    second = worker.run_once(now=NOW + timedelta(seconds=31))
    third = worker.run_once(now=NOW + timedelta(seconds=92))

    assert first["failed_count"] == 1
    assert second["failed_count"] == 1
    assert third["dead_letter_count"] == 1
    stored = storage.get_spam_operation(operation_id=operation["operation_id"])
    assert stored["status"] == "dead_letter"
    assert stored["attempt_count"] == 3
    storage.close()


def test_spam_worker_honors_rolling_hourly_cap_before_claim(tmp_path):
    storage = EmailAgentSQLiteStorage(str(tmp_path / "email.db"))
    first = _enqueue(storage, request_id="discord:1")
    writer = SuccessfulWriter()
    worker = EmailSpamWorker(
        storage=storage,
        writer=writer,
        config=EmailSpamWorkerConfig(
            enabled=True,
            max_writes_per_hour=1,
            max_writes_per_day=2,
        ),
        worker_id="worker-1",
    )
    assert worker.run_once(now=NOW)["verified_count"] == 1

    storage.upsert_message(record=message_record("m2"), now=(NOW + timedelta(minutes=1)).isoformat())
    storage.enqueue_spam_operation(
        gmail_message_id="m2",
        taxonomy_version="shared-v1",
        requested_by_user_id="jordan",
        discord_channel_id="100",
        external_request_id="discord:2",
        idempotency_key="spam:discord:2:m2",
        max_attempts=3,
        now=(NOW + timedelta(minutes=1)).isoformat(),
    )
    limited = worker.run_once(now=NOW + timedelta(minutes=1))

    assert limited["status"] == "rate_limited"
    assert storage.get_spam_operation(operation_id=first["operation_id"])["status"] == "verified"
    assert storage.status()["spam_queued_count"] == 1
    storage.close()


def test_spam_worker_counts_failed_first_claim_toward_safety_cap(tmp_path):
    storage = EmailAgentSQLiteStorage(str(tmp_path / "email.db"))
    first = _enqueue(storage, request_id="discord:1")
    worker = EmailSpamWorker(
        storage=storage,
        writer=FailingWriter(),
        config=EmailSpamWorkerConfig(
            enabled=True,
            max_writes_per_hour=1,
            max_writes_per_day=2,
        ),
        worker_id="worker-1",
    )

    assert worker.run_once(now=NOW)["failed_count"] == 1
    limited = worker.run_once(now=NOW + timedelta(seconds=31))

    assert limited["status"] == "rate_limited"
    assert limited["started_last_hour"] == 1
    assert storage.get_spam_operation(operation_id=first["operation_id"])["status"] == "queued"
    storage.close()


def test_mailbox_worker_marks_read_then_completes_local_disposition(tmp_path):
    storage = EmailAgentSQLiteStorage(str(tmp_path / "email.db"))
    storage.upsert_message(record=message_record(), now=NOW.isoformat())
    operation = storage.enqueue_mailbox_operation(
        operation_type="mark_read_complete",
        gmail_message_id="m1",
        taxonomy_version="shared-v1",
        requested_by_user_id="jordan",
        discord_channel_id="100",
        external_request_id="discord:read-1",
        idempotency_key="complete:discord:read-1:m1",
        max_attempts=3,
        now=NOW.isoformat(),
    )
    writer = SuccessfulWriter()
    worker = EmailSpamWorker(
        storage=storage,
        writer=writer,
        config=EmailSpamWorkerConfig(enabled=True),
        worker_id="worker-1",
    )

    result = worker.run_once(now=NOW)
    completed = storage.list_messages(
        taxonomy_version="shared-v1",
        limit=5,
        user_id="jordan",
        discord_channel_id="100",
        visibility="completed",
        now=NOW.isoformat(),
    )

    assert result["verified_count"] == 1
    assert storage.get_mailbox_operation(operation_id=operation["operation_id"])["status"] == "verified"
    assert completed[0]["user_disposition"] == "complete"
    assert completed[0]["gmail_label_ids"] == ["INBOX"]
    storage.close()


def test_mailbox_worker_applies_verified_allowlisted_managed_category(tmp_path):
    storage = EmailAgentSQLiteStorage(str(tmp_path / "email.db"))
    storage.upsert_message(record=message_record(), now=NOW.isoformat())
    operation = storage.enqueue_label_operation(
        gmail_message_id="m1",
        taxonomy_version="shared-v1",
        logical_category_key="community_sports",
        gmail_label_name="Jarvis/Community Sports",
        operation_type="add",
        idempotency_key="label:m1:community_sports:1",
        max_attempts=3,
        now=NOW.isoformat(),
    )
    writer = SuccessfulWriter()
    worker = EmailSpamWorker(
        storage=storage,
        writer=writer,
        config=EmailSpamWorkerConfig(enabled=False, label_writes_enabled=True),
        managed_label_names={
            "bills": "Jarvis/Bills",
            "community_sports": "Jarvis/Community Sports",
        },
        worker_id="worker-1",
    )

    result = worker.run_once(now=NOW)
    stored = storage.get_label_operation(operation_id=operation["operation_id"])

    assert result["label_verified_count"] == 1
    assert stored["status"] == "verified"
    assert stored["gmail_label_id"] == "Label_SPORTS"
    assert writer.write_calls == [
        ("m1", operation["operation_id"], "Jarvis/Community Sports", ("Jarvis/Bills", "Jarvis/Community Sports"))
    ]
    message = storage.get_message(gmail_message_id="m1", taxonomy_version="shared-v1")
    assert message["gmail_label_ids"] == ["INBOX", "Label_SPORTS"]
    storage.close()
