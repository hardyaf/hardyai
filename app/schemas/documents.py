from __future__ import annotations

from typing import Literal

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
    document_class: str | None = None
    classification_state: str = "unclassified"
    archive_text_visible: bool = True


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


class DocumentStructuredSearchResult(BaseModel):
    document_id: str
    title: str
    selected_document_class: str | None = None
    sensitivity: str
    matched_filters: list[str] = Field(default_factory=list)


class DocumentStructuredSearchResponse(BaseModel):
    results: list[DocumentStructuredSearchResult] = Field(default_factory=list)


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


class DocumentClassificationView(BaseModel):
    classification_id: str
    source_version_id: str
    run_id: str
    taxonomy_version: str
    label: str
    sensitivity: str
    confidence: float
    classifier_name: str
    classifier_version: str
    decision_source: str
    state: str
    selected: bool
    item_hash: str
    created_at: str


class DocumentClassificationsResponse(BaseModel):
    document_id: str
    classification_state: str
    classifications: list[DocumentClassificationView] = Field(default_factory=list)


class DocumentFieldView(BaseModel):
    observation_id: str
    field_name: str
    value: object
    literal_text: str
    sensitivity: str
    confidence: float
    evidence: list[dict[str, object]] = Field(default_factory=list)
    observation_state: str
    item_hash: str
    created_at: str
    review_decision_id: str | None = None
    decision_kind: str | None = None


class DocumentFieldsResponse(BaseModel):
    document_id: str
    source_version_id: str
    fields: list[DocumentFieldView] = Field(default_factory=list)


class DocumentActionProposalView(BaseModel):
    proposal_id: str
    document_id: str
    source_version_id: str
    run_id: str
    action_text: str
    target_list_name: str
    due_text: str | None = None
    normalized_due_date: str | None = None
    assignee_candidate: str | None = None
    confidence: float
    evidence: list[dict[str, object]] = Field(default_factory=list)
    sensitivity: str
    item_hash: str
    review_id: str | None = None
    state: str
    execution_ref: str | None = None
    target_item_ref: str | None = None
    owner_id: str | None = None
    created_at: str
    updated_at: str


class DocumentMemoryProposalView(BaseModel):
    proposal_id: str
    document_id: str
    source_version_id: str
    run_id: str
    fact_text: str
    confidence: float
    evidence: list[dict[str, object]] = Field(default_factory=list)
    sensitivity: str
    item_hash: str
    state: str
    created_at: str
    updated_at: str


class DocumentContactMatchView(BaseModel):
    contact_ref: str
    display_name: str
    organization: str | None = None
    score: float
    reasons: list[str] = Field(default_factory=list)


class DocumentContactProposalView(BaseModel):
    proposal_id: str
    document_id: str
    source_version_id: str
    run_id: str
    proposed_fields: dict[str, str] = Field(default_factory=dict)
    candidate_matches: list[DocumentContactMatchView] = Field(default_factory=list)
    provider_name: str | None = None
    capability_status: str
    proposed_operation: str
    selected_contact_ref: str | None = None
    confidence: float
    evidence: list[dict[str, object]] = Field(default_factory=list)
    item_hash: str
    review_id: str | None = None
    state: str
    execution_ref: str | None = None
    target_contact_ref: str | None = None
    created_at: str
    updated_at: str


class DocumentProposalsResponse(BaseModel):
    document_id: str
    action_proposals: list[DocumentActionProposalView] = Field(default_factory=list)
    memory_proposals: list[DocumentMemoryProposalView] = Field(default_factory=list)
    contact_proposals: list[DocumentContactProposalView] = Field(default_factory=list)


class DocumentIntelligenceResponse(BaseModel):
    document_id: str
    analyses: list[dict[str, object]] = Field(default_factory=list)
    claims: list[dict[str, object]] = Field(default_factory=list)


class RestrictedDocumentAccessRequest(BaseModel):
    purpose: Literal["human_review", "correction", "identity_verification", "tax_use", "legal_use"]
    request_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")


class DocumentActionExecutionBindingRequest(BaseModel):
    review_id: str = Field(min_length=8, max_length=120)
    execution_ref: str = Field(min_length=8, max_length=200)
    target_item_ref: str = Field(min_length=1, max_length=240)
