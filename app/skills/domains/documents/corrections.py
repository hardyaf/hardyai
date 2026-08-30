from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from app.skills.domains.documents.permissions import DocumentAccessPolicy
from app.skills.domains.documents.schemas import field_spec_for, validate_field_correction
from app.skills.domains.documents.types import DocumentRecord


class DocumentCorrectionRepository(Protocol):
    def effective_fields(self, *, document_id: str) -> list[dict[str, Any]]: ...

    def record_field_decision(self, **kwargs: Any) -> dict[str, Any]: ...


def field_review_binding_hash(
    *,
    document_id: str,
    source_version_id: str,
    field_name: str,
    observation_id: str | None,
    observation_item_hash: str | None,
    review_decision_id: str | None,
    effective_value: object | None,
) -> str:
    value_hash = hashlib.sha256(
        json.dumps(
            effective_value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "document_id": str(document_id),
        "source_version_id": str(source_version_id),
        "field_name": str(field_name).strip().casefold(),
        "observation_id": str(observation_id or "absent"),
        "observation_item_hash": str(observation_item_hash or "absent"),
        "review_decision_id": str(review_decision_id or "none"),
        "effective_value_hash": value_hash,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest()


def field_decision_item_hash(
    *,
    review_binding_hash: str,
    decision_kind: str,
    corrected_value: str | None,
) -> str:
    """Bind a Core review to one decision without persisting corrected content there."""

    normalized_kind = str(decision_kind or "").strip().casefold()
    value_hash = (
        hashlib.sha256(str(corrected_value).encode("utf-8")).hexdigest()
        if corrected_value is not None
        else None
    )
    return hashlib.sha256(
        json.dumps(
            {
                "review_binding_hash": str(review_binding_hash).strip().casefold(),
                "decision_kind": normalized_kind,
                "corrected_value_hash": value_hash,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
class DocumentFieldCorrectionService:
    """Applies version-bound human field decisions inside the Documents authority."""

    def __init__(self, repository: DocumentCorrectionRepository) -> None:
        self.repository = repository

    def list_fields(self, *, record: DocumentRecord, user_id: str) -> list[dict[str, Any]]:
        if not DocumentAccessPolicy.can_read_fields(record=record, user_id=user_id):
            raise PermissionError("protected_fields_unavailable")
        source_version_id = str(record.source_version_id or "")
        rows = self.repository.effective_fields(document_id=record.document_id)
        projected: list[dict[str, Any]] = []
        for raw in rows[:64]:
            row = dict(raw)
            row["review_binding_hash"] = field_review_binding_hash(
                document_id=record.document_id,
                source_version_id=source_version_id,
                field_name=str(row.get("field_name") or ""),
                observation_id=(
                    str(row["observation_id"]) if row.get("observation_id") is not None else None
                ),
                observation_item_hash=(
                    str(row["item_hash"]) if row.get("item_hash") is not None else None
                ),
                review_decision_id=(
                    str(row["review_decision_id"])
                    if row.get("review_decision_id") is not None
                    else None
                ),
                effective_value=row.get("value"),
            )
            projected.append(row)
        return projected

    def apply(
        self,
        *,
        record: DocumentRecord,
        user_id: str,
        source_version_id: str,
        field_name: str,
        observation_id: str | None,
        review_binding_hash: str,
        review_decision_id: str,
        decision_kind: str,
        corrected_value: str | None = None,
    ) -> dict[str, Any]:
        if not DocumentAccessPolicy.can_read_fields(record=record, user_id=user_id):
            raise PermissionError("protected_fields_unavailable")
        active_source_version = str(record.source_version_id or "")
        if not active_source_version or str(source_version_id) != active_source_version:
            raise ValueError("field_source_version_changed")
        if record.document_class is None:
            raise ValueError("document_class_unavailable")

        normalized_field = str(field_name or "").strip().casefold()
        field_spec_for(record.document_class, normalized_field)
        current = next(
            (
                row
                for row in self.list_fields(record=record, user_id=user_id)
                if str(row.get("field_name") or "").strip().casefold() == normalized_field
            ),
            None,
        )
        expected_observation_id = (
            str(current["observation_id"]) if current and current.get("observation_id") else None
        )
        supplied_observation_id = str(observation_id).strip() if observation_id else None
        if supplied_observation_id != expected_observation_id:
            raise ValueError("field_observation_changed")
        expected_binding = (
            str(current["review_binding_hash"])
            if current is not None
            else field_review_binding_hash(
                document_id=record.document_id,
                source_version_id=active_source_version,
                field_name=normalized_field,
                observation_id=None,
                observation_item_hash=None,
                review_decision_id=None,
                effective_value=None,
            )
        )
        if str(review_binding_hash).strip().casefold() != expected_binding:
            raise ValueError("field_review_binding_changed")

        normalized_kind = str(decision_kind or "").strip().casefold()
        if normalized_kind == "confirm":
            if current is None:
                raise ValueError("field_confirmation_requires_observation")
            applied_value = None
        elif normalized_kind == "correct":
            applied_value = validate_field_correction(
                document_class=record.document_class,
                field_name=normalized_field,
                value=str(corrected_value or ""),
            )
        else:
            raise ValueError("unsupported field decision")

        decision = self.repository.record_field_decision(
            document_id=record.document_id,
            source_version_id=active_source_version,
            field_name=normalized_field,
            review_decision_id=str(review_decision_id),
            decision_kind=normalized_kind,
            selected_observation_id=expected_observation_id,
            applied_value=applied_value,
        )
        return {
            "field_decision_id": str(decision["field_decision_id"]),
            "document_id": record.document_id,
            "source_version_id": active_source_version,
            "field_name": normalized_field,
            "review_decision_id": str(decision["review_decision_id"]),
            "selected_observation_id": (
                str(decision["selected_observation_id"])
                if decision.get("selected_observation_id")
                else None
            ),
            "decision_kind": str(decision["decision_kind"]),
        }
