from __future__ import annotations

import json
import hashlib
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from app.reviews.service import HumanReviewService
from app.reviews.types import ReviewKind
from app.skills.domains.documents.artifacts import ContentAddressedArtifactStore
from app.skills.domains.documents.ports import (
    ArchiveReadPort,
    DocumentParserPort,
    ParserOperationUnavailable,
)
from app.skills.domains.documents.quality import evaluate_native_artifact
from app.skills.domains.documents.storage import DocumentRepository, DocumentStorageError
from app.skills.domains.documents.types import ArtifactKind, ProcessingState


class DocumentProcessingError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = str(code)[:120]


class DocumentProcessingPending(DocumentProcessingError):
    pass


class DocumentProcessingService:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        archive: ArchiveReadPort,
        parser: DocumentParserPort,
        artifact_store: ContentAddressedArtifactStore,
        reviews: HumanReviewService | None = None,
        max_provider_json_bytes: int = 64 * 1024 * 1024,
        max_markdown_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        self.repository = repository
        self.archive = archive
        self.parser = parser
        self.artifact_store = artifact_store
        self.reviews = reviews
        self.max_provider_json_bytes = max(1024, int(max_provider_json_bytes))
        self.max_markdown_bytes = max(1024, int(max_markdown_bytes))

    def process(
        self,
        *,
        document_id: str,
        source_version_id: str,
        run_id: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        record = self.repository.get(document_id)
        if record is None or record.source_version_id != source_version_id or not record.source_ref:
            raise DocumentProcessingError("processing_source_version_unavailable")
        source = self.repository.archive_source(record.source_ref)
        if source is None:
            raise DocumentProcessingError("processing_archive_mapping_unavailable")
        run = self.repository.begin_processing_run(run_id=run_id, fencing_token=fencing_token)
        operation_ref = str(run.get("provider_operation_ref") or "").strip()
        if not operation_ref:
            chunks = self.archive.download_original(source.external_id)
            from tempfile import SpooledTemporaryFile

            with SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as stream:
                observed = 0
                digest = hashlib.sha256()
                for chunk in chunks:
                    observed += len(chunk)
                    if observed > record.size_bytes:
                        raise DocumentProcessingError("processing_source_size_mismatch")
                    digest.update(chunk)
                    stream.write(chunk)
                if observed != record.size_bytes:
                    raise DocumentProcessingError("processing_source_size_mismatch")
                if digest.hexdigest() != record.sha256:
                    raise DocumentProcessingError("processing_source_checksum_mismatch")
                stream.seek(0)
                submission = self.parser.submit(
                    stream=stream,
                    filename=record.original_filename,
                    media_type=record.media_type,
                )
            operation_ref = submission.operation_ref
            if not self.repository.set_processing_operation(
                run_id=run_id,
                fencing_token=fencing_token,
                operation_ref=operation_ref,
            ):
                raise DocumentStorageError("stale_processing_fence")
            raise DocumentProcessingPending("parser_submitted")

        try:
            operation = self.parser.status(operation_ref)
        except ParserOperationUnavailable as exc:
            self._clear_unavailable_operation(
                run_id=run_id,
                fencing_token=fencing_token,
                operation_ref=operation_ref,
            )
            raise DocumentProcessingPending("parser_operation_unavailable") from exc
        if operation.state in {"pending", "started", "running"}:
            raise DocumentProcessingPending("parser_pending")
        if operation.state != "success":
            self.repository.finish_processing_run(
                run_id=run_id,
                fencing_token=fencing_token,
                state=ProcessingState.FAILED,
                error_code=operation.error_code or "parser_failed",
            )
            raise DocumentProcessingError(operation.error_code or "parser_failed")

        try:
            artifact = evaluate_native_artifact(
                self.parser.result(
                    operation_ref=operation_ref,
                    document_id=document_id,
                    source_version_id=source_version_id,
                    run_id=run_id,
                )
            )
        except ParserOperationUnavailable as exc:
            self._clear_unavailable_operation(
                run_id=run_id,
                fencing_token=fencing_token,
                operation_ref=operation_ref,
            )
            raise DocumentProcessingPending("parser_operation_unavailable") from exc
        raw_bytes = json.dumps(
            artifact.raw_provider,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        normalized_payload = asdict(artifact)
        normalized_payload.pop("raw_provider", None)
        normalized_bytes = json.dumps(
            normalized_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        markdown_bytes = artifact.markdown.encode("utf-8")
        if len(raw_bytes) > self.max_provider_json_bytes or len(normalized_bytes) > self.max_provider_json_bytes:
            raise DocumentProcessingError("parser_output_too_large")
        if len(markdown_bytes) > self.max_markdown_bytes:
            raise DocumentProcessingError("parser_markdown_too_large")
        stored = [
            (ArtifactKind.PROVIDER_JSON, self.artifact_store.put(raw_bytes, suffix="json")),
            (ArtifactKind.NORMALIZED_JSON, self.artifact_store.put(normalized_bytes, suffix="json")),
            (ArtifactKind.MARKDOWN, self.artifact_store.put(markdown_bytes, suffix="md")),
        ]
        artifact_rows: list[dict[str, Any]] = []
        for kind, value in stored:
            artifact_rows.append(
                self.repository.store_artifact(
                    artifact_id=str(uuid4()),
                    document_id=document_id,
                    source_version_id=source_version_id,
                    run_id=run_id,
                    artifact_kind=kind,
                    storage_key=value.storage_key,
                    sha256=value.sha256,
                    size_bytes=value.size_bytes,
                    schema_version=artifact.schema_version,
                    sensitivity=record.sensitivity,
                )
            )
        self.repository.replace_normalized_projection(
            run_id=run_id,
            document_id=document_id,
            source_version_id=source_version_id,
            fencing_token=fencing_token,
            pages=[asdict(page) for page in artifact.pages],
            blocks=[asdict(block) for block in artifact.blocks],
            tables=[asdict(table) for table in artifact.tables],
            sensitivity=record.sensitivity,
        )
        normalized_hash = next(
            str(row["sha256"])
            for row in artifact_rows
            if row["artifact_kind"] == ArtifactKind.NORMALIZED_JSON.value
        )
        self.repository.commit_stage(
            run_id=run_id,
            fencing_token=fencing_token,
            stage="normalize",
            stage_version=artifact.schema_version,
            result_hash=normalized_hash,
        )
        if artifact.quality.processing_complete:
            self.repository.finish_processing_run(
                run_id=run_id,
                fencing_token=fencing_token,
                state=ProcessingState.COMPLETE,
                activate=True,
            )
            return {
                "status": "complete",
                "document_id": document_id,
                "run_id": run_id,
                "block_count": len(artifact.blocks),
                "page_count": len(artifact.pages),
            }
        review_id = None
        if self.reviews is not None:
            review = self.reviews.create_review(
                review_kind=ReviewKind.QUALITY,
                subject_type="document_processing_run",
                subject_id=run_id,
                subject_version=source_version_id,
                item_hash=normalized_hash,
                sensitivity=record.sensitivity.value,
                source_ref=document_id,
                confidence=artifact.quality.text_coverage_score,
                validator_summary=[
                    {"code": reason, "passed": False}
                    for reason in artifact.quality.review_reasons
                ],
                evidence_refs=[],
            )
            review_id = str(review["review_id"])
        self.repository.finish_processing_run(
            run_id=run_id,
            fencing_token=fencing_token,
            state=(
                ProcessingState.NEEDS_REVIEW
                if review_id is not None
                else ProcessingState.PROCESSING_INCOMPLETE
            ),
            error_code=";".join(artifact.quality.review_reasons)[:120],
        )
        return {
            "status": "needs_review" if review_id is not None else "processing_incomplete",
            "document_id": document_id,
            "run_id": run_id,
            "review_id": review_id,
            "quality_reasons": list(artifact.quality.review_reasons),
        }

    def _clear_unavailable_operation(
        self,
        *,
        run_id: str,
        fencing_token: int,
        operation_ref: str,
    ) -> None:
        if not self.repository.clear_processing_operation(
            run_id=run_id,
            fencing_token=fencing_token,
            expected_operation_ref=operation_ref,
        ):
            raise DocumentStorageError("stale_processing_fence")
