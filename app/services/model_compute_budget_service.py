from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.jobs.repository import DurableJobRepository
from app.jobs.types import ResourceClass


MODEL_COMPUTE_BUDGET_NOTICE_JOB = "model.compute_budget_notice.v1"


class ModelComputeBudgetNotificationService:
    """Durable, content-free operator notices for adaptive model compute."""

    def __init__(
        self,
        *,
        repository: DurableJobRepository,
        worker_id: str | None = None,
        batch_size: int = 10,
        lease_seconds: float = 30.0,
    ) -> None:
        self.repository = repository
        self.worker_id = str(worker_id or f"model-compute-notify-{uuid4()}")
        self.batch_size = max(1, min(int(batch_size), 50))
        self.lease_seconds = max(5.0, min(float(lease_seconds), 120.0))

    def enqueue_escalation(self, metrics: dict[str, Any]) -> dict[str, Any]:
        call_id = str(metrics.get("call_id") or "").strip()
        lane = self._identifier(metrics.get("lane"), fallback="model")
        model = str(metrics.get("model") or "local model").strip()[:120] or "local model"
        reason = self._identifier(metrics.get("escalation_reason"), fallback="token_limit")
        attempt = self._bounded_int(metrics.get("attempt"), minimum=1, maximum=8)
        from_budget = self._bounded_int(
            metrics.get("requested_num_predict"), minimum=1, maximum=1_000_000
        )
        to_budget = self._bounded_int(
            metrics.get("escalated_to_num_predict"), minimum=1, maximum=1_000_000
        )
        if not call_id or len(call_id) > 80 or to_budget <= from_budget:
            raise ValueError("invalid model compute escalation metrics")
        identity = f"{call_id}:{attempt}:{from_budget}:{to_budget}"
        digest = hashlib.sha256(identity.encode("ascii", errors="ignore")).hexdigest()
        return self.repository.enqueue_job(
            job_type=MODEL_COMPUTE_BUDGET_NOTICE_JOB,
            aggregate_id=call_id,
            idempotency_key=f"model-compute-budget-notice:{digest}",
            payload={
                "schema_version": 1,
                "notice_kind": "escalated",
                "call_id": call_id,
                "lane": lane,
                "model": model,
                "reason": reason,
                "attempt": attempt,
                "from_num_predict": from_budget,
                "to_num_predict": to_budget,
            },
            max_attempts=8,
            priority=50,
            resource_class=ResourceClass.CPU_SMALL.value,
            total_deadline_at=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        )

    def enqueue_failed_loop(self, metrics: dict[str, Any]) -> dict[str, Any]:
        call_id = str(metrics.get("call_id") or "").strip()
        lane = self._identifier(metrics.get("lane"), fallback="model")
        model = str(metrics.get("model") or "local model").strip()[:120] or "local model"
        reason = self._identifier(metrics.get("done_reason"), fallback="token_limit")
        attempt = self._bounded_int(metrics.get("attempt"), minimum=1, maximum=8)
        final_budget = self._bounded_int(
            metrics.get("requested_num_predict"), minimum=1, maximum=1_000_000
        )
        if not call_id or len(call_id) > 80:
            raise ValueError("invalid model compute failed-loop metrics")
        digest = hashlib.sha256(f"{call_id}:failed-loop".encode("ascii", errors="ignore")).hexdigest()
        return self.repository.enqueue_job(
            job_type=MODEL_COMPUTE_BUDGET_NOTICE_JOB,
            aggregate_id=call_id,
            idempotency_key=f"model-compute-budget-notice:{digest}",
            payload={
                "schema_version": 1,
                "notice_kind": "failed_loop",
                "call_id": call_id,
                "lane": lane,
                "model": model,
                "reason": reason,
                "attempt": attempt,
                "final_num_predict": final_budget,
            },
            max_attempts=8,
            priority=40,
            resource_class=ResourceClass.CPU_SMALL.value,
            total_deadline_at=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        )

    def claim(self) -> list[dict[str, Any]]:
        return self.repository.claim_jobs(
            job_type=MODEL_COMPUTE_BUDGET_NOTICE_JOB,
            worker_id=self.worker_id,
            limit=self.batch_size,
            lease_seconds=self.lease_seconds,
        )

    def message(self, job: dict[str, Any]) -> str:
        payload = self._payload(job)
        lane = str(payload["lane"]).replace("_", " ")
        reason = str(payload["reason"]).replace("_", " ")
        if payload.get("notice_kind") == "failed_loop":
            return (
                "A model task exhausted every bounded output-token retry and stopped as a failed loop: "
                f"{lane} on `{payload['model']}` reached "
                f"{int(payload['final_num_predict']):,} output tokens after "
                f"{int(payload['attempt'])} attempts ({reason}). The event is tracked as "
                "`model.compute_budget.failed_loop` and should be reviewed as a prompt, code, or "
                "process problem."
            )[:1900]
        return (
            "Compute budget increased automatically so token exhaustion would not end the task: "
            f"{lane} on `{payload['model']}` used "
            f"{int(payload['from_num_predict']):,} -> {int(payload['to_num_predict']):,} output tokens "
            f"({reason}, attempt {int(payload['attempt'])}). The task continued, and the event is "
            "tracked as `model.compute_budget.escalated` for process tuning."
        )[:1900]

    @staticmethod
    def already_delivered(job: dict[str, Any]) -> bool:
        return bool(str(job.get("provider_operation_ref") or "").strip())

    def record_delivery(self, job: dict[str, Any], *, message_id: str) -> bool:
        normalized = str(message_id or "").strip()
        if not normalized.isdigit() or len(normalized) > 32:
            raise ValueError("invalid Discord delivery message ID")
        return self.repository.set_provider_operation(
            job_id=str(job["job_id"]),
            worker_id=self.worker_id,
            fencing_token=int(job.get("lease_fencing_token") or 0),
            operation_ref=f"discord:{normalized}",
            reconcile_state="delivered",
        )

    def complete(self, job: dict[str, Any]) -> bool:
        return self.repository.complete_job(
            job_id=str(job["job_id"]),
            worker_id=self.worker_id,
            fencing_token=int(job.get("lease_fencing_token") or 0),
        )

    def retry(self, job: dict[str, Any], *, error_code: str) -> bool:
        attempt = max(1, int(job.get("attempt_count") or 1))
        return self.repository.retry_job(
            job_id=str(job["job_id"]),
            worker_id=self.worker_id,
            fencing_token=int(job.get("lease_fencing_token") or 0),
            error_code=str(error_code or "model_compute_notice_failed")[:120],
            delay_seconds=min(300.0, float(2 ** min(attempt, 8))),
        )

    @staticmethod
    def delivery_nonce(job: dict[str, Any]) -> int:
        material = str(job.get("idempotency_key") or job.get("job_id") or "").encode("utf-8")
        return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") & ((1 << 63) - 1)

    @staticmethod
    def _payload(job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload")
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("unsupported model compute notice payload")
        return payload

    @staticmethod
    def _identifier(value: Any, *, fallback: str) -> str:
        normalized = str(value or "").strip().casefold()
        if not normalized or len(normalized) > 80 or not all(
            character.isalnum() or character in {"_", "-", "."} for character in normalized
        ):
            return fallback
        return normalized

    @staticmethod
    def _bounded_int(value: Any, *, minimum: int, maximum: int) -> int:
        if isinstance(value, bool):
            raise ValueError("invalid model compute integer")
        try:
            numeric = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid model compute integer") from exc
        if numeric < minimum or numeric > maximum:
            raise ValueError("model compute integer outside bounds")
        return numeric
