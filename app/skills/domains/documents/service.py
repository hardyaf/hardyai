from __future__ import annotations

from app.skills.domains.documents.ingestion import TransientDocumentSpool
from app.skills.domains.documents.ports import DurableDocumentEnqueuePort
from app.skills.domains.documents.storage import DocumentRepository
from app.skills.domains.documents.types import IntakeResult, StagedDocument


class DocumentIngestionService:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        spool: TransientDocumentSpool,
        enqueuer: DurableDocumentEnqueuePort,
    ) -> None:
        self.repository = repository
        self.spool = spool
        self.enqueuer = enqueuer

    def accept(self, *, owner_id: str, staged: StagedDocument) -> IntakeResult:
        record, created = self.repository.create_or_get(owner_id=owner_id, staged=staged)
        if not created:
            self.spool.delete(staged.spool_key)
            return IntakeResult(
                record=record,
                created=False,
                enqueue_confirmed=record.durable_job_id is not None,
            )
        try:
            job_id = self.enqueuer.enqueue_document(
                document_id=record.document_id,
                intake_id=record.intake_id,
                sha256=record.sha256,
            )
        except Exception:
            return IntakeResult(record=record, created=True, enqueue_confirmed=False)
        queued = self.repository.mark_enqueued(document_id=record.document_id, durable_job_id=job_id)
        return IntakeResult(record=queued, created=True, enqueue_confirmed=True)

    def recover_awaiting_enqueue(self, *, limit: int = 100) -> int:
        recovered = 0
        for record in self.repository.awaiting_enqueue(limit=limit):
            job_id = self.enqueuer.enqueue_document(
                document_id=record.document_id,
                intake_id=record.intake_id,
                sha256=record.sha256,
            )
            self.repository.mark_enqueued(document_id=record.document_id, durable_job_id=job_id)
            recovered += 1
        return recovered
