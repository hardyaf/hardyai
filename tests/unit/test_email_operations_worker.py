from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from app.services.google.gmail_spam_writer import GmailManagedLabelWriteResult
from app.skills.domains.email_agent.catalog import EmailCatalogService
from app.skills.domains.email_agent.storage import inspect_email_operations_worker_database
from app.workers.email_operations_worker import (
    EmailOperationsWorker,
    EmailOperationsWorkerConfig,
)
from tests.unit.test_email_agent_operations import NOW, _envelope, _setup


def test_compose_mounts_modify_token_directory_for_atomic_oauth_refresh():
    compose = (
        Path(__file__).resolve().parents[2] / "deploy" / "docker" / "compose.yaml"
    ).read_text(encoding="utf-8")
    directory_mount = (
        "../../secrets/email-spam-worker:/opt/jarvis/secrets/email-spam-worker"
    )

    assert compose.count(directory_mount) == 2
    assert (
        "../../secrets/email-spam-worker/token.json:"
        "/opt/jarvis/secrets/email-spam-worker/token.json"
    ) not in compose


def test_compose_keeps_managed_label_writes_default_off_and_process_scoped():
    compose = (
        Path(__file__).resolve().parents[2] / "deploy" / "docker" / "compose.yaml"
    ).read_text(encoding="utf-8")

    assert compose.count(
        'EMAIL_AGENT_LABEL_WRITES_ENABLED: "${EMAIL_AGENT_LABEL_WRITES_ENABLED:-false}"'
    ) == 2
    assert compose.count('EMAIL_AGENT_LABEL_WRITES_ENABLED: "false"') == 1
    assert (
        'EMAIL_AGENT_OPERATIONS_WORKER_ENABLED: '
        '"${EMAIL_AGENT_OPERATIONS_WORKER_ENABLED:-false}"'
    ) in compose
    assert compose.count('EMAIL_AGENT_OPERATIONS_WORKER_ENABLED: "false"') == 1


class StatefulWriter:
    def __init__(self) -> None:
        self.provider_effects = 0
        self.calls = 0
        self.applied = False

    def verify_profile(self) -> None:
        return None

    def mutate_managed_labels(self, **kwargs):
        self.calls += 1
        before = ("INBOX", "UNREAD", "Label_Done") if self.applied else ("INBOX", "UNREAD")
        if not self.applied:
            self.applied = True
            self.provider_effects += 1
        after = ("INBOX", "UNREAD", "Label_Done")
        return GmailManagedLabelWriteResult(
            message_id=kwargs["message_id"],
            labels_before=before,
            labels_after=after,
            provider_modified=before != after,
            verified=True,
            managed_label_ids=(("Jarvis/Done", "Label_Done"),),
        )


def test_worker_retries_after_provider_effect_before_local_completion_without_second_effect(
    tmp_path,
    monkeypatch,
):
    permissions, storage, executor = _setup(tmp_path)
    envelope = _envelope(
        executor,
        tool_id="email.apply_labels",
        arguments={
            "message_refs": ["E1"],
            "label_refs": [EmailCatalogService.label_ref("done")],
        },
    )
    executor.execute(envelope=envelope)
    writer = StatefulWriter()
    original_complete = storage.complete_managed_label_operation
    failure_count = 0

    def fail_first_completion(**kwargs):
        nonlocal failure_count
        failure_count += 1
        if failure_count == 1:
            raise RuntimeError("simulated local commit interruption")
        return original_complete(**kwargs)

    monkeypatch.setattr(storage, "complete_managed_label_operation", fail_first_completion)
    worker = EmailOperationsWorker(
        storage=storage,
        writer=writer,
        permissions=permissions,
        config=EmailOperationsWorkerConfig(enabled=True),
        worker_id="worker-crash-recovery",
    )

    first = worker.run_once(now=NOW)
    second = worker.run_once(now=NOW + timedelta(seconds=16))
    operation = storage.get_managed_label_operation(
        operation_id=envelope.operation_id,
        owner_user_id="operator",
        discord_channel_id="100",
    )

    assert first["retry_count"] == 1
    assert second["verified_count"] == 1
    assert writer.calls == 2
    assert writer.provider_effects == 1
    assert operation["status"] == "completed"
    with storage._lock:
        private_manifest = storage._conn.execute(
            "SELECT recovery_manifest_json FROM email_tool_operations WHERE operation_id=?",
            (envelope.operation_id,),
        ).fetchone()[0]
    assert private_manifest == "{}"
    storage.close()


def test_expired_claim_increments_fence_and_rejects_stale_completion(tmp_path):
    _, storage, executor = _setup(tmp_path)
    envelope = _envelope(
        executor,
        tool_id="email.remove_labels",
        arguments={
            "message_refs": ["E1"],
            "label_refs": [EmailCatalogService.label_ref("done")],
        },
    )
    executor.execute(envelope=envelope)
    def iso(value):
        return value.isoformat().replace("+00:00", "Z")

    first = storage.claim_managed_label_operations(
        lease_owner="worker-old",
        now=iso(NOW),
        lease_expires_at=iso(NOW + timedelta(seconds=15)),
        limit=1,
    )[0]
    second = storage.claim_managed_label_operations(
        lease_owner="worker-new",
        now=iso(NOW + timedelta(seconds=16)),
        lease_expires_at=iso(NOW + timedelta(seconds=31)),
        limit=1,
    )[0]

    assert second["lease_fencing_token"] == first["lease_fencing_token"] + 1
    with pytest.raises(ValueError, match="lease_lost"):
        storage.complete_managed_label_operation(
            child_operation_id=first["child_operation_id"],
            lease_owner="worker-old",
            lease_fencing_token=first["lease_fencing_token"],
            provider_labels_before=[],
            provider_labels_after=[],
            gmail_label_ids=[],
            managed_label_state=[],
            now=iso(NOW + timedelta(seconds=16)),
        )
    storage.close()


def test_readiness_never_calls_the_provider(tmp_path):
    permissions, storage, _ = _setup(tmp_path)

    class NoProviderCalls:
        def verify_profile(self):
            raise AssertionError("readiness must not verify the provider")

        def mutate_managed_labels(self, **kwargs):
            raise AssertionError("readiness must not mutate the provider")

    worker = EmailOperationsWorker(
        storage=storage,
        writer=NoProviderCalls(),
        permissions=permissions,
        config=EmailOperationsWorkerConfig(enabled=True),
        worker_id="worker-readiness",
    )

    assert worker.readiness() == {
        "status": "ready",
        "worker_enabled": True,
        "managed_label_count": 2,
        "legacy_claim_eligible_count": 0,
        "schema_version": 15,
        "schema_ready": True,
        "supported_row_kinds": True,
        "unsupported_row_count": 0,
        "active_lease_owner_count": 0,
        "single_worker_ownership": True,
    }
    storage.close()


def test_readiness_database_inspection_is_read_only(tmp_path):
    permissions, storage, _ = _setup(tmp_path)
    database = tmp_path / "email.db"
    storage.close()
    before = database.read_bytes()

    inspection, stored_label_refs = inspect_email_operations_worker_database(
        str(database),
        now=NOW.isoformat(),
    )

    assert database.read_bytes() == before
    assert inspection == {
        "schema_version": 15,
        "schema_ready": True,
        "supported_row_kinds": True,
        "unsupported_row_count": 0,
        "active_lease_owner_count": 0,
        "single_worker_ownership": True,
    }
    assert stored_label_refs == {
        EmailCatalogService.label_ref(label.key)
        for label in permissions.managed_labels
        if label.enabled
    }
