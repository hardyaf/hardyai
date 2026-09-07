from __future__ import annotations

from app.jobs.repository import DurableJobRepository
from app.jobs.types import ResourceClass


DOCUMENT_ARCHIVE_JOB_TYPE = "document.archive.v1"
DOCUMENT_PROCESS_JOB_TYPE = "document.process.v1"


class DurableDocumentEnqueuer:
    """Coordinator-side adapter that writes only bounded document references to core jobs."""

    def __init__(
        self,
        repository: DurableJobRepository,
        *,
        max_attempts: int,
        processing_max_attempts: int | None = None,
    ) -> None:
        self.repository = repository
        self.max_attempts = max(1, int(max_attempts))
        self.processing_max_attempts = max(
            1,
            int(processing_max_attempts if processing_max_attempts is not None else max_attempts),
        )

    def enqueue_document(self, *, document_id: str, intake_id: str, sha256: str) -> str:
        job = self.repository.enqueue_job(
            job_type=DOCUMENT_ARCHIVE_JOB_TYPE,
            aggregate_id=document_id,
            idempotency_key=f"document-archive:{document_id}",
            payload={
                "document_id": document_id,
                "intake_id": intake_id,
                "sha256": sha256,
            },
            max_attempts=self.max_attempts,
        )
        return str(job["job_id"])

    def enqueue_processing(
        self,
        *,
        document_id: str,
        source_version_id: str,
        run_id: str,
        operation_id: str | None = None,
        processing_tier: str = "default",
        arguments_hash: str | None = None,
    ) -> str:
        tool_operation_id = str(operation_id or "").strip()
        if tool_operation_id:
            normalized_hash = str(arguments_hash or "").strip().casefold()
            normalized_tier = str(processing_tier or "default").strip().casefold()
            if len(normalized_hash) != 64 or normalized_tier not in {"default", "review_fallback"}:
                raise ValueError("document_processing_operation_invalid")
            idempotency_key = tool_operation_id
            payload = {
                "document_id": document_id,
                "processing_tier": normalized_tier,
                "arguments_hash": normalized_hash,
            }
        else:
            idempotency_key = f"document-process:{run_id}"
            payload = {
                "document_id": document_id,
                "source_version_id": source_version_id,
                "run_id": run_id,
            }
        job = self.repository.enqueue_job(
            job_type=DOCUMENT_PROCESS_JOB_TYPE,
            aggregate_id=document_id,
            idempotency_key=idempotency_key,
            payload=payload,
            max_attempts=self.processing_max_attempts,
            priority=200,
            resource_class=(
                ResourceClass.GPU_VLM.value
                if tool_operation_id and str(processing_tier).strip().casefold() == "review_fallback"
                else ResourceClass.CPU_LARGE.value
            ),
        )
        return str(job["job_id"])
