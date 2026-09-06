from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
import pytest

from app.services.google.gmail_spam_writer import (
    GmailManagedLabelWriteResult,
    GmailSpamWriteResult,
)
from app.core.tool_loop_types import validate_descriptor_payload
from app.skills.domains.email_agent.catalog import EmailCatalogService
from app.skills.domains.email_agent.config import EmailAgentPermissions
from app.skills.domains.email_agent.operations import EmailManagedLabelToolExecutor
from app.skills.domains.email_agent.storage import EmailAgentSQLiteStorage
from app.skills.tool_contracts import ToolCallEnvelope, compile_tool_descriptors
from app.workers.email_operations_worker import (
    EmailOperationsWorker,
    EmailOperationsWorkerConfig,
)


NOW = datetime(2026, 8, 31, 16, 0, tzinfo=UTC)


def _permissions() -> EmailAgentPermissions:
    return EmailAgentPermissions.from_mapping(
        {
            "version": 2,
            "gmail_profile": "jarvis@example.com",
            "google_account_key": "house",
            "taxonomy_version": "shared-v1",
            "source_routes": [
                {
                    "route_key": "work",
                    "display_name": "Work",
                    "source_mailbox": "source@example.edu",
                    "destination_alias": "jarvis+work@example.com",
                }
            ],
            "categories": [
                {"key": "needs_review", "display_name": "Needs Review", "audience": "shared"}
            ],
            "managed_labels": [
                {"key": "done", "display_name": "Done", "gmail_label_name": "Jarvis/Done"},
                {"key": "bills", "display_name": "Bills", "gmail_label_name": "Jarvis/Bills"},
            ],
            "access": [
                {
                    "user_id": "operator",
                    "discord_channel_id": "100",
                    "external_user_id": "42",
                    "agent_ids": ["jarvis"],
                    "audiences": ["shared"],
                    "enabled": True,
                }
            ],
        }
    )


def _descriptors():
    text = Path("app/prompts/skills/email_agent_skill.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    descriptors, diagnostics = compile_tool_descriptors(
        skill_id="skill.email.agent",
        contract_version=frontmatter["main_tools_contract_version"],
        declarations=frontmatter["main_tools"],
    )
    assert diagnostics == ()
    return {item.tool_id: item for item in descriptors}


def _record(message_id: str) -> dict:
    return {
        "gmail_message_id": message_id,
        "gmail_thread_id": f"thread-{message_id}",
        "rfc_message_id": None,
        "source_route_key": "work",
        "gmail_history_id": "1",
        "internal_date": int(NOW.timestamp() * 1000),
        "sender_name": "Sender",
        "sender_email": "sender@example.net",
        "recipient_headers_json": "[]",
        "subject": "Canary",
        "snippet": "Disposable test message",
        "gmail_label_ids_json": '["INBOX","UNREAD"]',
        "attachment_metadata_json": "[]",
        "canonical_body_hash": message_id,
        "list_id": None,
    }


def _context() -> dict:
    return {
        "source_interface": "discord",
        "identity_bound": True,
        "requested_by_user_id": "operator",
        "discord_channel_id": "100",
        "external_user_id": "42",
        "agent_id": "jarvis",
    }


def _envelope(
    executor,
    *,
    tool_id: str,
    arguments: dict,
    ordinal: int = 1,
    principal_subject: str = "42",
    external_user_id: str = "42",
):
    descriptor = _descriptors()[tool_id]
    validated = descriptor.validate_arguments(arguments)
    canonical = executor.canonicalize(
        tool_id=tool_id,
        validated_arguments=validated,
        request_context=_context(),
    )
    return ToolCallEnvelope.create(
        root_request_id=f"request-{tool_id}-{ordinal}",
        call_ordinal=ordinal,
        session_id="session-1",
        principal_kind="discord_adapter",
        principal_subject=principal_subject,
        external_user_id=external_user_id,
        user_id="operator",
        agent_id="jarvis",
        source_interface="discord",
        channel_scope="100",
        skill_id="skill.email.agent",
        descriptor=descriptor,
        authorization_snapshot_ref="authz-1",
        validated_arguments=canonical,
    )


def test_envelope_reauthorization_keeps_adapter_and_external_user_distinct(tmp_path):
    _, storage, executor = _setup(tmp_path)
    label_ref = EmailCatalogService.label_ref("done")
    authorized = _envelope(
        executor,
        tool_id="email.apply_labels",
        arguments={"message_refs": ["E1"], "label_refs": [label_ref]},
        principal_subject="embedded-discord-adapter",
        external_user_id="42",
    )
    changed_user = _envelope(
        executor,
        tool_id="email.apply_labels",
        arguments={"message_refs": ["E1"], "label_refs": [label_ref]},
        ordinal=2,
        principal_subject="embedded-discord-adapter",
        external_user_id="99",
    )

    assert executor.execute(envelope=authorized)["status"] == "queued"
    denied = executor.execute(envelope=changed_user)
    assert denied["status"] == "policy_denied"
    assert denied["denial_reason"] == "email_tool_scope_changed"
    storage.close()


class FakeWriter:
    def __init__(self, *, fail_message_id: str | None = None) -> None:
        self.fail_message_id = fail_message_id
        self.calls: list[dict] = []
        self.system_calls: list[dict] = []

    def verify_profile(self) -> None:
        return None

    def mutate_managed_labels(self, **kwargs):
        self.calls.append(dict(kwargs))
        if kwargs["message_id"] == self.fail_message_id:
            raise TimeoutError("provider timeout")
        after = ("INBOX", "UNREAD", "Label_Done")
        return GmailManagedLabelWriteResult(
            message_id=kwargs["message_id"],
            labels_before=("INBOX", "UNREAD"),
            labels_after=after,
            provider_modified=True,
            verified=True,
            managed_label_ids=(("Jarvis/Done", "Label_Done"),),
        )

    def mutate_system_label(self, **kwargs):
        self.system_calls.append(dict(kwargs))
        before = {"INBOX", "UNREAD", "STARRED"}
        after = set(before)
        if kwargs["action"] == "apply":
            after.add(kwargs["system_label"])
        else:
            after.discard(kwargs["system_label"])
        return GmailSpamWriteResult(
            message_id=kwargs["message_id"],
            labels_before=tuple(sorted(before)),
            labels_after=tuple(sorted(after)),
            provider_modified=before != after,
            verified=True,
        )


def _setup(tmp_path, *, max_attempts: int = 4):
    permissions = _permissions()
    storage = EmailAgentSQLiteStorage(str(tmp_path / "email.db"))
    for message_id in ("m1", "m2"):
        storage.upsert_message(record=_record(message_id), now=NOW.isoformat())
    storage.create_reference_set(
        user_id="operator",
        discord_channel_id="100",
        query_text="fixture",
        message_ids=["m1", "m2"],
        thread_ids=["thread-m1", "thread-m2"],
        focused_message_id="m1",
        focused_thread_id="thread-m1",
        created_at=NOW.isoformat(),
        expires_at=(NOW + timedelta(hours=1)).isoformat(),
    )
    executor = EmailManagedLabelToolExecutor(
        storage=storage,
        permissions=permissions,
        max_attempts=max_attempts,
        utc_clock=lambda: NOW,
    )
    return permissions, storage, executor


def test_atomic_reservation_replay_and_verified_worker_completion(tmp_path):
    permissions, storage, executor = _setup(tmp_path)
    label_ref = EmailCatalogService.label_ref("done")
    envelope = _envelope(
        executor,
        tool_id="email.apply_labels",
        arguments={"message_refs": ["E2", "E1"], "label_refs": [label_ref]},
    )

    queued = executor.execute(envelope=envelope)
    replay = executor.execute(envelope=envelope)
    writer = FakeWriter()
    worker = EmailOperationsWorker(
        storage=storage,
        writer=writer,
        permissions=permissions,
        config=EmailOperationsWorkerConfig(enabled=True),
        worker_id="worker-1",
    )
    result = worker.run_once(now=NOW)
    operation = storage.get_managed_label_operation(
        operation_id=envelope.operation_id,
        owner_user_id="operator",
        discord_channel_id="100",
    )

    assert queued["status"] == "queued"
    assert queued["payload"]["child_count"] == 2
    assert replay["payload"]["idempotent_replay"] is True
    assert result["verified_count"] == 2
    assert operation["status"] == "completed"
    assert operation["child_counts"] == {"verified": 2}
    assert [call["message_id"] for call in writer.calls] == ["m1", "m2"]
    assert storage.managed_labels_for_messages(gmail_message_ids=["m1"])["m1"][0][
        "display_name"
    ] == "Done"
    storage.close()


@pytest.mark.parametrize(
    ("tool_id", "arguments", "expected_action", "expected_label"),
    [
        ("email.set_read_state", {"message_refs": ["E1"], "state": "read"}, "remove", "UNREAD"),
        ("email.set_read_state", {"message_refs": ["E1"], "state": "unread"}, "apply", "UNREAD"),
        ("email.archive_messages", {"message_refs": ["E1"]}, "remove", "INBOX"),
        ("email.restore_to_inbox", {"message_refs": ["E1"]}, "apply", "INBOX"),
    ],
)
def test_reversible_mailbox_tools_use_the_existing_operation_ledger_and_worker(
    tmp_path,
    tool_id,
    arguments,
    expected_action,
    expected_label,
):
    permissions, storage, executor = _setup(tmp_path)
    envelope = _envelope(executor, tool_id=tool_id, arguments=arguments)

    queued = executor.execute(envelope=envelope)
    replay = executor.execute(envelope=envelope)
    writer = FakeWriter()
    worker = EmailOperationsWorker(
        storage=storage,
        writer=writer,
        permissions=permissions,
        config=EmailOperationsWorkerConfig(enabled=True),
        worker_id="worker-system-label",
    )
    result = worker.run_once(now=NOW)
    with storage._lock:
        child = storage._conn.execute(
            "SELECT child_operation_id, action, managed_label_refs_json "
            "FROM email_managed_label_operations "
            "WHERE parent_operation_id=?",
            (envelope.operation_id,),
        ).fetchone()

    assert queued["status"] == "queued"
    assert replay["payload"]["idempotent_replay"] is True
    assert result["verified_count"] == 1
    assert child["action"] == expected_action
    assert writer.system_calls == [
        {
            "message_id": "m1",
            "operation_id": child["child_operation_id"],
            "action": expected_action,
            "system_label": expected_label,
        }
    ]
    assert storage.get_managed_label_operation(
        operation_id=envelope.operation_id,
        owner_user_id="operator",
        discord_channel_id="100",
    )["status"] == "completed"
    storage.close()


def test_mixed_child_outcomes_are_partial_and_legacy_rows_are_never_claimed(tmp_path):
    permissions, storage, executor = _setup(tmp_path, max_attempts=1)
    legacy_id = "legacy-op"
    legacy = storage.enqueue_label_operation(
        gmail_message_id="m1",
        taxonomy_version="shared-v1",
        logical_category_key="needs_review",
        gmail_label_name="Jarvis/Needs Review",
        operation_type="add",
        idempotency_key="legacy-key",
        now=NOW.isoformat(),
        max_attempts=3,
    )
    legacy_id = legacy["operation_id"]
    envelope = _envelope(
        executor,
        tool_id="email.apply_labels",
        arguments={
            "message_refs": ["E1", "E2"],
            "label_refs": [EmailCatalogService.label_ref("done")],
        },
    )
    executor.execute(envelope=envelope)
    worker = EmailOperationsWorker(
        storage=storage,
        writer=FakeWriter(fail_message_id="m2"),
        permissions=permissions,
        config=EmailOperationsWorkerConfig(enabled=True),
        worker_id="worker-2",
    )

    result = worker.run_once(now=NOW)
    operation = storage.get_managed_label_operation(
        operation_id=envelope.operation_id,
        owner_user_id="operator",
        discord_channel_id="100",
    )
    legacy = storage.get_label_operation(operation_id=legacy_id)

    assert result["verified_count"] == 1
    assert result["dead_letter_count"] == 1
    assert operation["status"] == "partial"
    assert operation["child_counts"] == {"dead_letter": 1, "verified": 1}
    assert legacy["status"] == "queued"
    storage.close()


def test_reserved_parent_reconstructs_missing_children_before_claim(tmp_path):
    _, storage, executor = _setup(tmp_path, max_attempts=3)
    envelope = _envelope(
        executor,
        tool_id="email.apply_labels",
        arguments={
            "message_refs": ["E1", "E2"],
            "label_refs": [EmailCatalogService.label_ref("done")],
        },
    )
    executor.execute(envelope=envelope)
    with storage._lock:
        storage._conn.execute(
            "UPDATE email_tool_operations SET status='reserved' WHERE operation_id=?",
            (envelope.operation_id,),
        )
        storage._conn.execute(
            "DELETE FROM email_managed_label_operations "
            "WHERE parent_operation_id=? AND child_index=2",
            (envelope.operation_id,),
        )
        storage._conn.commit()

    recovered = storage.recover_reserved_managed_label_operations(now=NOW.isoformat())
    with storage._lock:
        rows = storage._conn.execute(
            "SELECT child_index, status, max_attempts, managed_label_refs_json "
            "FROM email_managed_label_operations WHERE parent_operation_id=? "
            "ORDER BY child_index",
            (envelope.operation_id,),
        ).fetchall()

    assert recovered == {"recovered_count": 1, "failed_count": 0}
    assert [(row["child_index"], row["status"], row["max_attempts"]) for row in rows] == [
        (1, "queued", 3),
        (2, "queued", 3),
    ]
    assert json.loads(rows[1]["managed_label_refs_json"]) == [
        EmailCatalogService.label_ref("done")
    ]
    storage.close()


def test_stale_label_and_message_refs_return_replanning_observations(tmp_path):
    _, storage, executor = _setup(tmp_path)
    stale_label = "label_v1_" + ("f" * 24)
    label_envelope = _envelope(
        executor,
        tool_id="email.apply_labels",
        arguments={"message_refs": ["E1"], "label_refs": [stale_label]},
    )
    message_envelope = _envelope(
        executor,
        tool_id="email.apply_labels",
        arguments={
            "message_refs": ["E3"],
            "label_refs": [EmailCatalogService.label_ref("done")],
        },
    )

    stale_label_result = executor.execute(envelope=label_envelope)
    stale_message_result = executor.execute(envelope=message_envelope)

    assert stale_label_result["status"] == "needs_input"
    assert stale_label_result["missing_fields"] == ["label_refs"]
    assert "operation_ref" not in stale_label_result["payload"]
    validate_descriptor_payload(
        _descriptors()["email.apply_labels"],
        stale_label_result["payload"],
        observation=True,
    )
    assert stale_message_result["status"] == "needs_input"
    assert stale_message_result["missing_fields"] == ["message_refs"]
    assert storage.get_managed_label_operation(
        operation_id=label_envelope.operation_id,
        owner_user_id="operator",
        discord_channel_id="100",
    ) is None
    storage.close()
