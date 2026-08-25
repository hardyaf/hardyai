from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class DocumentState(StrEnum):
    AWAITING_ENQUEUE = "awaiting_enqueue"
    QUEUED = "queued"
    ARCHIVING = "archiving"
    READY = "ready"
    FAILED = "failed"


class Sensitivity(StrEnum):
    NORMAL = "normal"
    PRIVATE = "private"
    FINANCIAL = "financial"
    IDENTITY = "identity"
    HIGHLY_RESTRICTED = "highly_restricted"


class ProcessingState(StrEnum):
    NOT_REQUESTED = "not_requested"
    QUEUED = "queued"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    COMPLETE = "complete"
    PROCESSING_INCOMPLETE = "processing_incomplete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProcessingRoute(StrEnum):
    NATIVE_DOCLING = "native_docling"
    CONVENTIONAL_OCR = "conventional_ocr"
    STRUCTURE = "structure"
    VLM_FALLBACK = "vlm_fallback"
    MANUAL = "manual"


class ArtifactKind(StrEnum):
    PROVIDER_JSON = "provider_json"
    NORMALIZED_JSON = "normalized_json"
    MARKDOWN = "markdown"
    SOURCE_PRESERVING_TEXT = "source_preserving_text"
    SEARCH_BLOCKS = "search_blocks"


@dataclass(frozen=True)
class StagedDocument:
    spool_key: str
    original_filename: str
    title: str
    media_type: str
    size_bytes: int
    sha256: str
    ingest_route: str = "web"


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    intake_id: str
    owner_id: str
    title: str
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str
    state: DocumentState
    spool_key: str | None
    archive_task_ref: str | None
    source_ref: str | None
    durable_job_id: str | None
    failure_code: str | None
    sensitivity: Sensitivity
    processing_state: ProcessingState
    source_version_id: str | None
    active_run_id: str | None
    search_visible: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ArchiveSourceRecord:
    source_ref: str
    document_id: str
    provider: str
    external_id: str
    verified_sha256: str
    verified_at: str


@dataclass(frozen=True)
class IntakeResult:
    record: DocumentRecord
    created: bool
    enqueue_confirmed: bool


@dataclass(frozen=True)
class EvidenceRef:
    page_number: int
    block_id: str
    bbox: tuple[float, float, float, float] | None = None
    char_span: tuple[int, int] | None = None


@dataclass(frozen=True)
class NormalizedBlock:
    block_id: str
    page_number: int
    kind: str
    reading_order: int
    text: str
    bbox: tuple[float, float, float, float] | None
    char_span: tuple[int, int] | None
    provider_ref: str | None
    confidence: float | None = None
    language: str | None = None


@dataclass(frozen=True)
class NormalizedPage:
    page_number: int
    width: float
    height: float
    coordinate_space: str
    rotation_degrees: int = 0


@dataclass(frozen=True)
class NormalizedTableCell:
    cell_id: str
    row_index: int
    column_index: int
    row_span: int
    column_span: int
    text: str
    bbox: tuple[float, float, float, float] | None
    provider_ref: str | None


@dataclass(frozen=True)
class NormalizedTable:
    table_id: str
    page_number: int
    reading_order: int
    row_count: int
    column_count: int
    bbox: tuple[float, float, float, float] | None
    provider_ref: str | None
    cells: tuple[NormalizedTableCell, ...]


@dataclass(frozen=True)
class QualityReport:
    text_characters: int
    page_count: int
    block_count: int
    invalid_character_rate: float
    text_coverage_score: float
    reading_order_complete: bool
    processing_complete: bool
    review_reasons: tuple[str, ...]


@dataclass(frozen=True)
class DocumentArtifact:
    schema_version: str
    document_id: str
    source_version_id: str
    run_id: str
    provider_name: str
    provider_version: str
    pages: tuple[NormalizedPage, ...]
    blocks: tuple[NormalizedBlock, ...]
    quality: QualityReport
    raw_provider: dict[str, Any]
    markdown: str
    tables: tuple[NormalizedTable, ...] = ()
