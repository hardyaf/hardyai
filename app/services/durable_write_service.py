from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.services.memory_service import MemoryService
from app.jobs.types import DurableJobStore


MEMORY_WRITE_JOB = "write.memory_interaction.v1"


@dataclass(frozen=True)
class DurableWriteConfig:
    poll_seconds: float = 0.5
    batch_size: int = 20
    lease_seconds: float = 30.0
    max_attempts: int = 5


class DurableWriteService:
    """Generic durable-job worker for acknowledged application writes."""

    def __init__(
        self,
        *,
        repository: DurableJobStore,
        memory_service: MemoryService,
        config: DurableWriteConfig | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._repository = repository
        self._memory_service = memory_service
        self._config = config or DurableWriteConfig()
        self._worker_id = str(worker_id or f"durable-write-{uuid4()}")

    def enqueue_memory_interaction(
        self,
        *,
        request_id: str,
        session_id: str,
        user_id: str,
        source: str,
        intent: str,
        route: str,
        request_text: str,
        response_summary: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        idempotency_key = f"memory:{str(request_id).strip()}"
        job = self._repository.enqueue_job(
            job_type=MEMORY_WRITE_JOB,
            aggregate_id=session_id,
            idempotency_key=idempotency_key,
            payload={
                "schema_version": 1,
                "session_id": session_id,
                "user_id": user_id,
                "source": source,
                "intent": intent,
                "route": route,
                "request_text": request_text[:20000],
                "response_summary": response_summary[:20000],
                "metadata": dict(metadata),
            },
            max_attempts=self._config.max_attempts,
        )
        return self._delivery(job)

    def run_once(self) -> int:
        jobs = self._repository.claim_jobs(
            job_type=MEMORY_WRITE_JOB,
            worker_id=self._worker_id,
            limit=self._config.batch_size,
            lease_seconds=self._config.lease_seconds,
        )
        processed = 0
        last_error: str | None = None
        for job in jobs:
            try:
                self._commit_memory_job(job)
                if not self._repository.complete_job(
                    job_id=str(job["job_id"]),
                    worker_id=self._worker_id,
                ):
                    raise RuntimeError("durable_write_lease_lost")
                processed += 1
            except Exception as exc:  # pragma: no cover - defensive worker boundary
                last_error = type(exc).__name__
                attempt = max(1, int(job.get("attempt_count") or 1))
                self._repository.retry_job(
                    job_id=str(job["job_id"]),
                    worker_id=self._worker_id,
                    error_code=last_error,
                    delay_seconds=min(60.0, float(2 ** min(attempt, 5))),
                )
        self._repository.record_worker_heartbeat(
            worker_type="durable_write",
            worker_id=self._worker_id,
            status="degraded" if last_error else "ready",
            last_error_code=last_error,
            metadata={"claimed": len(jobs), "committed": processed},
        )
        return processed

    def recover_startup(self, *, max_batches: int = 20) -> int:
        total = 0
        for _ in range(max(1, min(int(max_batches), 100))):
            processed = self.run_once()
            total += processed
            if processed == 0:
                break
        return total

    async def run_forever(self) -> None:
        while True:
            await asyncio.to_thread(self.run_once)
            await asyncio.sleep(max(0.1, float(self._config.poll_seconds)))

    def _commit_memory_job(self, job: dict[str, Any]) -> None:
        payload = job.get("payload")
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("unsupported_durable_write_payload")
        metadata = payload.get("metadata")
        self._memory_service.record_interaction(
            session_id=str(payload.get("session_id") or ""),
            user_id=str(payload.get("user_id") or ""),
            source=str(payload.get("source") or ""),
            intent=str(payload.get("intent") or ""),
            route=str(payload.get("route") or ""),
            request_text=str(payload.get("request_text") or ""),
            response_summary=str(payload.get("response_summary") or ""),
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
            operation_id=str(job.get("idempotency_key") or ""),
        )

    @staticmethod
    def _delivery(job: dict[str, Any]) -> dict[str, Any]:
        raw_status = str(job.get("status") or "pending").strip().casefold()
        if raw_status == "completed":
            status = "committed"
        elif raw_status == "dead_letter":
            status = "failed"
        else:
            status = "queued"
        return {
            "status": status,
            "job_id": job.get("job_id"),
            "operation_id": job.get("idempotency_key"),
        }
