from __future__ import annotations

from app.jobs.repository import DurableJobRepository
from app.services.model_compute_budget_service import (
    MODEL_COMPUTE_BUDGET_NOTICE_JOB,
    ModelComputeBudgetNotificationService,
)


def test_compute_budget_notice_is_durable_idempotent_and_content_free(tmp_path) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    service = ModelComputeBudgetNotificationService(repository=repository, worker_id="notice-1")
    metrics = {
        "call_id": "8b448642-ef72-4d39-9a22-b5416ec42ad1",
        "lane": "main_conversation",
        "model": "gpt-oss:20b",
        "escalation_reason": "provider_token_limit",
        "attempt": 1,
        "requested_num_predict": 1024,
        "escalated_to_num_predict": 2048,
        "prompt": "must never be stored",
    }

    first = service.enqueue_escalation(metrics)
    second = service.enqueue_escalation(metrics)

    assert first["job_id"] == second["job_id"]
    jobs = repository.list_jobs(job_type=MODEL_COMPUTE_BUDGET_NOTICE_JOB)
    assert len(jobs) == 1
    assert jobs[0]["payload"] == {
        "schema_version": 1,
        "notice_kind": "escalated",
        "call_id": metrics["call_id"],
        "lane": "main_conversation",
        "model": "gpt-oss:20b",
        "reason": "provider_token_limit",
        "attempt": 1,
        "from_num_predict": 1024,
        "to_num_predict": 2048,
    }
    assert "must never be stored" not in repr(jobs[0])
    repository.close()


def test_compute_budget_failed_loop_has_separate_durable_notice(tmp_path) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    service = ModelComputeBudgetNotificationService(repository=repository, worker_id="notice-loop")

    service.enqueue_failed_loop(
        {
            "call_id": "call-loop",
            "lane": "main_conversation",
            "model": "gpt-oss:20b",
            "done_reason": "length",
            "attempt": 4,
            "requested_num_predict": 8192,
        }
    )

    job = service.claim()[0]
    assert "exhausted every bounded" in service.message(job)
    assert "8,192" in service.message(job)
    assert job["payload"]["notice_kind"] == "failed_loop"
    repository.close()


def test_compute_budget_notice_claim_delivery_and_completion(tmp_path) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    service = ModelComputeBudgetNotificationService(repository=repository, worker_id="notice-2")
    service.enqueue_escalation(
        {
            "call_id": "call-1",
            "lane": "email_summary",
            "model": "gpt-oss:20b",
            "escalation_reason": "observed_token_limit",
            "attempt": 2,
            "requested_num_predict": 2048,
            "escalated_to_num_predict": 4096,
        }
    )

    job = service.claim()[0]
    assert "2,048 -> 4,096" in service.message(job)
    assert service.record_delivery(job, message_id="900") is True
    assert service.complete(job) is True
    assert repository.get_job(str(job["job_id"]))["status"] == "completed"
    repository.close()
