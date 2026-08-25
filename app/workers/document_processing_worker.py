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
from app.integrations.paperless.adapter import PaperlessArchiveAdapter
from app.integrations.paperless.client import PaperlessClient
from app.jobs.document_enqueue import (
    DOCUMENT_ARCHIVE_JOB_TYPE,
    DOCUMENT_PROCESS_JOB_TYPE,
    DurableDocumentEnqueuer,
)
from app.jobs.enqueue_ipc import DocumentEnqueueSocketServer
from app.jobs.repository import DurableJobRepository
from app.jobs.types import JobStatus
from app.reviews.repository import HumanReviewRepository
from app.reviews.service import HumanReviewService
from app.services.offline_runtime_policy import validate_offline_runtime
from app.skills.domains.documents.artifacts import ContentAddressedArtifactStore
from app.skills.domains.documents.configuration import native_docling_configuration_sha256
from app.skills.domains.documents.ingestion import TransientDocumentSpool
from app.skills.domains.documents.processing import (
    DocumentProcessingError,
    DocumentProcessingPending,
    DocumentProcessingService,
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
        processing_enqueuer: DurableDocumentEnqueuer | None = None,
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
    ) -> None:
        self.jobs = jobs
        self.documents = documents
        self.spool = spool
        self.archive = archive
        self.ingestion = ingestion
        self.processing = processing
        self.processing_enqueuer = processing_enqueuer
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
        if self.processing is not None and self.processing_enqueuer is not None:
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
        if self.processing is not None:
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

    def _ensure_processing_queued(self, record) -> None:
        if (
            self.processing is None
            or self.processing_enqueuer is None
            or record.media_type != "application/pdf"
            or not record.source_version_id
        ):
            return
        if record.processing_state != ProcessingState.NOT_REQUESTED:
            return
        run = self.documents.create_processing_run(
            document_id=record.document_id,
            route=ProcessingRoute.NATIVE_DOCLING,
            parser_name=self.processing.parser.provider_name,
            parser_version=self.processing.parser.provider_version,
            parser_image_digest=self.parser_image_digest,
            configuration_sha256=self.processing_configuration_sha256,
            resource_lane="cpu_large",
        )
        self.processing_enqueuer.enqueue_processing(
            document_id=record.document_id,
            source_version_id=str(run["source_version_id"]),
            run_id=str(run["run_id"]),
        )

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
                assert self.processing is not None
                result = self.processing.process(
                    document_id=document_id,
                    source_version_id=source_version_id,
                    run_id=run_id,
                    fencing_token=token,
                )
                if not self.jobs.complete_job(
                    job_id=job_id,
                    worker_id=self.worker_id,
                    fencing_token=token,
                ):
                    raise DocumentProcessingError("stale_processing_job_fence")
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
                results.append({"status": "dead_letter" if terminal else "retry", "error_code": code})
        return results

    def _acknowledge_processing_cancel(
        self,
        *,
        job_id: str,
        run_id: str,
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
    processing: DocumentProcessingService | None = None
    processing_config_hash: str | None = None
    if settings.documents_processing_enabled and settings.documents_docling_enabled:
        reviews_repository = HumanReviewRepository(settings.database_path)
        docling_client = DoclingClient(
            base_url=settings.docling_base_url,
            api_key_path=settings.docling_api_key_path,
            server_version=settings.docling_server_version,
            timeout_seconds=settings.docling_timeout_seconds,
            max_response_bytes=settings.docling_max_response_bytes,
        )
        parser = DoclingParserAdapter(docling_client, provider_version=settings.docling_server_version)
        processing = DocumentProcessingService(
            repository=documents,
            archive=archive,
            parser=parser,
            artifact_store=ContentAddressedArtifactStore(settings.documents_artifacts_path),
            reviews=HumanReviewService(reviews_repository),
            max_provider_json_bytes=settings.docling_max_response_bytes,
        )
        processing_config_hash = native_docling_configuration_sha256(settings)
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
        processing=processing,
        processing_enqueuer=enqueuer if processing is not None else None,
        parser_image_digest=settings.docling_image_digest,
        processing_configuration_sha256=processing_config_hash,
        scanner=scanner,
        reconciler=reconciler,
        process_lease_seconds=settings.document_process_lease_seconds,
        poll_seconds=settings.document_archive_poll_seconds,
    )
    signal.signal(signal.SIGTERM, lambda *_: worker.request_stop())
    signal.signal(signal.SIGINT, lambda *_: worker.request_stop())
    try:
        worker.run_forever()
    finally:
        enqueue_server.close()
        if docling_client is not None:
            docling_client.close()
        if reviews_repository is not None:
            reviews_repository.close()
        archive_client.close()
        documents.close()
        jobs.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
