from __future__ import annotations

import pytest

from app.skills.domains.documents.ingestion import TransientDocumentSpool
from app.skills.domains.documents.storage import DocumentRepository, DocumentStorageError


PDF_A = b"%PDF-1.4\nA\n%%EOF\n"
PDF_B = b"%PDF-1.4\nB\n%%EOF\n"


def _record(repository: DocumentRepository, spool: TransientDocumentSpool, data: bytes):
    writer = spool.begin(filename="scan.pdf", declared_media_type="application/pdf", title="Scan")
    writer.write(data)
    record, created = repository.create_or_get(owner_id="operator", staged=writer.finish())
    assert created is True
    return repository.mark_archiving(document_id=record.document_id, task_ref=f"task-{record.document_id}")


def test_archive_source_conflict_rolls_back_second_document_state(tmp_path) -> None:
    repository = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=1024, quota_bytes=4096)
    first = _record(repository, spool, PDF_A)
    second = _record(repository, spool, PDF_B)
    repository.mark_ready(
        document_id=first.document_id,
        provider="paperless",
        external_id="42",
        verified_sha256=first.sha256,
    )

    with pytest.raises(DocumentStorageError, match="archive_source_conflict"):
        repository.mark_ready(
            document_id=second.document_id,
            provider="paperless",
            external_id="42",
            verified_sha256=second.sha256,
        )

    persisted = repository.get(second.document_id)
    assert persisted.state.value == "archiving"
    assert persisted.source_ref is None
    repository.close()
