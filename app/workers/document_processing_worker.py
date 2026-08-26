from __future__ import annotations

import hashlib
import signal
import time
from pathlib import Path
from threading import Event
from typing import Any
from uuid import uuid4

from app.config import settings
from app.integrations.docling.adapter import DoclingParserAdapter
from app.integrations.docling.client import DoclingClient
from app.integrations.paddleocr.adapter import PaddleOCRParserAdapter
from app.integrations.paddleocr.client import PaddleOCRClient
from app.integrations.paddleocr_vl.adapter import PaddleOCRVLParserAdapter
from app.integrations.paddleocr_vl.client import PaddleOCRVLClient
from app.integrations.paperless.adapter import PaperlessArchiveAdapter
from app.integrations.paperless.client import PaperlessClient
from app.jobs.document_enqueue import (
    DOCUMENT_ARCHIVE_JOB_TYPE,
    DOCUMENT_PROCESS_JOB_TYPE,
    DurableDocumentEnqueuer,
)
from app.jobs.document_completion import DurableDocumentCompletionEnqueuer
from app.jobs.enqueue_ipc import DocumentEnqueueSocketServer
from app.jobs.repository import DurableJobRepository
from app.jobs.types import JobStatus
from app.reviews.repository import HumanReviewRepository
from app.reviews.service import HumanReviewService
from app.services.offline_runtime_policy import validate_offline_runtime
from app.skills.domains.documents.artifacts import ContentAddressedArtifactStore
from app.skills.domains.documents.configuration import (
    conventional_ocr_configuration_sha256,
    native_docling_configuration_sha256,
    vlm_fallback_configuration_sha256,
)
from app.skills.domains.documents.ingestion import TransientDocumentSpool
from app.skills.domains.documents.classification import DeterministicDocumentClassifier
from app.skills.domains.documents.enrichment import DocumentEnrichmentService
from app.skills.domains.documents.extraction import DeterministicStructuredExtractor
from app.skills.domains.documents.note_proposals import NoteProposalService
from app.skills.domains.documents.contact_proposals import ContactProposalService
from app.skills.domains.documents.intelligence import DocumentIntelligenceService
from app.restricted_documents.readiness import evaluate_restricted_workflow
from app.skills.domains.documents.processing import (
    DocumentProcessingError,
    DocumentProcessingPending,
    DocumentProcessingService,
)
from app.skills.domains.documents.quality import (
    evaluate_conventional_ocr_artifact,
    evaluate_vlm_fallback_artifact,
)
from app.skills.domains.documents.reconciliation import DocumentOriginReconciler
from app.skills.domains.documents.scanner import WatchedDocumentScanner
from app.skills.domains.documents.service import DocumentIngestionService
from app.skills.domains.documents.storage import DocumentRepository, DocumentStorageError
from app.skills.domains.documents.types import DocumentState, ProcessingRoute, ProcessingState


class DocumentArchiveError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code[:120]


class DocumentProcessingWorker:
    def __init__(
        self,
        *,
        jobs: DurableJobRepository,
        documents: DocumentRepository,
        spool: TransientDocumentSpool,
        archive: PaperlessArchiveAdapter,
        ingestion: DocumentIngestionService | None = None,
        processing: DocumentProcessingService | None = None,
        processing_services: dict[ProcessingRoute, DocumentProcessingService] | None = None,
        processing_route_metadata: dict[ProcessingRoute, dict[str, str | None]] | None = None,
        processing_enqueuer: DurableDocumentEnqueuer | None = None,
        completion_enqueuer: DurableDocumentCompletionEnqueuer | None = None,
        parser_image_digest: str | None = None,
        processing_configuration_sha256: str | None = None,
        scanner: WatchedDocumentScanner | None = None,
        reconciler: DocumentOriginReconciler | None = None,
        reconciliation_interval_seconds: float = 300.0,
        worker_id: str | None = None,
        batch_size: int = 2,
        lease_seconds: float = 180.0,
        process_lease_seconds: float = 300.0,
        poll_seconds: float = 5.0,
        archive_read_grant_deferred: bool = False,
    ) -> None:
        self.jobs = jobs
        self.documents = documents
        self.spool = spool
        self.archive = archive
        self.ingestion = ingestion
        self.processing = processing
        self.processing_services = dict(processing_services or {})
        if processing is not None and ProcessingRoute.NATIVE_DOCLING not in self.processing_services:
            self.processing_services[ProcessingRoute.NATIVE_DOCLING] = processing
        self.processing_route_metadata = dict(processing_route_metadata or {})
        self.processing_enqueuer = processing_enqueuer
        self.completion_enqueuer = completion_enqueuer
        self.parser_image_digest = str(parser_image_digest or "")[:160] or None
        self.processing_configuration_sha256 = str(
            processing_configuration_sha256 or hashlib.sha256(b"docling-native-v1").hexdigest()
        )
        self.scanner = scanner
        self.reconciler = reconciler
        self.reconciliation_interval_seconds = max(
            30.0,
            min(float(reconciliation_interval_seconds), 86400.0),
        )
        self._last_reconciliation = 0.0
        self.worker_id = worker_id or f"document-worker-{uuid4()}"
        self.batch_size = max(1, min(int(batch_size), 10))
        self.lease_seconds = max(30.0, min(float(lease_seconds), 900.0))
        self.process_lease_seconds = max(30.0, min(float(process_lease_seconds), 1800.0))
        self.poll_seconds = max(1.0, min(float(poll_seconds), 300.0))
        self.archive_read_grant_deferred = bool(archive_read_grant_deferred)
        self._stop = Event()

    def request_stop(self) -> None:
        self._stop.set()

    def run_once(self) -> list[dict[str, Any]]:
        self.jobs.record_worker_heartbeat(
            worker_type="documents",
            worker_id=self.worker_id,
            status="polling",
        )
        maintenance: dict[str, Any] = {}
        recovery_error: str | None = None
        if self.ingestion is not None:
            try:
                maintenance["awaiting_enqueue_recovered"] = self.ingestion.recover_awaiting_enqueue(
                    limit=100
                )
            except Exception as exc:
                recovery_error = f"awaiting_enqueue_{type(exc).__name__}"[:120]
        if self.processing_services and self.processing_enqueuer is not None:
            try:
                recovered = 0
                for run in self.documents.pending_processing_runs(limit=100):
                    self.processing_enqueuer.enqueue_processing(
                        document_id=str(run["document_id"]),
                        source_version_id=str(run["source_version_id"]),
                        run_id=str(run["run_id"]),
                    )
                    recovered += 1
                maintenance["processing_enqueue_recovered"] = recovered
                queued_existing = 0
                for record in self.documents.ready_unprocessed(limit=100):
                    if self._ensure_processing_queued(record):
                        queued_existing += 1
                maintenance["ready_processing_queued"] = queued_existing
            except Exception as exc:
                recovery_error = f"processing_enqueue_{type(exc).__name__}"[:120]
        if self.scanner is not None:
            try:
                maintenance["scanner"] = self.scanner.scan_once()
            except Exception as exc:
                recovery_error = f"scanner_{type(exc).__name__}"[:120]
        if (
            self.reconciler is not None
            and time.monotonic() - self._last_reconciliation >= self.reconciliation_interval_seconds
        ):
            try:
                maintenance["origin_reconciliation"] = self.reconciler.reconcile()
                self._last_reconciliation = time.monotonic()
            except Exception as exc:
                recovery_error = f"origin_reconciliation_{type(exc).__name__}"[:120]

        results = self._run_archive_jobs()
        if self.processing_services:
            results.extend(self._run_processing_jobs())
        errors = [
            result
            for result in results
            if result.get("status") in {"retry", "dead_letter", "cancelled"}
        ]
        self.jobs.record_worker_heartbeat(
            worker_type="documents",
            worker_id=self.worker_id,
            status="degraded" if errors or recovery_error else "idle",
            last_error_code=(str(errors[-1].get("error_code")) if errors else recovery_error),
            metadata={
                "result_count": len(results),
                "error_count": len(errors),
                **maintenance,
            },
        )
        return results

    def _run_archive_jobs(self) -> list[dict[str, Any]]:
        claimed = self.jobs.claim_jobs(
            job_type=DOCUMENT_ARCHIVE_JOB_TYPE,
            worker_id=self.worker_id,
            limit=self.batch_size,
            lease_seconds=self.lease_seconds,
        )
        results: list[dict[str, Any]] = []
        for job in claimed:
            token = int(job.get("lease_fencing_token") or 0)
            try:
                self.jobs.update_progress(
                    job_id=str(job["job_id"]),
                    worker_id=self.worker_id,
                    fencing_token=token,
                    stage="archive",
                    current=0,
                    total=1,
                )
                result = self._process_archive(job)
                if not self.jobs.complete_job(
                    job_id=str(job["job_id"]),
                    worker_id=self.worker_id,
                    fencing_token=token,
                ):
                    if self.jobs.acknowledge_cancel(
                        job_id=str(job["job_id"]),
                        worker_id=self.worker_id,
                        fencing_token=token,
                    ):
                        result = {"status": "cancelled", "document_id": self._document_id(job)}
                    else:
                        raise DocumentArchiveError("stale_archive_job_fence")
                results.append(result)
            except Exception as exc:
                results.append(self._retry_archive(job=job, fencing_token=token, exc=exc))
        self._reconcile_archive_dead_letters(results)
        return results

    def _retry_archive(
        self,
        *,
        job: dict[str, Any],
        fencing_token: int,
        exc: Exception,
    ) -> dict[str, Any]:
        code = str(getattr(exc, "code", "") or type(exc).__name__)[:120]
        attempt = int(job.get("attempt_count") or 1)
        self.jobs.retry_job(
            job_id=str(job["job_id"]),
            worker_id=self.worker_id,
            fencing_token=fencing_token,
            error_code=code,
            delay_seconds=min(300.0, self.poll_seconds * (2 ** min(max(0, attempt - 1), 6))),
        )
        persisted = self.jobs.get_job(str(job["job_id"])) or {}
        status = str(persisted.get("status") or JobStatus.RETRY.value)
        document_id = self._document_id(job)
        if self.documents.get(document_id) is not None:
            self.documents.mark_failure(
                document_id=document_id,
                error_code=code,
                terminal=status == JobStatus.DEAD_LETTER.value,
            )
        if status == JobStatus.DEAD_LETTER.value:
            self._signal_terminal(document_id=document_id, state="failed")
        return {
            "status": "dead_letter" if status == JobStatus.DEAD_LETTER.value else "retry",
            "error_code": code,
        }

    def _reconcile_archive_dead_letters(self, results: list[dict[str, Any]]) -> None:
        for dead_job in self.jobs.list_jobs(
            job_type=DOCUMENT_ARCHIVE_JOB_TYPE,
            status=JobStatus.DEAD_LETTER,
            limit=100,
        ):
            document_id = self._document_id(dead_job)
            record = self.documents.get(document_id)
            if record is None or record.state in {DocumentState.READY, DocumentState.FAILED}:
                continue
            code = str(dead_job.get("last_error_code") or "document_job_dead_letter")[:120]
            self.documents.mark_failure(document_id=document_id, error_code=code, terminal=True)
            self._signal_terminal(document_id=document_id, state="failed")
            results.append(
                {
                    "status": "dead_letter",
                    "error_code": code,
                    "document_id": document_id,
                    "reconciled": True,
                }
            )

    def _process_archive(self, job: dict[str, Any]) -> dict[str, Any]:
        document_id = self._document_id(job)
        record = self.documents.get(document_id)
        if record is None:
            raise DocumentArchiveError("document_record_missing")
        if record.state == DocumentState.READY:
            if record.spool_key:
                self.spool.delete(record.spool_key)
                record = self.documents.clear_spool(
                    document_id=document_id,
                    expected_spool_key=record.spool_key,
                )
            self._ensure_processing_queued(record)
            return {"status": "ready", "document_id": document_id, "reconciled": True}
        if not record.spool_key:
            raise DocumentArchiveError("document_spool_missing")
        task_ref = record.archive_task_ref
        if not task_ref:
            with self.spool.open_read(record.spool_key) as source:
                task_ref = self.archive.submit(
                    stream=source,
                    filename=record.original_filename,
                    title=record.title,
                )
            record = self.documents.mark_archiving(document_id=document_id, task_ref=task_ref)
        task = self.archive.task_status(task_ref)
        if task.state == "pending":
            raise DocumentArchiveError("paperless_task_pending")
        if task.state not in {"succeeded", "duplicate"} or not task.source_external_id:
            raise DocumentArchiveError(task.error_code or "paperless_task_failed")
        if not self.archive_read_grant_deferred:
            self.archive.grant_read_access(task.source_external_id)
        observed_hash = hashlib.sha256()
        observed_size = 0
        for chunk in self.archive.download_original(task.source_external_id):
            observed_size += len(chunk)
            if observed_size > record.size_bytes:
                raise DocumentArchiveError("archive_original_size_mismatch")
            observed_hash.update(chunk)
        if observed_size != record.size_bytes or observed_hash.hexdigest() != record.sha256:
            raise DocumentArchiveError("archive_original_checksum_mismatch")
        spool_key = record.spool_key
        ready = self.documents.mark_ready(
            document_id=document_id,
            provider=self.archive.provider_name,
            external_id=task.source_external_id,
            verified_sha256=record.sha256,
        )
        if self.archive_read_grant_deferred:
            self.documents.set_archive_text_visibility(
                document_id=document_id,
                visible=False,
            )
            ready = self.documents.get(document_id) or ready
        self.spool.delete(spool_key)
        ready = self.documents.clear_spool(
            document_id=document_id,
            expected_spool_key=spool_key,
        )
        self._ensure_processing_queued(ready)
        return {
            "status": "ready",
            "document_id": ready.document_id,
            "duplicate_reconciled": task.state == "duplicate",
        }

    def _ensure_processing_queued(self, record) -> bool:
        if self.processing_enqueuer is None or not record.source_version_id:
            return False
        if record.processing_state != ProcessingState.NOT_REQUESTED:
            return False
        if record.media_type == "application/pdf":
            route = ProcessingRoute.NATIVE_DOCLING
        elif record.media_type in {"image/jpeg", "image/png"}:
            route = ProcessingRoute.CONVENTIONAL_OCR
        else:
            return False
        processing = self.processing_services.get(route)
        if processing is None:
            return False
        metadata = self.processing_route_metadata.get(route, {})
        run = self.documents.create_processing_run(
            document_id=record.document_id,
            route=route,
            parser_name=processing.parser.provider_name,
            parser_version=processing.parser.provider_version,
            parser_image_digest=(
                str(metadata.get("image_digest") or "")[:160] or self.parser_image_digest
            ),
            configuration_sha256=(
                str(metadata.get("configuration_sha256") or "")
                or self.processing_configuration_sha256
            ),
            artifact_schema_version=("2" if route == ProcessingRoute.CONVENTIONAL_OCR else "1"),
            resource_lane=str(metadata.get("resource_lane") or "cpu_large")[:40],
        )
        self.processing_enqueuer.enqueue_processing(
            document_id=record.document_id,
            source_version_id=str(run["source_version_id"]),
            run_id=str(run["run_id"]),
        )
        return True

    def _run_processing_jobs(self) -> list[dict[str, Any]]:
        claimed = self.jobs.claim_jobs(
            job_type=DOCUMENT_PROCESS_JOB_TYPE,
            worker_id=self.worker_id,
            limit=self.batch_size,
            lease_seconds=self.process_lease_seconds,
        )
        results: list[dict[str, Any]] = []
        for job in claimed:
            job_id = str(job["job_id"])
            token = int(job.get("lease_fencing_token") or 0)
            payload = job.get("payload") or {}
            run_id = str(payload.get("run_id") or "")
            document_id = str(payload.get("document_id") or "")
            source_version_id = str(payload.get("source_version_id") or "")
            try:
                if not all((run_id, document_id, source_version_id)):
                    raise DocumentProcessingError("processing_job_payload_invalid")
                if not self.jobs.update_progress(
                    job_id=job_id,
                    worker_id=self.worker_id,
                    fencing_token=token,
                    stage="parser_reconcile",
                    current=0,
                    total=3,
                ):
                    raise DocumentProcessingError("stale_processing_job_fence")
                run = self.documents.get_processing_run(run_id)
                if run is None:
                    raise DocumentProcessingError("processing_run_missing")
                try:
                    route = ProcessingRoute(str(run.get("route") or ""))
                except ValueError as exc:
                    raise DocumentProcessingError("processing_route_invalid") from exc
                processing = self.processing_services.get(route)
                if processing is None:
                    raise DocumentProcessingError("processing_route_unavailable")
                result = processing.process(
                    document_id=document_id,
                    source_version_id=source_version_id,
                    run_id=run_id,
                    fencing_token=token,
                )
                if (
                    route == ProcessingRoute.NATIVE_DOCLING
                    and result.get("status") in {"needs_review", "processing_incomplete"}
                    and ProcessingRoute.CONVENTIONAL_OCR in self.processing_services
                ):
                    result["fallback"] = self._enqueue_fallback(
                        document_id=document_id,
                        fallback_from_run_id=run_id,
                        route=ProcessingRoute.CONVENTIONAL_OCR,
                    )
                elif (
                    route == ProcessingRoute.CONVENTIONAL_OCR
                    and result.get("status") in {"needs_review", "processing_incomplete"}
                    and ProcessingRoute.VLM_FALLBACK in self.processing_services
                ):
                    result["fallback"] = self._enqueue_fallback(
                        document_id=document_id,
                        fallback_from_run_id=run_id,
                        route=ProcessingRoute.VLM_FALLBACK,
                    )
                if not self.jobs.complete_job(
                    job_id=job_id,
                    worker_id=self.worker_id,
                    fencing_token=token,
                ):
                    raise DocumentProcessingError("stale_processing_job_fence")
                if self._processing_result_is_terminal(result):
                    self._signal_terminal(
                        document_id=document_id,
                        state=str(result.get("status") or "complete"),
                    )
                results.append(result)
            except DocumentProcessingPending as exc:
                run = self.documents.get_processing_run(run_id) or {}
                operation_ref = str(run.get("provider_operation_ref") or "")
                if operation_ref:
                    self.jobs.set_provider_operation(
                        job_id=job_id,
                        worker_id=self.worker_id,
                        fencing_token=token,
                        operation_ref=operation_ref,
                        reconcile_state="pending",
                    )
                if not self.jobs.defer_job(
                    job_id=job_id,
                    worker_id=self.worker_id,
                    fencing_token=token,
                    delay_seconds=self.poll_seconds,
                    reconcile_state=str(exc.code),
                ):
                    self._acknowledge_processing_cancel(
                        job_id=job_id,
                        run_id=run_id,
                        document_id=document_id,
                        fencing_token=token,
                    )
                results.append(
                    {
                        "status": "provider_pending",
                        "document_id": document_id,
                        "run_id": run_id,
                    }
                )
            except Exception as exc:
                code = str(getattr(exc, "code", "") or type(exc).__name__)[:120]
                attempt = int(job.get("attempt_count") or 1)
                self.jobs.retry_job(
                    job_id=job_id,
                    worker_id=self.worker_id,
                    fencing_token=token,
                    error_code=code,
                    delay_seconds=min(300.0, self.poll_seconds * (2 ** min(max(0, attempt - 1), 6))),
                )
                persisted = self.jobs.get_job(job_id) or {}
                terminal = persisted.get("status") == JobStatus.DEAD_LETTER.value
                if terminal:
                    self._fail_processing_run(run_id=run_id, fencing_token=token, code=code)
                    self._signal_terminal(document_id=document_id, state="failed")
                results.append({"status": "dead_letter" if terminal else "retry", "error_code": code})
        return results

    def _enqueue_fallback(
        self,
        *,
        document_id: str,
        fallback_from_run_id: str,
        route: ProcessingRoute,
    ) -> dict[str, Any]:
        processing = self.processing_services[route]
        metadata = self.processing_route_metadata.get(route, {})
        record = self.documents.get(document_id)
        if (
            route == ProcessingRoute.VLM_FALLBACK
            and (record is None or record.media_type not in {"image/jpeg", "image/png"})
        ):
            return {"status": "not_queued", "error_code": "vlm_media_type_unsupported"}
        schema_version = {
            ProcessingRoute.NATIVE_DOCLING: "1",
            ProcessingRoute.CONVENTIONAL_OCR: "2",
            ProcessingRoute.VLM_FALLBACK: "3",
        }[route]
        try:
            run = self.documents.create_processing_run(
                document_id=document_id,
                route=route,
                parser_name=processing.parser.provider_name,
                parser_version=processing.parser.provider_version,
                parser_image_digest=str(metadata.get("image_digest") or "")[:160] or None,
                configuration_sha256=str(metadata.get("configuration_sha256") or ""),
                artifact_schema_version=schema_version,
                resource_lane=str(metadata.get("resource_lane") or "cpu")[:40],
                fallback_from_run_id=fallback_from_run_id,
            )
        except Exception as exc:
            return {"status": "not_queued", "error_code": type(exc).__name__}
        enqueue_confirmed = True
        try:
            assert self.processing_enqueuer is not None
            job_id = self.processing_enqueuer.enqueue_processing(
                document_id=document_id,
                source_version_id=str(run["source_version_id"]),
                run_id=str(run["run_id"]),
            )
        except Exception:
            enqueue_confirmed = False
            job_id = None
        return {
            "status": "queued" if enqueue_confirmed else "awaiting_enqueue_recovery",
            "route": route.value,
            "run_id": str(run["run_id"]),
            "job_id": job_id,
        }

    def _acknowledge_processing_cancel(
        self,
        *,
        job_id: str,
        run_id: str,
        document_id: str,
        fencing_token: int,
    ) -> None:
        if self.jobs.acknowledge_cancel(
            job_id=job_id,
            worker_id=self.worker_id,
            fencing_token=fencing_token,
        ):
            try:
                self.documents.finish_processing_run(
                    run_id=run_id,
                    fencing_token=fencing_token,
                    state=ProcessingState.CANCELLED,
                    error_code="cancelled",
                )
                self._signal_terminal(document_id=document_id, state="cancelled")
            except (DocumentStorageError, KeyError):
                pass

    def _fail_processing_run(self, *, run_id: str, fencing_token: int, code: str) -> None:
        try:
            run = self.documents.get_processing_run(run_id)
            if run is not None and run.get("status") == ProcessingState.PROCESSING.value:
                self.documents.finish_processing_run(
                    run_id=run_id,
                    fencing_token=fencing_token,
                    state=ProcessingState.FAILED,
                    error_code=code,
                )
        except (DocumentStorageError, KeyError):
            pass

    @staticmethod
    def _processing_result_is_terminal(result: dict[str, Any]) -> bool:
        fallback = result.get("fallback")
        if isinstance(fallback, dict) and fallback.get("status") in {
            "queued",
            "awaiting_enqueue_recovery",
        }:
            return False
        return str(result.get("status") or "").strip().casefold() in {
            "complete",
            "needs_review",
            "processing_incomplete",
            "failed",
            "cancelled",
            "protected_pending",
        }

    def _signal_terminal(self, *, document_id: str, state: str) -> None:
        if self.completion_enqueuer is None:
            return
        try:
            self.completion_enqueuer.signal_terminal(document_id=document_id, state=state)
        except Exception:
            # Notification jobs also poll with a bounded delay, closing the
            # documents.db/core.db cross-store crash or lock window.
            return

    @staticmethod
    def _document_id(job: dict[str, Any]) -> str:
        return str((job.get("payload") or {}).get("document_id") or job.get("aggregate_id") or "")

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.poll_seconds)


def _read_positive_id(path_value: str) -> int:
    path = Path(path_value)
    if path.is_symlink():
        raise RuntimeError("Paperless read-user ID path must not be a symlink")
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("Paperless read-user ID file is unavailable") from exc
    if not text.isdigit() or int(text) <= 0:
        raise RuntimeError("Paperless read-user ID file is invalid")
    return int(text)


def main() -> int:
    if not settings.documents_enabled:
        raise RuntimeError("DOCUMENTS_ENABLED must be true to run the document worker.")
    if settings.documents_note_proposals_enabled and not settings.documents_safe_extraction_enabled:
        raise RuntimeError("DOCUMENTS_NOTE_PROPOSALS_ENABLED requires safe extraction.")
    if settings.documents_contact_proposals_enabled and not settings.documents_safe_extraction_enabled:
        raise RuntimeError("DOCUMENTS_CONTACT_PROPOSALS_ENABLED requires safe extraction.")
    if settings.documents_intelligence_enabled and not settings.documents_safe_extraction_enabled:
        raise RuntimeError("DOCUMENTS_INTELLIGENCE_ENABLED requires safe extraction.")
    restricted = evaluate_restricted_workflow(
        enabled=settings.documents_restricted_workflow_enabled,
        cipher_configured=False,
        isolated_store_configured=False,
        security_review_id=settings.documents_restricted_security_review_id,
        recovery_attestation_path=settings.documents_restricted_recovery_attestation_path,
    )
    if settings.documents_restricted_workflow_enabled and not restricted.ready:
        raise RuntimeError("Restricted document workflow is blocked: " + ",".join(restricted.reasons))
    validate_offline_runtime(settings, entrypoint="document-worker")
    jobs = DurableJobRepository(settings.database_path)
    documents = DocumentRepository(settings.documents_database_path)
    spool = TransientDocumentSpool(
        settings.documents_spool_path,
        max_bytes=settings.documents_max_upload_bytes,
        quota_bytes=settings.documents_spool_quota_bytes,
        min_free_bytes=settings.documents_min_free_bytes,
        max_image_pixels=settings.documents_max_image_pixels,
    )
    enqueuer = DurableDocumentEnqueuer(
        jobs,
        max_attempts=settings.document_archive_max_attempts,
        processing_max_attempts=settings.document_process_max_attempts,
    )
    completion_enqueuer = DurableDocumentCompletionEnqueuer(jobs)
    archive_client = PaperlessClient(
        base_url=settings.paperless_base_url,
        token_path=settings.paperless_archive_token_path,
        api_version=settings.paperless_api_version,
        server_version=settings.paperless_server_version,
        timeout_seconds=settings.paperless_timeout_seconds,
    )
    archive = PaperlessArchiveAdapter(
        archive_client,
        read_user_id=_read_positive_id(settings.paperless_read_user_id_path),
    )
    ingestion = DocumentIngestionService(repository=documents, spool=spool, enqueuer=enqueuer)
    reviews_repository: HumanReviewRepository | None = None
    docling_client: DoclingClient | None = None
    paddleocr_client: PaddleOCRClient | None = None
    paddleocr_vl_client: PaddleOCRVLClient | None = None
    processing_services: dict[ProcessingRoute, DocumentProcessingService] = {}
    route_metadata: dict[ProcessingRoute, dict[str, str | None]] = {}
    artifact_store = ContentAddressedArtifactStore(settings.documents_artifacts_path)
    review_service: HumanReviewService | None = None
    if settings.documents_processing_enabled and (
        settings.documents_docling_enabled
        or settings.documents_paddleocr_enabled
        or settings.documents_paddleocr_vl_enabled
    ):
        reviews_repository = HumanReviewRepository(settings.database_path)
        review_service = HumanReviewService(reviews_repository)
    enrichment_service = (
        DocumentEnrichmentService(
            repository=documents,
            classifier=DeterministicDocumentClassifier(),
            extractor=DeterministicStructuredExtractor(),
            reviews=review_service,
            archive_access=archive,
            note_proposals=(
                NoteProposalService(repository=documents, reviews=review_service)
                if settings.documents_note_proposals_enabled
                else None
            ),
            contact_proposals=(
                ContactProposalService(repository=documents, reviews=review_service)
                if settings.documents_contact_proposals_enabled
                else None
            ),
            intelligence=(
                DocumentIntelligenceService(repository=documents, reviews=review_service)
                if settings.documents_intelligence_enabled
                else None
            ),
        )
        if settings.documents_safe_extraction_enabled and review_service is not None
        else None
    )
    if settings.documents_processing_enabled and settings.documents_docling_enabled:
        docling_client = DoclingClient(
            base_url=settings.docling_base_url,
            api_key_path=settings.docling_api_key_path,
            server_version=settings.docling_server_version,
            timeout_seconds=settings.docling_timeout_seconds,
            max_response_bytes=settings.docling_max_response_bytes,
        )
        parser = DoclingParserAdapter(docling_client, provider_version=settings.docling_server_version)
        processing_services[ProcessingRoute.NATIVE_DOCLING] = DocumentProcessingService(
            repository=documents,
            archive=archive,
            parser=parser,
            artifact_store=artifact_store,
            reviews=(
                None
                if settings.documents_paddleocr_enabled or settings.documents_paddleocr_vl_enabled
                else review_service
            ),
            max_provider_json_bytes=settings.docling_max_response_bytes,
            enrichment=enrichment_service,
        )
        route_metadata[ProcessingRoute.NATIVE_DOCLING] = {
            "image_digest": settings.docling_image_digest,
            "configuration_sha256": native_docling_configuration_sha256(settings),
            "resource_lane": "cpu_large",
        }
    if settings.documents_processing_enabled and settings.documents_paddleocr_enabled:
        paddleocr_client = PaddleOCRClient(
            base_url=settings.paddleocr_base_url,
            api_key_path=settings.paddleocr_api_key_path,
            server_version=settings.paddleocr_server_version,
            timeout_seconds=settings.paddleocr_timeout_seconds,
            max_input_bytes=settings.documents_max_upload_bytes,
            max_response_bytes=settings.paddleocr_max_response_bytes,
        )
        parser = PaddleOCRParserAdapter(
            paddleocr_client,
            provider_version=settings.paddleocr_server_version,
        )
        processing_services[ProcessingRoute.CONVENTIONAL_OCR] = DocumentProcessingService(
            repository=documents,
            archive=archive,
            parser=parser,
            artifact_store=artifact_store,
            reviews=(None if settings.documents_paddleocr_vl_enabled else review_service),
            max_provider_json_bytes=settings.paddleocr_max_response_bytes,
            quality_evaluator=evaluate_conventional_ocr_artifact,
            enrichment=enrichment_service,
        )
        route_metadata[ProcessingRoute.CONVENTIONAL_OCR] = {
            "image_digest": settings.paddleocr_image_digest,
            "configuration_sha256": conventional_ocr_configuration_sha256(settings),
            "resource_lane": "cpu_ocr",
        }
    if settings.documents_processing_enabled and settings.documents_paddleocr_vl_enabled:
        paddleocr_vl_client = PaddleOCRVLClient(
            base_url=settings.paddleocr_vl_base_url,
            framework_version=settings.paddleocr_vl_framework_version,
            pipeline_version=settings.paddleocr_vl_pipeline_version,
            timeout_seconds=settings.paddleocr_vl_timeout_seconds,
            max_input_bytes=settings.documents_max_upload_bytes,
            max_response_bytes=settings.paddleocr_vl_max_response_bytes,
        )
        parser = PaddleOCRVLParserAdapter(
            paddleocr_vl_client,
            provider_version=settings.paddleocr_vl_framework_version,
        )

        def evaluate_vlm_with_conventional_evidence(artifact):
            run = documents.get_processing_run(artifact.run_id) or {}
            fallback_run_id = str(run.get("fallback_from_run_id") or "")
            reference_texts = tuple(
                str(block.get("literal_text") or "")
                for block in documents.processing_run_blocks(fallback_run_id)
            )
            return evaluate_vlm_fallback_artifact(
                artifact,
                reference_texts=reference_texts,
            )

        processing_services[ProcessingRoute.VLM_FALLBACK] = DocumentProcessingService(
            repository=documents,
            archive=archive,
            parser=parser,
            artifact_store=artifact_store,
            reviews=review_service,
            max_provider_json_bytes=settings.paddleocr_vl_max_response_bytes,
            quality_evaluator=evaluate_vlm_with_conventional_evidence,
            enrichment=enrichment_service,
        )
        route_metadata[ProcessingRoute.VLM_FALLBACK] = {
            "image_digest": settings.paddleocr_vl_image_digest,
            "configuration_sha256": vlm_fallback_configuration_sha256(settings),
            "resource_lane": "gpu_vlm",
        }
    scanner = (
        WatchedDocumentScanner(
            root=settings.documents_watch_path,
            spool=spool,
            ingestion=ingestion,
            owner_id=settings.documents_watch_owner_id,
            stable_seconds=settings.documents_watch_stable_seconds,
        )
        if settings.documents_watch_enabled
        else None
    )
    reconciler = (
        DocumentOriginReconciler(
            repository=documents,
            provider=archive,
            owner_id=settings.documents_origin_owner_id,
            max_source_bytes=settings.documents_max_upload_bytes,
        )
        if settings.documents_origin_reconciliation_enabled
        else None
    )
    enqueue_server = DocumentEnqueueSocketServer(settings.document_job_socket_path, enqueuer)
    enqueue_server.start()
    worker = DocumentProcessingWorker(
        jobs=jobs,
        documents=documents,
        spool=spool,
        archive=archive,
        ingestion=ingestion,
        processing_services=processing_services,
        processing_route_metadata=route_metadata,
        processing_enqueuer=enqueuer if processing_services else None,
        completion_enqueuer=completion_enqueuer,
        parser_image_digest=settings.docling_image_digest,
        scanner=scanner,
        reconciler=reconciler,
        process_lease_seconds=settings.document_process_lease_seconds,
        poll_seconds=settings.document_archive_poll_seconds,
        archive_read_grant_deferred=settings.documents_safe_extraction_enabled,
    )
    signal.signal(signal.SIGTERM, lambda *_: worker.request_stop())
    signal.signal(signal.SIGINT, lambda *_: worker.request_stop())
    try:
        worker.run_forever()
    finally:
        enqueue_server.close()
        if docling_client is not None:
            docling_client.close()
        if paddleocr_client is not None:
            paddleocr_client.close()
        if paddleocr_vl_client is not None:
            paddleocr_vl_client.close()
        if reviews_repository is not None:
            reviews_repository.close()
        archive_client.close()
        documents.close()
        jobs.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
