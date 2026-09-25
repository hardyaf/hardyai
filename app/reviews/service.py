from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from collections.abc import Mapping
from typing import Any

from app.reviews.repository import HumanReviewRepository
from app.reviews.types import (
    ActionApprovalProposalRequest,
    ReviewDecisionKind,
    ReviewKind,
    ReviewRequest,
    ReviewState,
)
from app.skills.tool_contracts import (
    ToolCallEnvelope,
    ToolDescriptor,
    canonical_json,
    thaw_json,
    tool_child_operation_id,
)


_HASH = re.compile(r"[0-9a-f]{64}")
_SENSITIVITY = {"normal", "private", "financial", "identity", "highly_restricted"}
_EFFECTS = {
    "read",
    "local_write",
    "external_write",
    "destructive_local",
    "destructive_external",
    "outbound_communication",
    "privileged",
}
_CARDINALITIES = {"single", "atomic_batch", "independent_batch"}
_PERSISTENCE = {"standard", "redacted", "no_store"}
_DESTINATION_PURPOSES = {"human_reviews", "operator_notices", "task_workspace"}
_FORBIDDEN_ARGUMENT_KEYS = {
    "api_key",
    "body",
    "credential",
    "credentials",
    "email_body",
    "document_text",
    "raw_document_text",
    "raw_email_body",
    "secret",
    "token",
}
_TRANSFER_TOP_LEVEL = {
    "manifest_version",
    "request_id",
    "request_hash",
    "requester_user_id",
    "agent_id",
    "channel_binding_hash",
    "destination",
    "destination_values",
    "sources",
}
_TRANSFER_DESTINATION = {
    "skill_id",
    "domain",
    "tool_id",
    "contract_version",
    "descriptor_hash",
    "resource_version",
    "arguments_hash",
    "sensitivity",
    "persistence",
}
_TRANSFER_DESTINATION_VALUE = {"destination_pointer", "value_hash"}
_TRANSFER_SOURCE = {
    "observation_ref",
    "operation_id",
    "skill_id",
    "domain",
    "tool_id",
    "contract_version",
    "descriptor_hash",
    "resource_version",
    "transfer_pattern",
    "transfer_scope",
    "source_pointer",
    "subtree_hash",
    "sensitivity",
    "persistence",
    "untrusted",
}


def _sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _bounded_text(value: object, *, code: str, maximum: int = 255) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(code)
    return normalized


def _hash(value: object, *, code: str) -> str:
    normalized = str(value or "").strip().casefold()
    if not _HASH.fullmatch(normalized):
        raise ValueError(code)
    return normalized


def _iso_instant(value: object, *, code: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(code) from exc
    if parsed.tzinfo is None:
        raise ValueError(code)
    return parsed.astimezone(UTC).isoformat()


def _assert_no_forbidden_arguments(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).strip().casefold() in _FORBIDDEN_ARGUMENT_KEYS:
                raise ValueError("action_proposal_forbidden_argument")
            _assert_no_forbidden_arguments(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_no_forbidden_arguments(child)


def action_request_binding_hash(envelope: ToolCallEnvelope) -> str:
    """Bind transfer evidence to the immutable request and requesting principal."""

    return _sha256(
        {
            "root_request_id": envelope.root_request_id,
            "session_id": envelope.session_id,
            "principal_kind": envelope.principal_kind,
            "principal_subject": envelope.principal_subject,
            "external_user_id": envelope.external_user_id,
            "requester_user_id": envelope.user_id,
            "agent_id": envelope.agent_id,
        }
    )


def action_channel_binding_hash(envelope: ToolCallEnvelope) -> str:
    """Bind transfer evidence to the exact transport and channel scope."""

    return _sha256(
        {
            "source_interface": envelope.source_interface,
            "channel_scope": envelope.channel_scope,
        }
    )


def _pointer_value(value: object, pointer: str) -> tuple[bool, object]:
    if pointer == "":
        return True, value
    if not pointer.startswith("/"):
        return False, None
    current = value
    for encoded in pointer[1:].split("/"):
        segment = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and segment in current:
            current = current[segment]
            continue
        if isinstance(current, (list, tuple)) and segment.isdigit():
            index = int(segment)
            if index < len(current):
                current = current[index]
                continue
        return False, None
    return True, current


def _pointer_pattern_matches(pattern: str, pointer: str) -> bool:
    if not pattern.startswith("/") or not pointer.startswith("/"):
        return False
    pattern_segments = pattern[1:].split("/")
    pointer_segments = pointer[1:].split("/")
    return len(pattern_segments) == len(pointer_segments) and all(
        expected == "*" or expected == observed
        for expected, observed in zip(pattern_segments, pointer_segments, strict=True)
    )


def derive_action_batch_manifest(
    *,
    envelope: ToolCallEnvelope,
    descriptor: ToolDescriptor,
) -> tuple[dict[str, Any], str]:
    """Derive the exact content-free child manifest for an independent batch."""

    if descriptor.effect_cardinality != "independent_batch":
        raise ValueError("action_batch_manifest_not_allowed")
    arguments = thaw_json(envelope.arguments)
    properties = descriptor.input_schema.get("properties")
    required = descriptor.input_schema.get("required")
    if not isinstance(properties, Mapping) or not isinstance(required, (list, tuple)):
        raise ValueError("action_batch_target_contract_invalid")
    target_fields = [
        str(name)
        for name, schema in properties.items()
        if name in required
        and isinstance(schema, Mapping)
        and schema.get("type") == "array"
        and isinstance(arguments.get(str(name)), list)
    ]
    if not target_fields:
        raise ValueError("action_batch_target_contract_invalid")
    # The descriptor's first required array property is the canonical target set. Additional
    # arrays are shared child arguments (for example an allowlisted label set).
    target_field = target_fields[0]
    targets = arguments[target_field]
    if not 1 <= len(targets) <= 100:
        raise ValueError("action_batch_target_count_invalid")
    canonical_targets = [canonical_json(item) for item in targets]
    if len(canonical_targets) != len(set(canonical_targets)):
        raise ValueError("action_batch_target_duplicate")
    if canonical_targets != sorted(canonical_targets):
        raise ValueError("action_batch_targets_not_canonical")
    children: list[dict[str, Any]] = []
    for index, (target, target_json) in enumerate(
        zip(targets, canonical_targets, strict=True),
        start=1,
    ):
        child_arguments = dict(arguments)
        child_arguments[target_field] = [target]
        target_ref = str(target) if isinstance(target, str) else target_json
        child_id, child_hash = tool_child_operation_id(
            operation_id=envelope.operation_id,
            child_index=index,
            canonical_target_ref=target_ref,
            child_arguments=child_arguments,
        )
        children.append(
            {
                "child_operation_id": child_id,
                "child_index": index,
                "target_hash": _sha256(target),
                "arguments_hash": child_hash,
            }
        )
    manifest = {
        "manifest_version": 1,
        "parent_operation_id": envelope.operation_id,
        "parent_arguments_hash": envelope.arguments_hash,
        "expected_child_count": len(children),
        "children": children,
    }
    return manifest, _sha256(manifest)


def _validate_batch_manifest(
    value: object,
    *,
    envelope: ToolCallEnvelope,
    descriptor: ToolDescriptor,
) -> tuple[dict[str, Any], str]:
    expected = {
        "manifest_version",
        "parent_operation_id",
        "parent_arguments_hash",
        "expected_child_count",
        "children",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("action_batch_manifest_shape_invalid")
    children = value.get("children")
    count = value.get("expected_child_count")
    if (
        value.get("manifest_version") != 1
        or value.get("parent_operation_id") != envelope.operation_id
        or value.get("parent_arguments_hash") != envelope.arguments_hash
        or not isinstance(count, int)
        or isinstance(count, bool)
        or not 1 <= count <= 100
        or not isinstance(children, list)
        or len(children) != count
    ):
        raise ValueError("action_batch_manifest_binding_invalid")
    normalized_children: list[dict[str, Any]] = []
    child_ids: set[str] = set()
    target_hashes: set[str] = set()
    child_fields = {"child_operation_id", "child_index", "target_hash", "arguments_hash"}
    for expected_index, child in enumerate(children, start=1):
        if not isinstance(child, Mapping) or set(child) != child_fields:
            raise ValueError("action_batch_child_shape_invalid")
        child_id = _bounded_text(
            child.get("child_operation_id"), code="action_batch_child_id_invalid"
        )
        target_hash = _hash(child.get("target_hash"), code="action_batch_target_hash_invalid")
        child_hash = _hash(
            child.get("arguments_hash"), code="action_batch_arguments_hash_invalid"
        )
        if child.get("child_index") != expected_index:
            raise ValueError("action_batch_child_order_invalid")
        if child_id in child_ids or target_hash in target_hashes:
            raise ValueError("action_batch_child_duplicate")
        child_ids.add(child_id)
        target_hashes.add(target_hash)
        normalized_children.append(
            {
                "child_operation_id": child_id,
                "child_index": expected_index,
                "target_hash": target_hash,
                "arguments_hash": child_hash,
            }
        )
    normalized = {
        "manifest_version": 1,
        "parent_operation_id": envelope.operation_id,
        "parent_arguments_hash": envelope.arguments_hash,
        "expected_child_count": count,
        "children": normalized_children,
    }
    expected_manifest, expected_hash = derive_action_batch_manifest(
        envelope=envelope,
        descriptor=descriptor,
    )
    if normalized != expected_manifest:
        raise ValueError("action_batch_manifest_changed")
    return normalized, expected_hash


def _validate_transfer_manifest(
    value: object,
    *,
    envelope: ToolCallEnvelope,
    descriptor: ToolDescriptor,
    descriptor_hash: str,
    resource_version: str,
) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping) or set(value) != _TRANSFER_TOP_LEVEL:
        raise ValueError("action_transfer_manifest_shape_invalid")
    destination = value.get("destination")
    destination_values = value.get("destination_values")
    sources = value.get("sources")
    if (
        value.get("manifest_version") != 1
        or value.get("request_id") != envelope.root_request_id
        or value.get("requester_user_id") != envelope.user_id
        or value.get("agent_id") != envelope.agent_id
        or not isinstance(destination, Mapping)
        or set(destination) != _TRANSFER_DESTINATION
        or not isinstance(destination_values, list)
        or not 1 <= len(destination_values) <= 32
        or not isinstance(sources, list)
        or not 1 <= len(sources) <= 32
    ):
        raise ValueError("action_transfer_manifest_binding_invalid")
    request_hash = _hash(
        value.get("request_hash"), code="action_transfer_request_hash_invalid"
    )
    channel_binding_hash = _hash(
        value.get("channel_binding_hash"),
        code="action_transfer_channel_binding_hash_invalid",
    )
    if request_hash != action_request_binding_hash(envelope):
        raise ValueError("action_transfer_request_changed")
    if channel_binding_hash != action_channel_binding_hash(envelope):
        raise ValueError("action_transfer_channel_changed")
    expected_destination = {
        "skill_id": envelope.skill_id,
        "domain": envelope.tool_id.split(".", 1)[0],
        "tool_id": envelope.tool_id,
        "contract_version": envelope.contract_version,
        "descriptor_hash": descriptor_hash,
        "resource_version": resource_version,
        "arguments_hash": envelope.arguments_hash,
        "sensitivity": descriptor.sensitivity,
        "persistence": descriptor.persistence,
    }
    if dict(destination) != expected_destination:
        raise ValueError("action_transfer_destination_changed")
    normalized_values: list[dict[str, str]] = []
    destination_pointers: set[str] = set()
    for entry in destination_values:
        if not isinstance(entry, Mapping) or set(entry) != _TRANSFER_DESTINATION_VALUE:
            raise ValueError("action_transfer_destination_value_shape_invalid")
        pointer = str(entry.get("destination_pointer") or "")
        if not pointer.startswith("/") or len(pointer) > 500 or pointer in destination_pointers:
            raise ValueError("action_transfer_destination_pointer_invalid")
        destination_pointers.add(pointer)
        normalized_values.append(
            {
                "destination_pointer": pointer,
                "value_hash": _hash(
                    entry.get("value_hash"), code="action_transfer_value_hash_invalid"
                ),
            }
        )
        found, destination_value = _pointer_value(thaw_json(envelope.arguments), pointer)
        if not found or normalized_values[-1]["value_hash"] != _sha256(destination_value):
            raise ValueError("action_transfer_destination_value_changed")
    normalized_sources: list[dict[str, Any]] = []
    source_keys: set[tuple[str, str, str]] = set()
    for entry in sources:
        if not isinstance(entry, Mapping) or set(entry) != _TRANSFER_SOURCE:
            raise ValueError("action_transfer_source_shape_invalid")
        source_pointer = str(entry.get("source_pointer") or "")
        pattern = str(entry.get("transfer_pattern") or "")
        scope = str(entry.get("transfer_scope") or "").strip().casefold()
        if (
            (source_pointer and not source_pointer.startswith("/"))
            or not pattern.startswith("/")
            or scope not in {"same_domain", "cross_domain"}
            or not isinstance(entry.get("contract_version"), int)
            or isinstance(entry.get("contract_version"), bool)
            or int(entry["contract_version"]) < 1
            or not isinstance(entry.get("untrusted"), bool)
        ):
            raise ValueError("action_transfer_source_binding_invalid")
        normalized_entry = {
            "observation_ref": _bounded_text(
                entry.get("observation_ref"), code="action_transfer_observation_ref_invalid"
            ),
            "operation_id": _bounded_text(
                entry.get("operation_id"), code="action_transfer_operation_id_invalid"
            ),
            "skill_id": _bounded_text(
                entry.get("skill_id"), code="action_transfer_skill_id_invalid"
            ).casefold(),
            "domain": _bounded_text(
                entry.get("domain"), code="action_transfer_domain_invalid"
            ).casefold(),
            "tool_id": _bounded_text(
                entry.get("tool_id"), code="action_transfer_tool_id_invalid"
            ).casefold(),
            "contract_version": int(entry["contract_version"]),
            "descriptor_hash": _hash(
                entry.get("descriptor_hash"), code="action_transfer_descriptor_hash_invalid"
            ),
            "resource_version": _bounded_text(
                entry.get("resource_version"), code="action_transfer_resource_version_invalid"
            ),
            "transfer_pattern": pattern,
            "transfer_scope": scope,
            "source_pointer": source_pointer,
            "subtree_hash": _hash(
                entry.get("subtree_hash"), code="action_transfer_subtree_hash_invalid"
            ),
            "sensitivity": _bounded_text(
                entry.get("sensitivity"), code="action_transfer_sensitivity_invalid"
            ).casefold(),
            "persistence": _bounded_text(
                entry.get("persistence"), code="action_transfer_persistence_invalid"
            ).casefold(),
            "untrusted": entry["untrusted"],
        }
        if normalized_entry["sensitivity"] not in _SENSITIVITY or normalized_entry[
            "persistence"
        ] not in _PERSISTENCE:
            raise ValueError("action_transfer_source_policy_invalid")
        if normalized_entry["domain"] != normalized_entry["tool_id"].partition(".")[0]:
            raise ValueError("action_transfer_source_domain_invalid")
        if normalized_entry["source_pointer"] and not _pointer_pattern_matches(
            normalized_entry["transfer_pattern"],
            normalized_entry["source_pointer"],
        ):
            raise ValueError("action_transfer_source_pointer_invalid")
        if normalized_entry["transfer_scope"] == "same_domain" and (
            normalized_entry["skill_id"] != envelope.skill_id
            or normalized_entry["domain"] != envelope.tool_id.partition(".")[0]
        ):
            raise ValueError("action_transfer_source_scope_invalid")
        key = (
            normalized_entry["operation_id"],
            normalized_entry["source_pointer"],
            normalized_entry["subtree_hash"],
        )
        if key in source_keys:
            raise ValueError("action_transfer_source_duplicate")
        source_keys.add(key)
        normalized_sources.append(normalized_entry)
    normalized = {
        "manifest_version": 1,
        "request_id": envelope.root_request_id,
        "request_hash": request_hash,
        "requester_user_id": envelope.user_id,
        "agent_id": envelope.agent_id,
        "channel_binding_hash": channel_binding_hash,
        "destination": expected_destination,
        "destination_values": normalized_values,
        "sources": normalized_sources,
    }
    return normalized, _sha256(normalized)


class HumanReviewService:
    """Shared review workflow; callers provide typed facts and humans provide decisions."""

    def __init__(self, repository: HumanReviewRepository) -> None:
        self.repository = repository

    def create_action_proposal(
        self,
        *,
        envelope: ToolCallEnvelope,
        descriptor: ToolDescriptor,
        resource_version: str,
        approver_principal: str,
        expires_at: str,
        destination_purpose: str = "human_reviews",
        safe_action_summary: str | None = None,
        risk_summary: str | None = None,
        batch_manifest: dict[str, Any] | None = None,
        transfer_manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically persist one exact call, linked review, and notification job."""

        if (
            envelope.tool_id != descriptor.tool_id
            or envelope.contract_version != descriptor.contract_version
            or envelope.skill_id != descriptor.skill_id
        ):
            raise ValueError("action_proposal_descriptor_mismatch")
        if descriptor.effect not in _EFFECTS or descriptor.effect == "read":
            raise ValueError("action_proposal_effect_invalid")
        if descriptor.effect_cardinality not in _CARDINALITIES:
            raise ValueError("action_proposal_cardinality_invalid")
        if descriptor.persistence not in _PERSISTENCE:
            raise ValueError("action_proposal_persistence_invalid")
        if descriptor.sensitivity == "highly_restricted":
            raise ValueError("action_proposal_sensitivity_prohibited")
        purpose = _bounded_text(
            destination_purpose, code="action_proposal_destination_invalid", maximum=64
        ).casefold()
        if purpose not in {"human_reviews", "task_workspace"}:
            raise ValueError("action_proposal_destination_invalid")
        resource = _bounded_text(
            resource_version, code="action_proposal_resource_version_invalid"
        )
        approver = _bounded_text(
            approver_principal, code="action_proposal_approver_invalid"
        )
        expiry = _iso_instant(expires_at, code="action_proposal_expiry_invalid")
        if expiry <= datetime.now(UTC).isoformat():
            raise ValueError("action_proposal_expiry_invalid")
        descriptor_hash = _sha256(descriptor.to_storage_dict())
        arguments = thaw_json(envelope.arguments)
        _assert_no_forbidden_arguments(arguments)
        destination_arguments_hash = _sha256(arguments)
        if destination_arguments_hash != envelope.arguments_hash:
            raise ValueError("action_proposal_arguments_hash_mismatch")

        normalized_batch: dict[str, Any] | None = None
        batch_hash: str | None = None
        if descriptor.effect_cardinality == "independent_batch":
            if batch_manifest is None:
                normalized_batch, batch_hash = derive_action_batch_manifest(
                    envelope=envelope,
                    descriptor=descriptor,
                )
            else:
                normalized_batch, batch_hash = _validate_batch_manifest(
                    batch_manifest,
                    envelope=envelope,
                    descriptor=descriptor,
                )
        elif batch_manifest is not None:
            raise ValueError("action_batch_manifest_not_allowed")

        normalized_transfer: dict[str, Any] | None = None
        transfer_hash: str | None = None
        if transfer_manifest is not None:
            normalized_transfer, transfer_hash = _validate_transfer_manifest(
                transfer_manifest,
                envelope=envelope,
                descriptor=descriptor,
                descriptor_hash=descriptor_hash,
                resource_version=resource,
            )
        if descriptor.persistence == "no_store" and normalized_transfer is None:
            raise ValueError("action_proposal_no_store_requires_transfer_manifest")

        summary = _bounded_text(
            safe_action_summary or f"Execute {descriptor.tool_id}.",
            code="action_proposal_summary_invalid",
            maximum=500,
        )
        risk = _bounded_text(
            risk_summary or f"{descriptor.effect} at {descriptor.sensitivity} sensitivity.",
            code="action_proposal_risk_invalid",
            maximum=240,
        )
        material = {
            **envelope.to_dict(),
            "descriptor_hash": descriptor_hash,
            "resource_version": resource,
            "effect": descriptor.effect,
            "effect_cardinality": descriptor.effect_cardinality,
            "sensitivity": descriptor.sensitivity,
            "persistence": descriptor.persistence,
            "destination_purpose": purpose,
            "approver_principal": approver,
            "safe_action_summary": summary,
            "risk_summary": risk,
            "expires_at": expiry,
            "batch_manifest": normalized_batch,
            "batch_manifest_hash": batch_hash,
            "transfer_manifest": normalized_transfer,
            "transfer_binding_hash": transfer_hash,
        }
        proposal_hash = _sha256(material)
        request = ActionApprovalProposalRequest(
            idempotency_key=f"action-proposal:v1:{envelope.operation_id}",
            proposal_hash=proposal_hash,
            root_request_id=envelope.root_request_id,
            operation_id=envelope.operation_id,
            call_ordinal=envelope.call_ordinal,
            session_id=envelope.session_id,
            principal_kind=envelope.principal_kind,
            principal_subject=envelope.principal_subject,
            external_user_id=envelope.external_user_id,
            requester_user_id=envelope.user_id,
            agent_id=envelope.agent_id,
            source_interface=envelope.source_interface,
            channel_scope=envelope.channel_scope,
            skill_id=envelope.skill_id,
            tool_id=envelope.tool_id,
            contract_version=envelope.contract_version,
            descriptor_hash=descriptor_hash,
            resource_version=resource,
            authorization_binding=envelope.authorization_snapshot_ref,
            arguments_hash=envelope.arguments_hash,
            destination_arguments=arguments,
            destination_arguments_hash=destination_arguments_hash,
            effect=descriptor.effect,
            effect_cardinality=descriptor.effect_cardinality,
            sensitivity=descriptor.sensitivity,
            persistence=descriptor.persistence,
            destination_purpose=purpose,
            approver_principal=approver,
            safe_action_summary=summary,
            risk_summary=risk,
            expires_at=expiry,
            batch_manifest=normalized_batch,
            batch_manifest_hash=batch_hash,
            transfer_manifest=normalized_transfer,
            transfer_binding_hash=transfer_hash,
        )
        return self.repository.create_action_proposal(request)

    def decide_action_proposal(
        self,
        *,
        proposal_id: str,
        review_id: str,
        bound_proposal_hash: str,
        decision: ReviewDecisionKind | str,
        actor_principal: str,
        destination_purpose: str,
        guild_id: str,
        channel_id: str,
        message_id: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self.repository.decide_action_proposal(
            proposal_id=_bounded_text(proposal_id, code="action_proposal_id_invalid"),
            review_id=_bounded_text(review_id, code="action_review_id_invalid"),
            bound_proposal_hash=_hash(
                bound_proposal_hash, code="action_proposal_hash_invalid"
            ),
            decision=ReviewDecisionKind(decision),
            actor_principal=_bounded_text(
                actor_principal, code="action_decision_actor_invalid"
            ),
            destination_purpose=_bounded_text(
                destination_purpose, code="action_decision_destination_invalid", maximum=64
            ).casefold(),
            guild_id=_bounded_text(guild_id, code="action_decision_guild_invalid"),
            channel_id=_bounded_text(channel_id, code="action_decision_channel_invalid"),
            message_id=_bounded_text(message_id, code="action_decision_message_invalid"),
            reason=_bounded_text(reason, code="action_decision_reason_invalid", maximum=500),
            idempotency_key=_bounded_text(
                idempotency_key, code="action_decision_idempotency_key_invalid"
            ),
        )

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
