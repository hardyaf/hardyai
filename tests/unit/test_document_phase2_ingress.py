from __future__ import annotations

import os

import pytest

from app.jobs.document_enqueue import DurableDocumentEnqueuer
from app.jobs.repository import DurableJobRepository
from app.skills.domains.documents.ingestion import TransientDocumentSpool
from app.skills.domains.documents.ports import ArchiveOrigin
from app.skills.domains.documents.reconciliation import DocumentOriginReconciler
from app.skills.domains.documents.scanner import WatchedDocumentScanner
from app.skills.domains.documents.service import DocumentIngestionService
from app.skills.domains.documents.storage import DocumentRepository


PDF = b"%PDF-1.4\norigin text\n%%EOF\n"


def _ingestion(tmp_path):
    jobs = DurableJobRepository(str(tmp_path / "core.db"))
    documents = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=4096, quota_bytes=16384)
    service = DocumentIngestionService(
        repository=documents,
        spool=spool,
        enqueuer=DurableDocumentEnqueuer(jobs, max_attempts=3),
    )
    return jobs, documents, spool, service


def test_watched_scanner_waits_for_stability_and_uses_normal_ingest_path(tmp_path, monkeypatch) -> None:
    jobs, documents, spool, ingestion = _ingestion(tmp_path)
    watched = tmp_path / "watched"
    scanner = WatchedDocumentScanner(
        root=str(watched),
        spool=spool,
        ingestion=ingestion,
        owner_id="operator",
        stable_seconds=1,
    )
    (watched / "statement.pdf").write_bytes(PDF)
    clock = iter((10.0, 10.5, 11.1))
    monkeypatch.setattr("app.skills.domains.documents.scanner.time.monotonic", lambda: next(clock))

    assert scanner.scan_once() == []
    assert scanner.scan_once() == []
    result = scanner.scan_once()
    assert result[0]["status"] == "queued"
    assert not (watched / "statement.pdf").exists()
    job = jobs.list_jobs(job_type="document.archive.v1")[0]
    record = documents.get(result[0]["document_id"])
    assert record.sha256 == job["payload"]["sha256"]
    assert record.original_filename == "statement.pdf"
    jobs.close()
    documents.close()


def test_watched_scanner_skips_nested_hidden_and_symlink_inputs(tmp_path) -> None:
    jobs, documents, spool, ingestion = _ingestion(tmp_path)
    watched = tmp_path / "watched"
    scanner = WatchedDocumentScanner(
        root=str(watched),
        spool=spool,
        ingestion=ingestion,
        owner_id="operator",
        stable_seconds=0.5,
    )
    (watched / ".hidden.pdf").write_bytes(PDF)
    nested = watched / "nested"
    nested.mkdir()
    (nested / "nested.pdf").write_bytes(PDF)
    target = watched / ".target.pdf"
    target.write_bytes(PDF)
    link = watched / "linked.pdf"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlinks unavailable")

    assert scanner.scan_once() == []
    assert jobs.list_jobs() == []
    jobs.close()
    documents.close()


class OriginProvider:
    def __init__(self) -> None:
        self.origins = [
            ArchiveOrigin(
                external_id="41",
                external_version="v1",
                title="Private source",
                original_filename="source.pdf",
                media_type="application/pdf",
                modified_at=None,
            )
        ]
        self.complete = True
        self.content = PDF

    def list_origins(self, *, limit: int):
        return self.origins[:limit], self.complete

    def download_original(self, source_external_id: str):
        assert source_external_id == "41"
        yield self.content


def test_paperless_reconciliation_is_append_only_and_marks_missing_only_on_complete_scan(tmp_path) -> None:
    repository = DocumentRepository(str(tmp_path / "documents.db"))
    provider = OriginProvider()
    reconciler = DocumentOriginReconciler(
        repository=repository,
        provider=provider,
        owner_id="operator",
        max_source_bytes=4096,
    )
    assert reconciler.reconcile() == {
        "status": "complete",
        "observed": 1,
        "created": 1,
        "updated": 0,
        "conflicts": 0,
        "missing": 0,
    }
    first = repository.document_for_external_id(provider="paperless", external_id="41")
    first_source_version = first.source_version_id
    assert reconciler.reconcile()["updated"] == 1
    assert repository.document_for_external_id(
        provider="paperless", external_id="41"
    ).source_version_id == first_source_version

    provider.origins = []
    provider.complete = False
    assert reconciler.reconcile()["missing"] == 0
    assert repository.document_for_external_id(provider="paperless", external_id="41") is not None
    provider.complete = True
    assert reconciler.reconcile()["missing"] == 1
    assert repository.document_for_external_id(provider="paperless", external_id="41") is None
    assert repository.document_for_external_id(
        provider="paperless", external_id="41", visible_only=False
    ) is not None
    repository.close()
