from __future__ import annotations

import hashlib
import json
from typing import Any

from app.skills.domains.documents.ports import ArchiveMetadataPort
from app.skills.domains.documents.storage import DocumentRepository


class DocumentMetadataSyncService:
    """Applies a previously approved, version-bound metadata proposal exactly once."""

    def __init__(self, *, repository: DocumentRepository, archive: ArchiveMetadataPort) -> None:
        self.repository = repository
        self.archive = archive

    def apply_approved(
        self,
        *,
        proposal_id: str,
        review: dict[str, Any],
        decision: dict[str, Any],
        operation_id: str,
        expected_external_version: str,
    ) -> dict[str, Any]:
        proposal = self.repository.get_metadata_proposal(proposal_id=proposal_id)
        if proposal is None:
            raise KeyError(proposal_id)
        self._validate_approval(proposal=proposal, review=review, decision=decision)
        record = self.repository.get(str(proposal["document_id"]))
        if record is None or record.source_version_id != str(proposal["source_version_id"]):
            raise ValueError("metadata_proposal_source_version_changed")
        if not record.source_ref:
            raise ValueError("metadata_proposal_source_unavailable")
        source = self.repository.archive_source(record.source_ref)
        if source is None:
            raise ValueError("metadata_proposal_archive_mapping_unavailable")
        field_name = str(proposal["field_name"])
        if field_name != "safe_title":
            raise ValueError("metadata_field_not_enabled_for_write")
        desired = {field_name: str(proposal["proposed_value"])}
        desired_hash = hashlib.sha256(
            json.dumps(desired, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        sync = self.repository.begin_metadata_sync(
            proposal_id=proposal_id,
            provider=source.provider,
            external_id=source.external_id,
            source_version_id=record.source_version_id,
            operation_id=operation_id,
            desired_hash=desired_hash,
            provider_version=expected_external_version,
        )
        if str(sync["state"]) == "applied":
            return sync
        if str(sync["state"]) != "applying":
            raise ValueError("metadata_operation_not_retryable")
        if str(sync["desired_hash"]) != desired_hash:
            raise ValueError("metadata_operation_payload_changed")
        try:
            snapshot = self.archive.write_metadata(
                source_external_id=source.external_id,
                expected_external_version=expected_external_version,
                changes=desired,
                operation_id=operation_id,
            )
            observed_hash = hashlib.sha256(
                json.dumps(snapshot.values, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
            ).hexdigest()
            if observed_hash != desired_hash:
                raise RuntimeError("metadata_readback_hash_mismatch")
        except Exception as exc:
            self.repository.finish_metadata_sync(
                operation_id=operation_id,
                state="conflicted" if "version" in str(exc).casefold() else "failed",
                error_code=str(getattr(exc, "code", "") or type(exc).__name__),
            )
            raise
        return self.repository.finish_metadata_sync(
            operation_id=operation_id,
            state="applied",
            observed_hash=observed_hash,
            provider_version=snapshot.external_version,
        )

    @staticmethod
    def _validate_approval(
        *,
        proposal: dict[str, Any],
        review: dict[str, Any],
        decision: dict[str, Any],
    ) -> None:
        if str(review.get("review_id")) != str(proposal.get("review_id")):
            raise ValueError("metadata_review_binding_changed")
        if str(review.get("subject_type")) != "document_metadata_proposal":
            raise ValueError("metadata_review_subject_invalid")
        if str(review.get("subject_id")) != str(proposal.get("proposal_id")):
            raise ValueError("metadata_review_subject_changed")
        if str(review.get("subject_version")) != str(proposal.get("source_version_id")):
            raise ValueError("metadata_review_version_changed")
        if str(review.get("item_hash")) != str(proposal.get("value_hash")):
            raise ValueError("metadata_review_value_changed")
        if str(review.get("state")) != "approved" or str(decision.get("decision")) != "approve":
            raise ValueError("metadata_proposal_not_approved")
        if str(decision.get("review_id")) != str(review.get("review_id")):
            raise ValueError("metadata_decision_binding_changed")
        if str(decision.get("bound_item_hash")) != str(review.get("item_hash")):
            raise ValueError("metadata_decision_version_changed")
