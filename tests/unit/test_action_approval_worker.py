from __future__ import annotations

from app.jobs.repository import DurableJobRepository
from app.jobs.types import (
    REVIEW_ACTION_EXECUTION_JOB,
    REVIEW_NOTIFICATION_DISCORD_JOB,
    REVIEW_OUTCOME_DISCORD_JOB,
)
from app.services.discord.approval_delivery import ApprovalDeliveryError
from app.workers.action_approval_worker import ActionApprovalWorker, ActionApprovalWorkerConfig


class StubDelivery:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    def deliver(self, job):
        self.calls.append(job["job_id"])
        if self.fail:
            raise ApprovalDeliveryError("discord_delivery_retryable")
        return {"status": "delivered", "message_id": "message-1"}

    def deliver_outcome(self, job):
        self.calls.append(job["job_id"])
        if self.fail:
            raise ApprovalDeliveryError("discord_delivery_retryable")
        return {"status": "delivered", "message_id": "message-2"}


class StubExecution:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, job):
        self.calls.append(job["job_id"])
        return {"status": "denied", "reason_code": "reauthorization_denied"}


def _notification(repository):
    payload = {
        "proposal_id": "proposal-1",
        "review_id": "review-1",
        "operation_id": "operation-1",
        "authorization_binding": "authorization-1",
        "batch_manifest_hash": None,
        "transfer_binding_hash": None,
        "destination_purpose": "human_reviews",
    }
    return repository.enqueue_job(
        job_type=REVIEW_NOTIFICATION_DISCORD_JOB,
        aggregate_id="proposal-1",
        idempotency_key=(
            "review-notification-discord:v1:proposal-1:review-1:human_reviews"
        ),
        payload=payload,
    )


def _execution(repository):
    payload = {
        "proposal_id": "proposal-2",
        "review_id": "review-2",
        "operation_id": "operation-2",
        "authorization_binding": "authorization-2",
        "batch_manifest_hash": None,
        "transfer_binding_hash": None,
    }
    return repository.enqueue_job(
        job_type=REVIEW_ACTION_EXECUTION_JOB,
        aggregate_id="proposal-2",
        idempotency_key="review-action-execution:v1:proposal-2:operation-2",
        payload=payload,
    )


def _outcome(repository):
    payload = {
        "proposal_id": "proposal-3",
        "review_id": "review-3",
        "operation_id": "operation-3",
        "authorization_binding": "authorization-3",
        "state": "executed",
        "destination_purpose": "human_reviews",
    }
    return repository.enqueue_job(
        job_type=REVIEW_OUTCOME_DISCORD_JOB,
        aggregate_id="proposal-3",
        idempotency_key="review-outcome-discord:v1:proposal-3:executed",
        payload=payload,
    )


def test_worker_claims_all_job_types_completes_and_heartbeats(tmp_path) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    notification = _notification(repository)
    execution = _execution(repository)
    outcome = _outcome(repository)
    delivery = StubDelivery()
    executor = StubExecution()
    worker = ActionApprovalWorker(
        jobs=repository,
        delivery=delivery,
        execution=executor,
        config=ActionApprovalWorkerConfig(enabled=True),
        worker_id="approval-worker-1",
    )

    result = worker.run_once()

    assert result == {
        "status": "ok",
        "claimed_count": 3,
        "completed_count": 3,
        "retry_count": 0,
        "dead_letter_count": 0,
    }
    assert repository.get_job(notification["job_id"])["status"] == "completed"
    assert repository.get_job(execution["job_id"])["status"] == "completed"
    assert repository.get_job(outcome["job_id"])["status"] == "completed"
    heartbeat = repository.get_worker_heartbeat("action_approval")
    assert heartbeat["status"] == "idle"
    repository.close()


def test_worker_retries_transient_delivery_with_bounded_attempts(tmp_path) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    notification = _notification(repository)
    worker = ActionApprovalWorker(
        jobs=repository,
        delivery=StubDelivery(fail=True),
        execution=StubExecution(),
        config=ActionApprovalWorkerConfig(enabled=True),
        worker_id="approval-worker-1",
    )

    result = worker.run_once()

    assert result["retry_count"] == 1
    persisted = repository.get_job(notification["job_id"])
    assert persisted["status"] == "retry"
    assert persisted["last_error_code"] == "discord_delivery_retryable"
    repository.close()
