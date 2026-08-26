from __future__ import annotations

from app.accelerator.repository import AcceleratorLeaseRepository
from app.accelerator.service import LANE_PRIORITIES


def test_durable_accelerator_queue_prioritizes_live_work_and_fences_old_owners(tmp_path) -> None:
    repository = AcceleratorLeaseRepository(str(tmp_path / "accelerator.db"))
    document_waiter = repository.enqueue(
        lane="document_vlm",
        priority=LANE_PRIORITIES["document_vlm"],
        wait_seconds=30,
    )
    main_waiter = repository.enqueue(
        lane="main_conversation",
        priority=LANE_PRIORITIES["main_conversation"],
        wait_seconds=30,
    )

    assert repository.try_acquire(waiter_id=document_waiter, lease_seconds=30) is None
    main_lease = repository.try_acquire(waiter_id=main_waiter, lease_seconds=30)
    assert main_lease is not None
    assert main_lease.lane == "main_conversation"
    assert repository.heartbeat(lease=main_lease, lease_seconds=30) is True
    assert repository.release(lease=main_lease) is True

    document_lease = repository.try_acquire(waiter_id=document_waiter, lease_seconds=30)
    assert document_lease is not None
    assert document_lease.fencing_token > main_lease.fencing_token
    assert repository.heartbeat(lease=main_lease, lease_seconds=30) is False
    assert repository.release(lease=main_lease) is False
    assert repository.release(lease=document_lease) is True
    assert repository.snapshot()["queued"] == 0
    repository.close()


def test_accelerator_client_fails_closed_when_admission_key_is_required(monkeypatch) -> None:
    from app.accelerator.client import accelerator_request_headers

    monkeypatch.setenv("ACCELERATOR_ADMISSION_REQUIRED", "true")
    monkeypatch.delenv("ACCELERATOR_ADMISSION_API_KEY_PATH", raising=False)

    try:
        accelerator_request_headers("micro")
    except RuntimeError as exc:
        assert str(exc) == "accelerator_admission_key_path_missing"
    else:
        raise AssertionError("required accelerator admission unexpectedly allowed a bypass")


def test_accelerator_client_reads_key_and_sets_typed_lane(tmp_path, monkeypatch) -> None:
    from app.accelerator.client import accelerator_request_headers

    key_path = tmp_path / "accelerator.key"
    key_path.write_text("bounded-test-key", encoding="utf-8")
    monkeypatch.setenv("ACCELERATOR_ADMISSION_REQUIRED", "true")
    monkeypatch.setenv("ACCELERATOR_ADMISSION_API_KEY_PATH", str(key_path))

    assert accelerator_request_headers("document_vlm") == {
        "X-HardyAI-Accelerator-Lane": "document_vlm",
        "X-HardyAI-Accelerator-Key": "bounded-test-key",
    }
