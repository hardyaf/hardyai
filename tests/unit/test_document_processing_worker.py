from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.jobs.document_enqueue import DurableDocumentEnqueuer
from app.jobs.repository import DurableJobRepository
from app.skills.domains.documents.ingestion import TransientDocumentSpool
from app.skills.domains.documents.ports import ArchiveTask
from app.skills.domains.documents.service import DocumentIngestionService
from app.skills.domains.documents.storage import DocumentRepository
from app.workers.document_processing_worker import DocumentProcessingWorker


PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


class FakeArchive:
    provider_name = "paperless"

    def __init__(self) -> None:
        self.submissions = 0

    def submit(self, *, stream, filename: str, title: str) -> str:
        assert stream.read() == PDF
        assert filename == "bill.pdf"
        self.submissions += 1
        return "task-1"

    def task_status(self, task_ref: str) -> ArchiveTask:
        assert task_ref == "task-1"
        return ArchiveTask(task_ref=task_ref, state="succeeded", source_external_id="41")

    def grant_read_access(self, source_external_id: str) -> None:
        assert source_external_id == "41"

    def download_original(self, source_external_id: str):
        assert source_external_id == "41"
        yield PDF[:10]
        yield PDF[10:]


class FailingArchive(FakeArchive):
    def __init__(self, *, fail_at: str) -> None:
        super().__init__()
        self.fail_at = fail_at

    def submit(self, *, stream, filename: str, title: str) -> str:
        if self.fail_at == "submit":
            raise RuntimeError("paperless unavailable")
        return super().submit(stream=stream, filename=filename, title=title)

    def task_status(self, task_ref: str) -> ArchiveTask:
        if self.fail_at == "pending":
            return ArchiveTask(task_ref=task_ref, state="pending")
        return super().task_status(task_ref)

    def download_original(self, source_external_id: str):
        if self.fail_at == "checksum":
            yield b"not-the-original"
            return
        yield from super().download_original(source_external_id)


def _accepted_document(tmp_path, *, max_attempts: int = 3):
    jobs = DurableJobRepository(str(tmp_path / "core.db"))
    documents = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=1024, quota_bytes=4096)
    ingestion = DocumentIngestionService(
        repository=documents,
        spool=spool,
        enqueuer=DurableDocumentEnqueuer(jobs, max_attempts=max_attempts),
    )
    writer = spool.begin(filename="bill.pdf", declared_media_type="application/pdf", title="Bill")
    writer.write(PDF)
    return jobs, documents, spool, ingestion.accept(owner_id="operator", staged=writer.finish())


def test_worker_verifies_original_before_ready_and_removes_spool(tmp_path) -> None:
    jobs = DurableJobRepository(str(tmp_path / "core.db"))
    documents = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=1024, quota_bytes=4096)
    enqueuer = DurableDocumentEnqueuer(jobs, max_attempts=3)
    ingestion = DocumentIngestionService(repository=documents, spool=spool, enqueuer=enqueuer)
    writer = spool.begin(filename="bill.pdf", declared_media_type="application/pdf", title="Bill")
    writer.write(PDF)
    accepted = ingestion.accept(owner_id="operator", staged=writer.finish())
    spool_path = spool.path_for(accepted.record.spool_key)
    archive = FakeArchive()
    worker = DocumentProcessingWorker(
        jobs=jobs,
        documents=documents,
        spool=spool,
        archive=archive,
        worker_id="worker-1",
    )

    result = worker.run_once()
    ready = documents.get(accepted.record.document_id)

    assert result == [
        {"status": "ready", "document_id": accepted.record.document_id, "duplicate_reconciled": False}
    ]
    assert ready.state.value == "ready"
    assert ready.source_ref
    assert ready.spool_key is None
    assert spool_path.exists() is False
    assert jobs.list_jobs(job_type="document.archive.v1")[0]["status"] == "completed"
    jobs.close()
    documents.close()


def test_ready_job_recovery_removes_spool_after_crash_window(tmp_path) -> None:
    jobs = DurableJobRepository(str(tmp_path / "core.db"))
    documents = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=1024, quota_bytes=4096)
    ingestion = DocumentIngestionService(
        repository=documents,
        spool=spool,
        enqueuer=DurableDocumentEnqueuer(jobs, max_attempts=3),
    )
    writer = spool.begin(filename="bill.pdf", declared_media_type="application/pdf", title="Bill")
    writer.write(PDF)
    accepted = ingestion.accept(owner_id="operator", staged=writer.finish())
    documents.mark_archiving(document_id=accepted.record.document_id, task_ref="task-1")
    ready = documents.mark_ready(
        document_id=accepted.record.document_id,
        provider="paperless",
        external_id="41",
        verified_sha256=accepted.record.sha256,
    )
    assert ready.spool_key is not None
    spool_path = spool.path_for(ready.spool_key)

    worker = DocumentProcessingWorker(
        jobs=jobs,
        documents=documents,
        spool=spool,
        archive=FakeArchive(),
        worker_id="worker-recovery",
    )
    assert worker.run_once()[0]["reconciled"] is True
    assert spool_path.exists() is False
    assert documents.get(ready.document_id).spool_key is None
    jobs.close()
    documents.close()


@pytest.mark.parametrize(
    ("fail_at", "expected_state", "expected_error"),
    [
        ("submit", "queued", "RuntimeError"),
        ("pending", "archiving", "paperless_task_pending"),
    ],
)
def test_transient_archive_failure_retains_spool_and_reports_truthful_state(
    tmp_path,
    fail_at: str,
    expected_state: str,
    expected_error: str,
) -> None:
    jobs, documents, spool, accepted = _accepted_document(tmp_path)
    spool_path = spool.path_for(accepted.record.spool_key)
    worker = DocumentProcessingWorker(
        jobs=jobs,
        documents=documents,
        spool=spool,
        archive=FailingArchive(fail_at=fail_at),
        worker_id=f"worker-{fail_at}",
    )

    assert worker.run_once() == [{"status": "retry", "error_code": expected_error}]
    record = documents.get(accepted.record.document_id)
    job = jobs.list_jobs(job_type="document.archive.v1")[0]
    assert record.state.value == expected_state
    assert record.failure_code == expected_error
    assert spool_path.is_file()
    assert job["status"] == "retry"
    jobs.close()
    documents.close()


def test_checksum_mismatch_dead_letters_without_deleting_only_ingress_copy(tmp_path) -> None:
    jobs, documents, spool, accepted = _accepted_document(tmp_path, max_attempts=1)
    spool_path = spool.path_for(accepted.record.spool_key)
    worker = DocumentProcessingWorker(
        jobs=jobs,
        documents=documents,
        spool=spool,
        archive=FailingArchive(fail_at="checksum"),
        worker_id="worker-checksum",
    )

    assert worker.run_once() == [
        {"status": "dead_letter", "error_code": "archive_original_checksum_mismatch"}
    ]
    record = documents.get(accepted.record.document_id)
    assert record.state.value == "failed"
    assert record.source_ref is None
    assert record.failure_code == "archive_original_checksum_mismatch"
    assert spool_path.is_file()
    assert jobs.list_jobs(job_type="document.archive.v1")[0]["status"] == "dead_letter"
    jobs.close()
    documents.close()


def test_core_job_payload_is_content_free(tmp_path) -> None:
    jobs, documents, _spool, accepted = _accepted_document(tmp_path)

    job = jobs.list_jobs(job_type="document.archive.v1")[0]

    assert job["aggregate_id"] == accepted.record.document_id
    assert job["payload"] == {
        "document_id": accepted.record.document_id,
        "intake_id": accepted.record.intake_id,
        "sha256": accepted.record.sha256,
    }
    serialized = str(job["payload"])
    assert accepted.record.title not in serialized
    assert accepted.record.original_filename not in serialized
    assert "PDF" not in serialized
    jobs.close()
    documents.close()


def test_worker_periodically_recovers_accepted_intake_after_enqueue_outage(tmp_path) -> None:
    jobs = DurableJobRepository(str(tmp_path / "core.db"))
    documents = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=1024, quota_bytes=4096)

    class UnavailableEnqueuer:
        def enqueue_document(self, **kwargs) -> str:
            raise RuntimeError("queue unavailable")

    ingestion = DocumentIngestionService(
        repository=documents,
        spool=spool,
        enqueuer=UnavailableEnqueuer(),
    )
    writer = spool.begin(filename="bill.pdf", declared_media_type="application/pdf", title="Bill")
    writer.write(PDF)
    accepted = ingestion.accept(owner_id="operator", staged=writer.finish())
    assert accepted.record.state.value == "awaiting_enqueue"
    ingestion.enqueuer = DurableDocumentEnqueuer(jobs, max_attempts=3)
    worker = DocumentProcessingWorker(
        jobs=jobs,
        documents=documents,
        spool=spool,
        archive=FakeArchive(),
        ingestion=ingestion,
        worker_id="worker-recovery-loop",
    )

    assert worker.run_once()[0]["status"] == "ready"
    assert documents.get(accepted.record.document_id).state.value == "ready"
    assert jobs.list_jobs(job_type="document.archive.v1")[0]["status"] == "completed"
    jobs.close()
    documents.close()


def test_worker_reconciles_final_expired_lease_into_document_failure(tmp_path, monkeypatch) -> None:
    jobs, documents, spool, accepted = _accepted_document(tmp_path, max_attempts=1)
    started = datetime.now(UTC)
    claimed = jobs.claim_jobs(
        job_type="document.archive.v1",
        worker_id="crashed-worker",
        limit=1,
        lease_seconds=1,
        now=started,
    )
    assert len(claimed) == 1

    monkeypatch.setattr("app.jobs.repository._utc_now", lambda: started + timedelta(seconds=2))
    worker = DocumentProcessingWorker(
        jobs=jobs,
        documents=documents,
        spool=spool,
        archive=FakeArchive(),
        worker_id="replacement-worker",
    )

    assert worker.run_once() == [
        {
            "status": "dead_letter",
            "error_code": "lease_expired",
            "document_id": accepted.record.document_id,
            "reconciled": True,
        }
    ]
    record = documents.get(accepted.record.document_id)
    assert record.state.value == "failed"
    assert record.failure_code == "lease_expired"
    assert spool.path_for(record.spool_key).is_file()
    jobs.close()
    documents.close()
