from __future__ import annotations

import pytest

from app.reviews.repository import HumanReviewRepository
from app.reviews.service import HumanReviewService
from app.skills.domains.documents.artifacts import ContentAddressedArtifactStore
from app.skills.domains.documents.ingestion import TransientDocumentSpool
from app.skills.domains.documents.ports import (
    ParserOperation,
    ParserOperationUnavailable,
    ParserSubmission,
)
from app.skills.domains.documents.processing import (
    DocumentProcessingPending,
    DocumentProcessingService,
)
from app.skills.domains.documents.storage import DocumentRepository, DocumentStorageError
from app.skills.domains.documents.types import (
    DocumentArtifact,
    NormalizedBlock,
    NormalizedPage,
    NormalizedTable,
    NormalizedTableCell,
    ProcessingRoute,
    QualityReport,
)


PDF = b"%PDF-1.4\nnative source bytes\n%%EOF\n"


class Archive:
    def __init__(self, content: bytes = PDF) -> None:
        self.content = content

    def download_original(self, source_external_id: str):
        assert source_external_id == "41"
        yield self.content[:9]
        yield self.content[9:]


class Parser:
    provider_name = "docling"
    provider_version = "1.30.0"

    def __init__(self, *, text: str) -> None:
        self.text = text
        self.submissions = 0

    def submit(self, *, stream, filename: str, media_type: str) -> ParserSubmission:
        assert stream.read() == PDF
        assert filename == "statement.pdf"
        assert media_type == "application/pdf"
        self.submissions += 1
        return ParserSubmission(operation_ref="operation-1")

    def status(self, operation_ref: str) -> ParserOperation:
        return ParserOperation(operation_ref=operation_ref, state="success")

    def result(self, *, operation_ref: str, document_id: str, source_version_id: str, run_id: str):
        block = NormalizedBlock(
            block_id="b1",
            page_number=1,
            kind="paragraph",
            reading_order=0,
            text=self.text,
            bbox=(10.0, 20.0, 300.0, 60.0),
            char_span=(0, len(self.text)),
            provider_ref="#/texts/0",
        )
        return DocumentArtifact(
            schema_version="1",
            document_id=document_id,
            source_version_id=source_version_id,
            run_id=run_id,
            provider_name="docling",
            provider_version="1.30.0",
            pages=(NormalizedPage(1, 612.0, 792.0, "points"),),
            blocks=(block,),
            tables=(
                NormalizedTable(
                    table_id="t1",
                    page_number=1,
                    reading_order=1,
                    row_count=1,
                    column_count=1,
                    bbox=(10.0, 80.0, 300.0, 140.0),
                    provider_ref="#/tables/0",
                    cells=(NormalizedTableCell("c1", 0, 0, 1, 1, "$5", None, None),),
                ),
            ),
            quality=QualityReport(0, 0, 0, 0.0, 0.0, False, False, ()),
            raw_provider={"status": "success", "document": {"opaque": True}},
            markdown=f"# Page 1\n\n{self.text}\n",
        )

    def ready(self) -> bool:
        return True


class ExpiringParser(Parser):
    def submit(self, *, stream, filename: str, media_type: str) -> ParserSubmission:
        super().submit(stream=stream, filename=filename, media_type=media_type)
        return ParserSubmission(operation_ref=f"operation-{self.submissions}")

    def status(self, operation_ref: str) -> ParserOperation:
        if operation_ref == "operation-1":
            raise ParserOperationUnavailable("provider operation expired")
        return ParserOperation(operation_ref=operation_ref, state="success")


def _ready_document(tmp_path):
    repository = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=4096, quota_bytes=16384)
    writer = spool.begin(
        filename="statement.pdf",
        declared_media_type="application/pdf",
        title="Statement",
    )
    writer.write(PDF)
    record, _ = repository.create_or_get(owner_id="operator", staged=writer.finish())
    repository.mark_archiving(document_id=record.document_id, task_ref="archive-task")
    ready = repository.mark_ready(
        document_id=record.document_id,
        provider="paperless",
        external_id="41",
        verified_sha256=record.sha256,
    )
    run = repository.create_processing_run(
        document_id=record.document_id,
        route=ProcessingRoute.NATIVE_DOCLING,
        parser_name="docling",
        parser_version="1.30.0",
        parser_image_digest="sha256:" + "1" * 64,
        configuration_sha256="2" * 64,
        resource_lane="cpu_large",
    )
    return repository, ready, run


def test_native_processing_persists_immutable_artifacts_search_and_evidence(tmp_path) -> None:
    repository, record, run = _ready_document(tmp_path)
    parser = Parser(text="Evidence-bearing native statement balance is five dollars.")
    service = DocumentProcessingService(
        repository=repository,
        archive=Archive(),
        parser=parser,
        artifact_store=ContentAddressedArtifactStore(str(tmp_path / "artifacts")),
    )

    with pytest.raises(DocumentProcessingPending, match="parser_submitted"):
        service.process(
            document_id=record.document_id,
            source_version_id=record.source_version_id,
            run_id=run["run_id"],
            fencing_token=1,
        )
    result = service.process(
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        run_id=run["run_id"],
        fencing_token=1,
    )
    assert result["status"] == "complete"
    assert parser.submissions == 1
    assert repository.get(record.document_id).active_run_id == run["run_id"]
    hit = repository.search_blocks(owner_id="operator", query="balance", limit=5)[0]
    assert hit["block_id"] == "b1" and hit["page_number"] == 1
    evidence = repository.evidence_blocks(
        document_id=record.document_id,
        owner_id="operator",
        block_id="b1",
    )[0]
    assert evidence["provider_ref"] == "#/texts/0"
    assert len(list((tmp_path / "artifacts").rglob("*.*"))) == 3

    second = repository.create_processing_run(
        document_id=record.document_id,
        route=ProcessingRoute.NATIVE_DOCLING,
        parser_name="docling",
        parser_version="1.30.0",
        parser_image_digest="sha256:" + "1" * 64,
        configuration_sha256="3" * 64,
        resource_lane="cpu_large",
        request_key="explicit:second",
    )
    assert second["run_id"] != run["run_id"]
    with pytest.raises(DocumentProcessingPending, match="parser_submitted"):
        service.process(
            document_id=record.document_id,
            source_version_id=record.source_version_id,
            run_id=second["run_id"],
            fencing_token=1,
        )
    second_result = service.process(
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        run_id=second["run_id"],
        fencing_token=1,
    )
    assert second_result["status"] == "complete"
    assert repository.get(record.document_id).active_run_id == second["run_id"]
    assert len(list((tmp_path / "artifacts").rglob("*.*"))) == 4
    with pytest.raises(DocumentStorageError, match="stale_processing_fence"):
        repository.begin_processing_run(run_id=run["run_id"], fencing_token=0)
    repository.close()


def test_near_empty_native_result_creates_shared_review(tmp_path) -> None:
    repository, record, run = _ready_document(tmp_path)
    review_repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = DocumentProcessingService(
        repository=repository,
        archive=Archive(),
        parser=Parser(text="tiny"),
        artifact_store=ContentAddressedArtifactStore(str(tmp_path / "artifacts")),
        reviews=HumanReviewService(review_repository),
    )
    with pytest.raises(DocumentProcessingPending):
        service.process(
            document_id=record.document_id,
            source_version_id=record.source_version_id,
            run_id=run["run_id"],
            fencing_token=1,
        )
    result = service.process(
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        run_id=run["run_id"],
        fencing_token=1,
    )
    assert result["status"] == "needs_review"
    assert "native_text_near_empty" in result["quality_reasons"]
    review = review_repository.get(result["review_id"])
    assert review["subject_type"] == "document_processing_run"
    assert review["item_hash"] and "tiny" not in str(review)
    review_repository.close()
    repository.close()


def test_processing_rejects_same_size_checksum_mismatch(tmp_path) -> None:
    repository, record, run = _ready_document(tmp_path)
    corrupted = bytearray(PDF)
    corrupted[-8] = ord("X")
    service = DocumentProcessingService(
        repository=repository,
        archive=Archive(bytes(corrupted)),
        parser=Parser(text="unused but long enough for native quality"),
        artifact_store=ContentAddressedArtifactStore(str(tmp_path / "artifacts")),
    )
    with pytest.raises(Exception, match="processing_source_checksum_mismatch"):
        service.process(
            document_id=record.document_id,
            source_version_id=record.source_version_id,
            run_id=run["run_id"],
            fencing_token=1,
        )
    repository.close()


def test_processing_resubmits_an_expired_provider_operation(tmp_path) -> None:
    repository, record, run = _ready_document(tmp_path)
    parser = ExpiringParser(text="Evidence-bearing result after provider operation recovery.")
    service = DocumentProcessingService(
        repository=repository,
        archive=Archive(),
        parser=parser,
        artifact_store=ContentAddressedArtifactStore(str(tmp_path / "artifacts")),
    )

    with pytest.raises(DocumentProcessingPending, match="parser_submitted"):
        service.process(
            document_id=record.document_id,
            source_version_id=record.source_version_id,
            run_id=run["run_id"],
            fencing_token=1,
        )
    with pytest.raises(DocumentProcessingPending, match="parser_operation_unavailable"):
        service.process(
            document_id=record.document_id,
            source_version_id=record.source_version_id,
            run_id=run["run_id"],
            fencing_token=1,
        )
    assert repository.get_processing_run(run["run_id"])["provider_operation_ref"] is None
    with pytest.raises(DocumentProcessingPending, match="parser_submitted"):
        service.process(
            document_id=record.document_id,
            source_version_id=record.source_version_id,
            run_id=run["run_id"],
            fencing_token=1,
        )
    result = service.process(
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        run_id=run["run_id"],
        fencing_token=1,
    )

    assert result["status"] == "complete"
    assert parser.submissions == 2
    repository.close()
