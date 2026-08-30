from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Any

from app.integrations.paperless.adapter import PaperlessReadAdapter
from app.integrations.paperless.client import PaperlessClient
from app.jobs.enqueue_ipc import UnixDocumentEnqueueClient
from app.skills.domains.documents.configuration import (
    conventional_ocr_configuration_sha256,
    native_docling_configuration_sha256,
    vlm_fallback_configuration_sha256,
)
from app.skills.domains.documents.corrections import DocumentFieldCorrectionService
from app.skills.domains.documents.ingestion import TransientDocumentSpool
from app.skills.domains.documents.service import DocumentIngestionService
from app.skills.domains.documents.reprocessing import DocumentReprocessingService
from app.skills.domains.documents.storage import DocumentRepository


@dataclass
class DocumentGatewayContainer:
    settings: Any
    repository: DocumentRepository | None = None
    spool: TransientDocumentSpool | None = None
    ingestion: DocumentIngestionService | None = None
    archive_reader: PaperlessReadAdapter | None = None
    archive_client: PaperlessClient | None = None
    enqueuer: UnixDocumentEnqueueClient | None = None
    reprocessing: DocumentReprocessingService | None = None
    field_corrections: DocumentFieldCorrectionService | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> "DocumentGatewayContainer":
        if not bool(settings.documents_enabled):
            return cls(settings=settings)
        repository = DocumentRepository(settings.documents_database_path)
        spool = TransientDocumentSpool(
            settings.documents_spool_path,
            max_bytes=settings.documents_max_upload_bytes,
            quota_bytes=settings.documents_spool_quota_bytes,
            min_free_bytes=settings.documents_min_free_bytes,
            max_image_pixels=settings.documents_max_image_pixels,
        )
        enqueuer = UnixDocumentEnqueueClient(settings.document_job_socket_path)
        ingestion = DocumentIngestionService(repository=repository, spool=spool, enqueuer=enqueuer)
        archive_client = PaperlessClient(
            base_url=settings.paperless_base_url,
            token_path=settings.paperless_read_token_path,
            api_version=settings.paperless_api_version,
            server_version=settings.paperless_server_version,
            timeout_seconds=settings.paperless_timeout_seconds,
        )
        return cls(
            settings=settings,
            repository=repository,
            spool=spool,
            ingestion=ingestion,
            archive_reader=PaperlessReadAdapter(archive_client),
            archive_client=archive_client,
            enqueuer=enqueuer,
            reprocessing=(
                DocumentReprocessingService(
                    repository=repository,
                    enqueuer=enqueuer,
                    parser_name="docling" if settings.documents_docling_enabled else "",
                    parser_version=(settings.docling_server_version if settings.documents_docling_enabled else ""),
                    parser_image_digest=(settings.docling_image_digest if settings.documents_docling_enabled else ""),
                    configuration_sha256=(
                        native_docling_configuration_sha256(settings)
                        if settings.documents_docling_enabled
                        else ""
                    ),
                    conventional_parser_name=(
                        "paddleocr" if settings.documents_paddleocr_enabled else None
                    ),
                    conventional_parser_version=settings.paddleocr_server_version,
                    conventional_parser_image_digest=settings.paddleocr_image_digest,
                    conventional_configuration_sha256=(
                        conventional_ocr_configuration_sha256(settings)
                        if settings.documents_paddleocr_enabled
                        else None
                    ),
                    review_fallback_parser_name=(
                        "paddleocr-vl" if settings.documents_paddleocr_vl_enabled else None
                    ),
                    review_fallback_parser_version=settings.paddleocr_vl_framework_version,
                    review_fallback_parser_image_digest=settings.paddleocr_vl_image_digest,
                    review_fallback_configuration_sha256=(
                        vlm_fallback_configuration_sha256(settings)
                        if settings.documents_paddleocr_vl_enabled
                        else None
                    ),
                )
                if settings.documents_processing_enabled
                and (
                    settings.documents_docling_enabled
                    or settings.documents_paddleocr_enabled
                    or settings.documents_paddleocr_vl_enabled
                )
                else None
            ),
            field_corrections=DocumentFieldCorrectionService(repository),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.settings.documents_enabled)

    def readiness(self) -> dict[str, object]:
        if not self.enabled:
            return {"status": "disabled", "enabled": False}
        storage_ready = bool(self.repository and self.spool)
        socket_path = Path(self.settings.document_job_socket_path)
        queue_ready = socket_path.is_socket()
        if queue_ready and os.name == "posix":
            socket_stat = socket_path.stat()
            queue_ready = socket_stat.st_uid == os.getuid() and not (
                stat.S_IMODE(socket_stat.st_mode) & 0o077
            )
        archive_ready = bool(self.archive_client and self.archive_client.ready())
        spool_usage = self.spool.usage_bytes() if self.spool is not None else None
        spool_free = self.spool.free_bytes() if self.spool is not None else None
        free_space_ready = bool(
            spool_free is not None and spool_free >= int(self.settings.documents_min_free_bytes)
        )
        ready = storage_ready and queue_ready and archive_ready and free_space_ready
        return {
            "status": "ready" if ready else "degraded",
            "enabled": True,
            "storage": storage_ready,
            "queue": queue_ready,
            "archive": archive_ready,
            "free_space": free_space_ready,
            "spool_usage_bytes": spool_usage,
            "spool_quota_bytes": int(self.settings.documents_spool_quota_bytes),
            "state_counts": self.repository.state_counts() if self.repository is not None else {},
        }

    def close(self) -> None:
        if self.archive_client is not None:
            self.archive_client.close()
        if self.repository is not None:
            self.repository.close()
