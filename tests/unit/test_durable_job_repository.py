from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.jobs.repository import DurableJobRepository


def test_generic_job_repository_is_idempotent_and_leased(tmp_path) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    first = repository.enqueue_job(
        job_type="document.archive.v1",
        aggregate_id="doc-1",
        idempotency_key="document-archive:doc-1",
        payload={"document_id": "doc-1"},
        max_attempts=2,
    )
    repeated = repository.enqueue_job(
        job_type="document.archive.v1",
        aggregate_id="doc-1",
        idempotency_key="document-archive:doc-1",
        payload={"document_id": "different-payload-is-not-applied"},
        max_attempts=9,
    )

    assert repeated["job_id"] == first["job_id"]
    assert repeated["payload"] == {"document_id": "doc-1"}

    claimed = repository.claim_jobs(
        job_type="document.archive.v1",
        worker_id="worker-1",
        limit=10,
        lease_seconds=30,
    )
    assert [job["job_id"] for job in claimed] == [first["job_id"]]
    assert claimed[0]["attempt_count"] == 1
    assert repository.complete_job(job_id=first["job_id"], worker_id="other-worker") is False
    assert repository.complete_job(job_id=first["job_id"], worker_id="worker-1") is True
    assert repository.get_job(first["job_id"])["status"] == "completed"
    repository.close()


def test_expired_lease_is_retried_then_dead_lettered_at_attempt_cap(tmp_path) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    job = repository.enqueue_job(
        job_type="document.archive.v1",
        aggregate_id="doc-1",
        idempotency_key="document-archive:doc-1",
        payload={"document_id": "doc-1"},
        max_attempts=2,
    )
    started = datetime.fromisoformat(job["available_at"]).astimezone(UTC) + timedelta(seconds=1)

    first = repository.claim_jobs(
        job_type="document.archive.v1",
        worker_id="worker-1",
        limit=1,
        lease_seconds=30,
        now=started,
    )
    assert first[0]["attempt_count"] == 1
    second = repository.claim_jobs(
        job_type="document.archive.v1",
        worker_id="worker-2",
        limit=1,
        lease_seconds=30,
        now=started + timedelta(seconds=31),
    )
    assert second[0]["attempt_count"] == 2
    assert repository.get_job(job["job_id"])["last_error_code"] == "lease_expired"

    assert repository.claim_jobs(
        job_type="document.archive.v1",
        worker_id="worker-3",
        limit=1,
        lease_seconds=30,
        now=started + timedelta(seconds=62),
    ) == []
    persisted = repository.get_job(job["job_id"])
    assert persisted["status"] == "dead_letter"
    assert persisted["last_error_code"] == "lease_expired"
    repository.close()
