from __future__ import annotations

from pydantic import BaseModel, Field


class DocumentStatusResponse(BaseModel):
    document_id: str
    intake_id: str
    title: str
    media_type: str
    size_bytes: int
    sha256: str
    state: str
    source_available: bool
    created_at: str
    updated_at: str
    failure_code: str | None = None
    sensitivity: str = "private"
    processing_state: str = "not_requested"
    source_version_id: str | None = None
    active_run_id: str | None = None


class DocumentUploadResponse(DocumentStatusResponse):
    duplicate: bool
    enqueue_confirmed: bool


class DocumentSearchResult(BaseModel):
    document_id: str
    title: str
    snippet: str
    state: str
    processing_state: str = "not_requested"
    page_number: int | None = None
    block_id: str | None = None
    evidence_path: str | None = None


class DocumentSearchResponse(BaseModel):
    query: str
    results: list[DocumentSearchResult] = Field(default_factory=list)


class DocumentReprocessRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")


class DocumentReprocessResponse(BaseModel):
    document_id: str
    run_id: str
    processing_state: str
    job_id: str | None = None
    enqueue_confirmed: bool


class DocumentEvidenceBlock(BaseModel):
    run_id: str
    block_id: str
    page_number: int
    block_kind: str
    reading_order: int
    literal_text: str
    bbox: list[float] | None = None
    char_span: list[int] | None = None
    provider_ref: str | None = None


class DocumentEvidenceResponse(BaseModel):
    document_id: str
    title: str
    sensitivity: str
    source_path: str
    blocks: list[DocumentEvidenceBlock] = Field(default_factory=list)


class DocumentMetadataProposalRequest(BaseModel):
    field_name: str = Field(min_length=1, max_length=40)
    proposed_value: str = Field(min_length=1, max_length=500)


class DocumentMetadataProposalResponse(BaseModel):
    proposal_id: str
    document_id: str
    source_version_id: str
    field_name: str
    value_hash: str
    sensitivity: str
    state: str
    review_id: str | None = None


class DocumentMetadataReviewBindingRequest(BaseModel):
    review_id: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9-]+$")
