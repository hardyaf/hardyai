from __future__ import annotations

from enum import StrEnum
from datetime import datetime
from typing import Any, Protocol


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY = "retry"
    COMPLETED = "completed"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class ResourceClass(StrEnum):
    CPU_SMALL = "cpu_small"
    CPU_LARGE = "cpu_large"
    GPU_OCR = "gpu_ocr"
    GPU_VLM = "gpu_vlm"


class DurableJobStore(Protocol):
    def enqueue_job(
        self,
        *,
        job_type: str,
        aggregate_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        available_at: str | None = None,
        max_attempts: int = 3,
        priority: int = 100,
        resource_class: str = ResourceClass.CPU_SMALL.value,
        total_deadline_at: str | None = None,
    ) -> dict[str, Any]:
        ...

    def claim_jobs(
        self,
        *,
        job_type: str,
        worker_id: str,
        limit: int,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def complete_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        fencing_token: int | None = None,
    ) -> bool:
        ...

    def retry_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        delay_seconds: float,
        fencing_token: int | None = None,
    ) -> bool:
        ...

    def renew_lease(
        self,
        *,
        job_id: str,
        worker_id: str,
        fencing_token: int,
        lease_seconds: float,
    ) -> bool:
        ...

    def defer_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        fencing_token: int,
        delay_seconds: float,
        reconcile_state: str,
    ) -> bool:
        ...

    def release_jobs(
        self,
        *,
        job_type: str,
        aggregate_id: str,
        reconcile_state: str,
    ) -> int:
        """Make matching pending work immediately claimable after an external state change."""
        ...

    def dead_letter_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        fencing_token: int,
        error_code: str,
    ) -> bool:
        ...

    def record_worker_heartbeat(
        self,
        *,
        worker_type: str,
        worker_id: str,
        status: str,
        last_error_code: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        ...
