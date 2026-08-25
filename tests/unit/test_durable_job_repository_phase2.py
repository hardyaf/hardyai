from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.jobs.repository import DurableJobRepository


def _job(repository: DurableJobRepository) -> dict:
    return repository.enqueue_job(
        job_type="document.process.v1",
        aggregate_id="doc-1",
        idempotency_key="process:doc-1:run-1",
        payload={"document_id": "doc-1", "source_version_id": "source-1", "run_id": "run-1"},
        resource_class="cpu_large",
    )


def test_progress_lease_and_provider_poll_are_fenced(tmp_path) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    queued = _job(repository)
    claimed = repository.claim_jobs(
        job_type="document.process.v1",
        worker_id="worker-a",
        limit=1,
        lease_seconds=30,
    )[0]
    token = int(claimed["lease_fencing_token"])

    assert repository.update_progress(
        job_id=queued["job_id"],
        worker_id="worker-a",
        fencing_token=token,
        stage="submit",
        current=1,
        total=3,
    )
    assert repository.renew_lease(
        job_id=queued["job_id"],
        worker_id="worker-a",
        fencing_token=token,
        lease_seconds=60,
    )
    assert repository.set_provider_operation(
        job_id=queued["job_id"],
        worker_id="worker-a",
        fencing_token=token,
        operation_ref="task-1",
        reconcile_state="submitted",
    )
    assert not repository.complete_job(
        job_id=queued["job_id"],
        worker_id="worker-a",
        fencing_token=token + 1,
    )
    assert repository.defer_job(
        job_id=queued["job_id"],
        worker_id="worker-a",
        fencing_token=token,
        delay_seconds=0,
        reconcile_state="provider_pending",
    )
    deferred = repository.get_job(queued["job_id"])
    assert deferred["status"] == "retry"
    assert deferred["attempt_count"] == 0
    assert deferred["current_stage"] == "submit"
    assert deferred["provider_operation_ref"] == "task-1"
    repository.close()


def test_cancellation_and_operator_requeue_are_idempotent(tmp_path) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    queued = _job(repository)
    first = repository.request_cancel(job_id=queued["job_id"])
    second = repository.request_cancel(job_id=queued["job_id"])
    assert first["status"] == second["status"] == "cancelled"
    assert first["cancel_requested_at"] == second["cancel_requested_at"]

    requeued = repository.requeue_job(job_id=queued["job_id"])
    assert requeued["status"] == "retry"
    assert requeued["cancel_requested_at"] is None
    assert repository.claim_jobs(
        job_type="document.process.v1",
        worker_id="worker-b",
        limit=1,
        lease_seconds=30,
    )
    repository.close()


def test_expired_lease_rejects_stale_worker_completion(tmp_path) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    queued = _job(repository)
    started = datetime.now(UTC)
    stale = repository.claim_jobs(
        job_type="document.process.v1",
        worker_id="worker-stale",
        limit=1,
        lease_seconds=1,
        now=started,
    )[0]
    replacement = repository.claim_jobs(
        job_type="document.process.v1",
        worker_id="worker-current",
        limit=1,
        lease_seconds=30,
        now=started + timedelta(seconds=2),
    )[0]
    assert replacement["lease_fencing_token"] > stale["lease_fencing_token"]
    assert not repository.complete_job(
        job_id=queued["job_id"],
        worker_id="worker-stale",
        fencing_token=stale["lease_fencing_token"],
    )
    assert repository.complete_job(
        job_id=queued["job_id"],
        worker_id="worker-current",
        fencing_token=replacement["lease_fencing_token"],
    )
    repository.close()
