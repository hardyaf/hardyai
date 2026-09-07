from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


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


class ActionProposalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    CANCELED = "canceled"
    DENIED = "denied"
    FAILED_TERMINAL = "failed_terminal"


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


@dataclass(frozen=True)
class ActionApprovalProposalRequest:
    idempotency_key: str
    proposal_hash: str
    root_request_id: str
    operation_id: str
    call_ordinal: int
    session_id: str
    principal_kind: str
    principal_subject: str
    external_user_id: str
    requester_user_id: str
    agent_id: str
    source_interface: str
    channel_scope: str
    skill_id: str
    tool_id: str
    contract_version: int
    descriptor_hash: str
    resource_version: str
    authorization_binding: str
    arguments_hash: str
    destination_arguments: dict[str, Any]
    destination_arguments_hash: str
    effect: str
    effect_cardinality: str
    sensitivity: str
    persistence: str
    destination_purpose: str
    approver_principal: str
    safe_action_summary: str
    risk_summary: str
    expires_at: str
    batch_manifest: dict[str, Any] | None = None
    batch_manifest_hash: str | None = None
    transfer_manifest: dict[str, Any] | None = None
    transfer_binding_hash: str | None = None
