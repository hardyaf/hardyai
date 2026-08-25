from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.skills.domains.documents.ingestion import DocumentValidationError, TransientDocumentSpool
from app.skills.domains.documents.service import DocumentIngestionService
from app.skills.domains.documents.storage import DocumentRepository


PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


def _png(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _jpeg(width: int, height: int) -> bytes:
    return (
        b"\xff\xd8"
        + b"\xff\xc0\x00\x0b\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x01\x01\x11\x00"
        + b"\xff\xd9"
    )


class FakeEnqueuer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, str]] = []

    def enqueue_document(self, *, document_id: str, intake_id: str, sha256: str) -> str:
        self.calls.append((document_id, intake_id, sha256))
        if self.fail:
            raise RuntimeError("queue unavailable")
        return "11111111-1111-4111-8111-111111111111"


def _stage(spool: TransientDocumentSpool, *, filename: str = "statement.pdf", data: bytes = PDF):
    writer = spool.begin(filename=filename, declared_media_type="application/pdf", title="Statement")
    writer.write(data[:7])
    writer.write(data[7:])
    return writer.finish()


def test_intake_is_fsynced_deduplicated_and_truthful_about_enqueue(tmp_path) -> None:
    repository = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=1024, quota_bytes=4096)
    enqueuer = FakeEnqueuer()
    service = DocumentIngestionService(repository=repository, spool=spool, enqueuer=enqueuer)

    first = service.accept(owner_id="operator", staged=_stage(spool))
    duplicate_staged = _stage(spool)
    duplicate_path = spool.path_for(duplicate_staged.spool_key)
    repeated = service.accept(owner_id="operator", staged=duplicate_staged)

    assert first.record.state.value == "queued"
    assert first.enqueue_confirmed is True
    assert repeated.record.document_id == first.record.document_id
    assert repeated.created is False
    assert duplicate_path.exists() is False
    assert len(enqueuer.calls) == 1
    repository.close()


def test_failed_enqueue_remains_visible_and_is_recoverable(tmp_path) -> None:
    repository = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=1024, quota_bytes=4096)
    failing = FakeEnqueuer(fail=True)
    service = DocumentIngestionService(repository=repository, spool=spool, enqueuer=failing)

    accepted = service.accept(owner_id="operator", staged=_stage(spool))
    assert accepted.record.state.value == "awaiting_enqueue"
    assert accepted.enqueue_confirmed is False
    assert spool.path_for(accepted.record.spool_key).exists()

    service.enqueuer = FakeEnqueuer()
    assert service.recover_awaiting_enqueue() == 1
    assert repository.get(accepted.record.document_id).state.value == "queued"
    repository.close()


def test_extension_and_magic_mismatch_removes_partial_file(tmp_path) -> None:
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=1024, quota_bytes=4096)
    writer = spool.begin(filename="not-a-pdf.pdf", declared_media_type="application/pdf", title=None)
    writer.write(b"\x89PNG\r\n\x1a\ncontent")
    with pytest.raises(DocumentValidationError, match="extension_signature_mismatch"):
        writer.finish()
    assert list(Path(spool.root).iterdir()) == []


def test_encrypted_pdf_marker_split_across_chunks_is_rejected_and_removed(tmp_path) -> None:
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=1024, quota_bytes=4096)
    writer = spool.begin(filename="locked.pdf", declared_media_type="application/pdf", title=None)
    writer.write(b"%PDF-1.4\n1 0 obj<</En")
    writer.write(b"crypt true>>endobj\n%%EOF\n")
    with pytest.raises(DocumentValidationError, match="encrypted_pdf_not_supported"):
        writer.finish()
    assert list(Path(spool.root).iterdir()) == []


def test_concurrent_exact_uploads_create_one_intake_and_one_job(tmp_path) -> None:
    repository = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=1024, quota_bytes=4096)
    enqueuer = FakeEnqueuer()
    service = DocumentIngestionService(repository=repository, spool=spool, enqueuer=enqueuer)
    staged = [_stage(spool), _stage(spool)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda item: service.accept(owner_id="operator", staged=item), staged))

    assert {result.record.document_id for result in results} == {results[0].record.document_id}
    assert sum(result.created for result in results) == 1
    assert len(enqueuer.calls) == 1
    assert len(list(Path(spool.root).glob("*.bin"))) == 1
    repository.close()


def test_separate_gateway_connections_converge_concurrent_exact_uploads(tmp_path) -> None:
    first_repository = DocumentRepository(str(tmp_path / "documents.db"))
    second_repository = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=1024, quota_bytes=4096)
    enqueuer = FakeEnqueuer()
    services = (
        DocumentIngestionService(repository=first_repository, spool=spool, enqueuer=enqueuer),
        DocumentIngestionService(repository=second_repository, spool=spool, enqueuer=enqueuer),
    )
    staged = [_stage(spool), _stage(spool)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(service.accept, owner_id="operator", staged=item)
            for service, item in zip(services, staged, strict=True)
        ]
        results = [future.result() for future in futures]

    assert {result.record.document_id for result in results} == {results[0].record.document_id}
    assert sum(result.created for result in results) == 1
    assert len(enqueuer.calls) == 1
    assert len(list(Path(spool.root).glob("*.bin"))) == 1
    first_repository.close()
    second_repository.close()


@pytest.mark.parametrize(
    ("filename", "media_type", "data"),
    [
        ("scan.png", "image/png", _png(3, 2)),
        ("scan.jpg", "image/jpeg", _jpeg(3, 2)),
    ],
)
def test_bounded_png_and_jpeg_are_accepted(
    tmp_path,
    filename: str,
    media_type: str,
    data: bytes,
) -> None:
    spool = TransientDocumentSpool(
        str(tmp_path / "spool"),
        max_bytes=1024,
        quota_bytes=4096,
        max_image_pixels=6,
    )
    writer = spool.begin(filename=filename, declared_media_type=media_type, title="Scan")
    writer.write(data)

    staged = writer.finish()

    assert staged.media_type == media_type
    assert staged.size_bytes == len(data)
    assert spool.path_for(staged.spool_key).read_bytes() == data


@pytest.mark.parametrize(
    ("filename", "media_type", "data", "expected_error"),
    [
        ("huge.png", "image/png", _png(3, 2), "image_dimensions_exceeded"),
        ("huge.jpg", "image/jpeg", _jpeg(3, 2), "image_dimensions_exceeded"),
        ("broken.png", "image/png", _png(1, 1)[:-12], "malformed_png"),
        ("broken.jpg", "image/jpeg", b"\xff\xd8\xff\xd9", "malformed_jpeg"),
    ],
)
def test_malformed_or_oversized_images_are_rejected_without_spool_residue(
    tmp_path,
    filename: str,
    media_type: str,
    data: bytes,
    expected_error: str,
) -> None:
    spool = TransientDocumentSpool(
        str(tmp_path / "spool"),
        max_bytes=1024,
        quota_bytes=4096,
        max_image_pixels=5,
    )
    writer = spool.begin(filename=filename, declared_media_type=media_type, title=None)
    writer.write(data)

    with pytest.raises(DocumentValidationError, match=expected_error):
        writer.finish()
    assert list(Path(spool.root).iterdir()) == []
