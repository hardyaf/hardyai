from __future__ import annotations

import re
from typing import Any

from app.reviews.repository import HumanReviewRepository
from app.reviews.types import ReviewDecisionKind, ReviewKind, ReviewRequest, ReviewState


_HASH = re.compile(r"[0-9a-f]{64}")
_SENSITIVITY = {"normal", "private", "financial", "identity", "highly_restricted"}


class HumanReviewService:
    """Shared review workflow; callers provide typed facts and humans provide decisions."""

    def __init__(self, repository: HumanReviewRepository) -> None:
        self.repository = repository

    def create_review(
        self,
        *,
        review_kind: ReviewKind | str,
        subject_type: str,
        subject_id: str,
        subject_version: str,
        item_hash: str,
        sensitivity: str,
        source_ref: str | None = None,
        confidence: float | None = None,
        validator_summary: list[dict[str, object]] | None = None,
        evidence_refs: list[str] | None = None,
        target_operation: str | None = None,
        authorization_binding: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        normalized_type = str(subject_type or "").strip().casefold()
        normalized_subject = str(subject_id or "").strip()
        normalized_version = str(subject_version or "").strip()
        normalized_hash = str(item_hash or "").strip().casefold()
        normalized_sensitivity = str(sensitivity or "").strip().casefold()
        if not normalized_type or not normalized_subject or not normalized_version:
            raise ValueError("review subject is incomplete")
        if not _HASH.fullmatch(normalized_hash):
            raise ValueError("review item hash is invalid")
        if normalized_sensitivity not in _SENSITIVITY:
            raise ValueError("review sensitivity is invalid")
        bounded_confidence = None if confidence is None else max(0.0, min(float(confidence), 1.0))
        return self.repository.create(
            ReviewRequest(
                review_kind=ReviewKind(review_kind),
                subject_type=normalized_type,
                subject_id=normalized_subject,
                subject_version=normalized_version,
                item_hash=normalized_hash,
                source_ref=str(source_ref).strip() if source_ref else None,
                sensitivity=normalized_sensitivity,
                confidence=bounded_confidence,
                validator_summary=tuple((validator_summary or [])[:32]),
                evidence_refs=tuple(str(item)[:200] for item in (evidence_refs or [])[:64]),
                target_operation=str(target_operation).strip()[:120] if target_operation else None,
                authorization_binding=(
                    str(authorization_binding).strip()[:240] if authorization_binding else None
                ),
                expires_at=expires_at,
            )
        )

    def decide(
        self,
        *,
        review_id: str,
        bound_item_hash: str,
        decision: ReviewDecisionKind | str,
        actor_principal: str,
        reason: str,
        idempotency_key: str,
        edited_value_ref: str | None = None,
    ) -> dict[str, Any]:
        actor = str(actor_principal or "").strip()
        rationale = " ".join(str(reason or "").split())[:500]
        key = str(idempotency_key or "").strip()
        if not actor or not rationale or not key:
            raise ValueError("review decision requires actor, reason, and idempotency key")
        return self.repository.decide(
            review_id=str(review_id),
            bound_item_hash=str(bound_item_hash).strip().casefold(),
            decision=ReviewDecisionKind(decision),
            actor_principal=actor,
            reason=rationale,
            idempotency_key=key,
            edited_value_ref=str(edited_value_ref).strip() if edited_value_ref else None,
        )

    def list_pending(self, *, subject_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_items(
            state=ReviewState.PENDING,
            subject_type=subject_type,
            limit=limit,
        )

    def latest_decision(self, *, review_id: str) -> dict[str, Any] | None:
        return self.repository.latest_decision(str(review_id))

    def mark_applied(self, *, decision_id: str, action_receipt_ref: str | None = None) -> bool:
        return self.repository.mark_applied(
            decision_id=str(decision_id),
            action_receipt_ref=(
                str(action_receipt_ref).strip()[:240] if action_receipt_ref else None
            ),
        )
