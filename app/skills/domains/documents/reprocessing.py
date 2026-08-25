from __future__ import annotations

from typing import Any

from app.skills.domains.documents.permissions import DocumentAccessPolicy
from app.skills.domains.documents.ports import DurableDocumentEnqueuePort
from app.skills.domains.documents.storage import DocumentRepository, DocumentStorageError
from app.skills.domains.documents.types import DocumentState, ProcessingRoute


class DocumentReprocessingService:
    """Creates an immutable parser run; source bytes never cross this control seam."""

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        enqueuer: DurableDocumentEnqueuePort,
        parser_name: str,
        parser_version: str,
        parser_image_digest: str,
        configuration_sha256: str,
    ) -> None:
        self.repository = repository
        self.enqueuer = enqueuer
        self.parser_name = str(parser_name)[:80]
        self.parser_version = str(parser_version)[:80]
        self.parser_image_digest = str(parser_image_digest)[:160]
        self.configuration_sha256 = str(configuration_sha256)

    def request(
        self,
        *,
        document_id: str,
        owner_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        record = self.repository.get(document_id, owner_id=owner_id)
        if record is None or not DocumentAccessPolicy.can_read(record=record, user_id=owner_id):
            raise KeyError(document_id)
        if record.state != DocumentState.READY or not record.source_version_id:
            raise DocumentStorageError("document_source_not_ready")
        if record.media_type != "application/pdf":
            raise DocumentStorageError("docling_phase3_pdf_only")
        request_key = f"reprocess:{document_id}:{str(idempotency_key).strip()}"
        run = self.repository.create_processing_run(
            document_id=document_id,
            route=ProcessingRoute.NATIVE_DOCLING,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            parser_image_digest=self.parser_image_digest,
            configuration_sha256=self.configuration_sha256,
            resource_lane="cpu_large",
            request_key=request_key,
        )
        enqueue_confirmed = True
        try:
            job_id = self.enqueuer.enqueue_processing(
                document_id=document_id,
                source_version_id=str(run["source_version_id"]),
                run_id=str(run["run_id"]),
            )
        except Exception:
            enqueue_confirmed = False
            job_id = None
        return {
            "document_id": document_id,
            "run_id": str(run["run_id"]),
            "processing_state": str(run["status"]),
            "job_id": job_id,
            "enqueue_confirmed": enqueue_confirmed,
        }

    def recover_pending(self, *, limit: int = 100) -> int:
        recovered = 0
        for run in self.repository.pending_processing_runs(limit=limit):
            self.enqueuer.enqueue_processing(
                document_id=str(run["document_id"]),
                source_version_id=str(run["source_version_id"]),
                run_id=str(run["run_id"]),
            )
            recovered += 1
        return recovered
