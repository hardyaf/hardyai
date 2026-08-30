from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from app.reviews.service import HumanReviewService
from app.reviews.types import ReviewDecisionKind, ReviewKind
from app.skills.domains.documents.corrections import (
    field_decision_item_hash,
    field_review_binding_hash,
)
from app.skills.domains.documents.ports import DocumentQueryPort
from app.skills.domains.documents.schemas import field_spec_for


class DocumentFieldReviewCoordinator:
    """Coordinates a content-free Core review with one Documents-owned field write."""

    def __init__(self, *, gateway: DocumentQueryPort, reviews: HumanReviewService) -> None:
        self.gateway = gateway
        self.reviews = reviews

    def apply(
        self,
        *,
        document_id: str,
        document_class: str,
        fields_response: dict[str, Any],
        current: dict[str, Any] | None,
        field_name: str,
        decision_kind: str,
        corrected_value: str | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        source_version_id = str(fields_response.get("source_version_id") or "").strip()
        if not source_version_id:
            raise ValueError("document source version is unavailable")
        spec = field_spec_for(document_class, field_name)
        observation_id = (
            str(current.get("observation_id"))
            if current is not None and current.get("observation_id")
            else None
        )
        review_binding_hash = (
            str(current.get("review_binding_hash") or "").strip().casefold()
            if current is not None
            else field_review_binding_hash(
                document_id=document_id,
                source_version_id=source_version_id,
                field_name=field_name,
                observation_id=None,
                observation_item_hash=None,
                review_decision_id=None,
                effective_value=None,
            )
        )
        if len(review_binding_hash) != 64:
            raise ValueError("document field review binding is unavailable")
        review_item_hash = field_decision_item_hash(
            review_binding_hash=review_binding_hash,
            decision_kind=decision_kind,
            corrected_value=corrected_value,
        )
        review = self.reviews.create_review(
            review_kind=ReviewKind.FIELD_CORRECTION,
            subject_type=(
                "document_field_observation" if observation_id else "document_missing_field"
            ),
            subject_id=observation_id or f"field:{field_name}",
            subject_version=source_version_id,
            item_hash=review_item_hash,
            sensitivity=(
                str(current.get("sensitivity") or spec.sensitivity.value)
                if current is not None
                else spec.sensitivity.value
            ),
            source_ref=document_id,
            confidence=(float(current.get("confidence") or 0.0) if current is not None else 0.0),
            validator_summary=[{"code": f"user_{decision_kind}", "passed": True}],
            target_operation="documents.apply_field_decision",
        )
        decision = self._approve_or_recover(
            review=review,
            review_item_hash=review_item_hash,
            document_id=document_id,
            field_name=field_name,
            decision_kind=decision_kind,
            context=context,
        )
        applied = self.gateway.apply_field_decision(
            document_id=document_id,
            source_version_id=source_version_id,
            field_name=field_name,
            observation_id=observation_id,
            review_binding_hash=review_binding_hash,
            review_decision_id=str(decision["decision_id"]),
            decision_kind=decision_kind,
            corrected_value=corrected_value,
        )
        self.reviews.mark_applied(
            decision_id=str(decision["decision_id"]),
            action_receipt_ref=f"document-field-decision:{applied['field_decision_id']}",
        )
        return applied

    def _approve_or_recover(
        self,
        *,
        review: dict[str, Any],
        review_item_hash: str,
        document_id: str,
        field_name: str,
        decision_kind: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if str(review.get("state") or "").strip().casefold() != "pending":
            recovered = self.reviews.latest_decision(review_id=str(review["review_id"]))
            if isinstance(recovered, dict) and str(recovered.get("decision")) == "approve":
                return recovered
            raise ValueError("document field review is not approved")

        request_id = str(context.get("request_id") or uuid4()).strip()
        idempotency_key = hashlib.sha256(
            json.dumps(
                {
                    "request_id": request_id,
                    "document_id": document_id,
                    "field_name": field_name,
                    "review_item_hash": review_item_hash,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return self.reviews.decide(
            review_id=str(review["review_id"]),
            bound_item_hash=review_item_hash,
            decision=ReviewDecisionKind.APPROVE,
            actor_principal=self._actor_principal(context),
            reason=(
                "authorized user corrected document field"
                if decision_kind == "correct"
                else "authorized user confirmed document field"
            ),
            idempotency_key=idempotency_key,
            edited_value_ref=(
                f"documents:field-decision:{document_id}:{field_name}"
                if decision_kind == "correct"
                else None
            ),
        )

    @staticmethod
    def _actor_principal(context: dict[str, Any]) -> str:
        principal_kind = str(context.get("principal_kind") or "operator").strip().casefold()
        if principal_kind == "discord_adapter":
            actor_id = str(
                context.get("external_user_id") or context.get("requested_by_user_id") or "scoped"
            ).strip()
            return f"discord:{actor_id[:160]}"
        actor_id = str(
            context.get("requested_by_user_id") or context.get("user_id") or "operator"
        ).strip()
        return f"operator:{actor_id[:160]}"
