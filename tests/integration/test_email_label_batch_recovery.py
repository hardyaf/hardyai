from __future__ import annotations

from datetime import timedelta

from app.skills.domains.email_agent.catalog import EmailCatalogService
from app.workers.email_operations_worker import (
    EmailOperationsWorker,
    EmailOperationsWorkerConfig,
)
from tests.unit.test_email_agent_operations import FakeWriter, NOW, _envelope, _setup


def test_worker_startup_reconstructs_reserved_batch_before_provider_claim(tmp_path):
    permissions, storage, executor = _setup(tmp_path)
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

    writer = FakeWriter()
    worker = EmailOperationsWorker(
        storage=storage,
        writer=writer,
        permissions=permissions,
        config=EmailOperationsWorkerConfig(enabled=True),
        worker_id="worker-restarted",
    )
    result = worker.run_once(now=NOW + timedelta(days=1))
    operation = storage.get_managed_label_operation(
        operation_id=envelope.operation_id,
        owner_user_id="operator",
        discord_channel_id="100",
    )

    assert result["verified_count"] == 2
    assert operation["status"] == "completed"
    assert operation["child_counts"] == {"verified": 2}
    assert [call["message_id"] for call in writer.calls] == ["m1", "m2"]
    storage.close()


def test_worker_startup_reconstructs_reversible_system_label_batch(tmp_path):
    permissions, storage, executor = _setup(tmp_path)
    envelope = _envelope(
        executor,
        tool_id="email.archive_messages",
        arguments={"message_refs": ["E1", "E2"]},
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

    writer = FakeWriter()
    worker = EmailOperationsWorker(
        storage=storage,
        writer=writer,
        permissions=permissions,
        config=EmailOperationsWorkerConfig(enabled=True),
        worker_id="worker-restarted-system-label",
    )
    result = worker.run_once(now=NOW + timedelta(days=1))
    operation = storage.get_managed_label_operation(
        operation_id=envelope.operation_id,
        owner_user_id="operator",
        discord_channel_id="100",
    )

    assert result["verified_count"] == 2
    assert operation["status"] == "completed"
    assert [call["message_id"] for call in writer.system_calls] == ["m1", "m2"]
    assert all(call["action"] == "remove" for call in writer.system_calls)
    assert all(call["system_label"] == "INBOX" for call in writer.system_calls)
    storage.close()
