from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReviewKind(StrEnum):
    QUALITY = "quality"
    CLASSIFICATION = "classification"
    FIELD_CORRECTION = "field_correction"
    METADATA_PROPOSAL = "metadata_proposal"
    DOWNSTREAM_ACTION = "downstream_action"


class ReviewState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    APPLIED = "applied"
    EXECUTED = "executed"


class ReviewDecisionKind(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True)
class ReviewRequest:
    review_kind: ReviewKind
    subject_type: str
    subject_id: str
    subject_version: str
    item_hash: str
    sensitivity: str
    source_ref: str | None = None
    confidence: float | None = None
    validator_summary: tuple[dict[str, object], ...] = ()
    evidence_refs: tuple[str, ...] = ()
    target_operation: str | None = None
    authorization_binding: str | None = None
    expires_at: str | None = None
