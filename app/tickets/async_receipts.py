from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from app.tickets.repository import TicketRepository, content_hash


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_SENSITIVITIES = frozenset(
    {"normal", "private", "financial", "identity", "highly_restricted"}
)
_PERSISTENCE = frozenset({"standard", "redacted", "no_store"})
_CARDINALITIES = frozenset({"single", "atomic_batch", "independent_batch"})
_TERMINAL_CHILD_STATES = frozenset(
    {"verified", "dead_letter", "cancelled", "denied"}
)
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "parent_operation_id",
        "authorization_binding_hash",
        "effect_cardinality",
        "expected_child_count",
        "skill_id",
        "tool_id",
        "contract_version",
        "descriptor_hash",
        "resource_version",
        "parent_arguments_hash",
        "children",
        "recovery_manifest_hash",
        "sensitivity",
        "persistence",
    }
)
_CHILD_FIELDS = frozenset(
    {"child_operation_id", "child_index", "target_hash", "arguments_hash"}
)
_CHILD_OUTCOME_FIELDS = frozenset(
    {
        "schema_version",
        "parent_operation_id",
        "parent_manifest_hash",
        "child_operation_id",
        "child_index",
        "effect_state",
        "reason_code",
        "receipt_hash",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "operation_id",
        "idempotency_key",
        "capability",
        "action",
        "resource_key",
        "status",
        "expected_effect",
        "validator_name",
        "validator_version",
        "resource_locator",
        "provider_resource_id",
        "provider_revision",
        "committed_at",
        "execution_observation",
        "result",
    }
)
_FORBIDDEN_PERSISTED_FIELD_PARTS = (
    "raw_message",
    "message_body",
    "email_body",
    "document_text",
    "document_body",
    "note_text",
    "private_note",
    "credential",
    "password",
    "secret",
    "access_token",
    "refresh_token",
    "reasoning",
    "chain_of_thought",
)
_FORBIDDEN_PERSISTED_FIELD_NAMES = frozenset(
    {
        "subject",
        "snippet",
        "body",
        "headers",
        "sender",
        "recipient",
        "recipients",
        "from",
        "to",
        "cc",
        "bcc",
        "content",
        "markdown",
        "transcript",
        "prompt",
    }
)
_MAX_PERSISTED_BYTES = 16_384


class EffectManifestReservation(Protocol):
    """Application seam for one shared-SQLite manifest/domain reservation."""

    def reserve(
        self,
        *,
        ticket_id: str,
        request_id: str,
        manifest: Mapping[str, Any],
        domain_reservation: Callable[[Any, Mapping[str, Any], str, str | None], Mapping[str, Any]]
        | None = None,
    ) -> dict[str, Any]: ...


def _require_hash(value: Any, code: str) -> str:
    normalized = str(value or "").strip().casefold()
    if not _HASH_RE.fullmatch(normalized):
        raise ValueError(code)
    return normalized


def _require_id(value: Any, code: str) -> str:
    normalized = str(value or "").strip()
    if not _OPAQUE_ID_RE.fullmatch(normalized):
        raise ValueError(code)
    return normalized


def _validate_persisted_value(value: Any, *, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError("ticket_control_payload_depth_exceeded")
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key or "").strip().casefold()
            if key in _FORBIDDEN_PERSISTED_FIELD_NAMES or any(
                part in key for part in _FORBIDDEN_PERSISTED_FIELD_PARTS
            ):
                raise ValueError("ticket_control_payload_sensitive_field_forbidden")
            _validate_persisted_value(nested, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        if len(value) > 256:
            raise ValueError("ticket_control_payload_item_limit_exceeded")
        for nested in value:
            _validate_persisted_value(nested, depth=depth + 1)
    elif isinstance(value, str) and len(value) > 2_000:
        raise ValueError("ticket_control_payload_string_limit_exceeded")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("ticket_control_payload_type_invalid")


def _bounded_control_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    _validate_persisted_value(payload)
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > _MAX_PERSISTED_BYTES:
        raise ValueError("ticket_control_payload_size_exceeded")
    return payload


def normalize_execution_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _MANIFEST_FIELDS:
        raise ValueError("tool_execution_manifest_shape_invalid")
    if value.get("schema_version") != 1:
        raise ValueError("tool_execution_manifest_version_invalid")
    cardinality = str(value.get("effect_cardinality") or "").strip().casefold()
    if cardinality not in _CARDINALITIES:
        raise ValueError("tool_execution_manifest_cardinality_invalid")
    sensitivity = str(value.get("sensitivity") or "").strip().casefold()
    persistence = str(value.get("persistence") or "").strip().casefold()
    if sensitivity not in _SENSITIVITIES or persistence not in _PERSISTENCE:
        raise ValueError("tool_execution_manifest_persistence_invalid")
    if persistence == "no_store":
        raise ValueError("tool_execution_manifest_no_store_forbidden")

    raw_children = value.get("children")
    if not isinstance(raw_children, (list, tuple)) or not raw_children:
        raise ValueError("tool_execution_manifest_children_invalid")
    children: list[dict[str, Any]] = []
    child_ids: set[str] = set()
    for position, raw_child in enumerate(raw_children):
        if not isinstance(raw_child, Mapping) or set(raw_child) != _CHILD_FIELDS:
            raise ValueError("tool_execution_manifest_child_shape_invalid")
        child_index = raw_child.get("child_index")
        if not isinstance(child_index, int) or isinstance(child_index, bool) or child_index != position:
            raise ValueError("tool_execution_manifest_child_index_invalid")
        child_id = _require_id(
            raw_child.get("child_operation_id"),
            "tool_execution_manifest_child_id_invalid",
        )
        if child_id in child_ids:
            raise ValueError("tool_execution_manifest_child_id_duplicate")
        child_ids.add(child_id)
        children.append(
            {
                "child_operation_id": child_id,
                "child_index": child_index,
                "target_hash": _require_hash(
                    raw_child.get("target_hash"),
                    "tool_execution_manifest_target_hash_invalid",
                ),
                "arguments_hash": _require_hash(
                    raw_child.get("arguments_hash"),
                    "tool_execution_manifest_arguments_hash_invalid",
                ),
            }
        )

    expected_count = value.get("expected_child_count")
    if (
        not isinstance(expected_count, int)
        or isinstance(expected_count, bool)
        or expected_count != len(children)
    ):
        raise ValueError("tool_execution_manifest_expected_count_mismatch")
    if cardinality in {"single", "atomic_batch"} and expected_count != 1:
        raise ValueError("tool_execution_manifest_atomic_count_invalid")
    recovery_hash = value.get("recovery_manifest_hash")
    if recovery_hash is not None:
        recovery_hash = _require_hash(
            recovery_hash,
            "tool_execution_manifest_recovery_hash_invalid",
        )

    contract_version = value.get("contract_version")
    if not isinstance(contract_version, int) or isinstance(contract_version, bool) or contract_version < 1:
        raise ValueError("tool_execution_manifest_contract_version_invalid")
    resource_version = value.get("resource_version")
    if not isinstance(resource_version, int) or isinstance(resource_version, bool) or resource_version < 1:
        raise ValueError("tool_execution_manifest_resource_version_invalid")
    normalized = {
        "schema_version": 1,
        "parent_operation_id": _require_id(
            value.get("parent_operation_id"),
            "tool_execution_manifest_parent_id_invalid",
        ),
        "authorization_binding_hash": _require_hash(
            value.get("authorization_binding_hash"),
            "tool_execution_manifest_authorization_hash_invalid",
        ),
        "effect_cardinality": cardinality,
        "expected_child_count": expected_count,
        "skill_id": _require_id(value.get("skill_id"), "tool_execution_manifest_skill_id_invalid"),
        "tool_id": _require_id(value.get("tool_id"), "tool_execution_manifest_tool_id_invalid"),
        "contract_version": contract_version,
        "descriptor_hash": _require_hash(
            value.get("descriptor_hash"),
            "tool_execution_manifest_descriptor_hash_invalid",
        ),
        "resource_version": resource_version,
        "parent_arguments_hash": _require_hash(
            value.get("parent_arguments_hash"),
            "tool_execution_manifest_arguments_hash_invalid",
        ),
        "children": children,
        "recovery_manifest_hash": recovery_hash,
        "sensitivity": sensitivity,
        "persistence": persistence,
    }
    return _bounded_control_payload(normalized)


def build_execution_manifest(
    *,
    parent_operation_id: str,
    authorization_binding_hash: str,
    effect_cardinality: str,
    skill_id: str,
    tool_id: str,
    contract_version: int,
    descriptor_hash: str,
    resource_version: int,
    parent_arguments_hash: str,
    children: list[Mapping[str, Any]],
    recovery_manifest_hash: str | None,
    sensitivity: str,
    persistence: str,
) -> dict[str, Any]:
    return normalize_execution_manifest(
        {
            "schema_version": 1,
            "parent_operation_id": parent_operation_id,
            "authorization_binding_hash": authorization_binding_hash,
            "effect_cardinality": effect_cardinality,
            "expected_child_count": len(children),
            "skill_id": skill_id,
            "tool_id": tool_id,
            "contract_version": contract_version,
            "descriptor_hash": descriptor_hash,
            "resource_version": resource_version,
            "parent_arguments_hash": parent_arguments_hash,
            "children": children,
            "recovery_manifest_hash": recovery_manifest_hash,
            "sensitivity": sensitivity,
            "persistence": persistence,
        }
    )


@dataclass(frozen=True, slots=True)
class TicketEffectManifestReservation:
    repository: TicketRepository

    def reserve(
        self,
        *,
        ticket_id: str,
        request_id: str,
        manifest: Mapping[str, Any],
        domain_reservation: Callable[[Any, Mapping[str, Any], str, str | None], Mapping[str, Any]]
        | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_execution_manifest(manifest)
        manifest_hash = content_hash(normalized)
        return self.repository.reserve_execution_manifest_atomic(
            ticket_id=ticket_id,
            request_id=request_id,
            manifest=normalized,
            manifest_hash=manifest_hash,
            domain_reservation=domain_reservation,
        )


def _normalize_receipt(
    *,
    child_operation_id: str,
    receipt: Mapping[str, Any],
    persistence: str,
) -> dict[str, Any]:
    if not isinstance(receipt, Mapping) or not set(receipt).issubset(_RECEIPT_FIELDS):
        raise ValueError("ticket_effect_receipt_shape_invalid")
    required = {
        "capability",
        "action",
        "resource_key",
        "status",
        "expected_effect",
        "validator_name",
        "validator_version",
        "resource_locator",
    }
    if not required.issubset(receipt):
        raise ValueError("ticket_effect_receipt_fields_missing")
    if receipt.get("operation_id") not in {None, child_operation_id}:
        raise ValueError("ticket_effect_receipt_operation_conflict")
    expected_receipt_key = f"ticket-effect-receipt:v1:{child_operation_id}"
    if receipt.get("idempotency_key") not in {None, expected_receipt_key}:
        raise ValueError("ticket_effect_receipt_idempotency_conflict")
    if str(receipt.get("status") or "").strip().casefold() != "committed":
        raise ValueError("ticket_effect_receipt_not_committed")
    if not str(receipt.get("committed_at") or "").strip():
        raise ValueError("ticket_effect_receipt_commit_time_required")
    for field in ("expected_effect", "resource_locator", "execution_observation", "result"):
        if field in receipt and not isinstance(receipt.get(field), Mapping):
            raise ValueError("ticket_effect_receipt_mapping_invalid")
    if persistence != "standard" and (
        receipt.get("execution_observation") or receipt.get("result")
    ):
        raise ValueError("ticket_redacted_receipt_content_forbidden")
    normalized = {
        "operation_id": child_operation_id,
        "idempotency_key": expected_receipt_key,
        "capability": _require_id(
            receipt.get("capability"), "ticket_effect_receipt_capability_invalid"
        ),
        "action": _require_id(receipt.get("action"), "ticket_effect_receipt_action_invalid"),
        "resource_key": _require_id(
            receipt.get("resource_key"), "ticket_effect_receipt_resource_key_invalid"
        ),
        "status": "committed",
        "expected_effect": dict(receipt.get("expected_effect") or {}),
        "validator_name": _require_id(
            receipt.get("validator_name"), "ticket_effect_receipt_validator_invalid"
        ),
        "validator_version": _require_id(
            receipt.get("validator_version"), "ticket_effect_receipt_validator_version_invalid"
        ),
        "resource_locator": dict(receipt.get("resource_locator") or {}),
        "provider_resource_id": (
            str(receipt["provider_resource_id"])
            if receipt.get("provider_resource_id") is not None
            else None
        ),
        "provider_revision": (
            str(receipt["provider_revision"])
            if receipt.get("provider_revision") is not None
            else None
        ),
        "committed_at": (
            str(receipt["committed_at"]) if receipt.get("committed_at") is not None else None
        ),
        "execution_observation": dict(receipt.get("execution_observation") or {}),
        "result": dict(receipt.get("result") or {}),
    }
    return _bounded_control_payload(normalized)


def reduce_ticket_effects(repository: TicketRepository, ticket_id: str) -> dict[str, Any]:
    manifests = repository.list_execution_manifests(ticket_id)
    if not manifests:
        return {
            "status": "no_manifest",
            "ticket_status": None,
            "reason": "execution_manifest_absent",
            "expected_count": 0,
            "terminal_count": 0,
            "verified_count": 0,
            "missing_child_operation_ids": [],
        }
    expected_children: dict[str, tuple[str, str, int]] = {}
    for entry in manifests:
        manifest = normalize_execution_manifest(entry.get("structured_payload") or {})
        parent_id = str(manifest["parent_operation_id"])
        for child in manifest["children"]:
            child_id = str(child["child_operation_id"])
            if child_id in expected_children:
                raise ValueError("ticket_manifest_child_binding_conflict")
            expected_children[child_id] = (
                parent_id,
                content_hash(manifest),
                int(child["child_index"]),
            )

    entries = repository.list_entries(ticket_id)
    outcome_by_child: dict[str, str] = {}
    for entry in entries:
        if entry.get("entry_type") != "child_outcome":
            continue
        payload = entry.get("structured_payload") or {}
        if not isinstance(payload, Mapping) or set(payload) != _CHILD_OUTCOME_FIELDS:
            raise ValueError("ticket_child_outcome_shape_invalid")
        child_id = str(payload.get("child_operation_id") or "")
        state = str(payload.get("effect_state") or "")
        if child_id not in expected_children or state not in _TERMINAL_CHILD_STATES:
            raise ValueError("ticket_child_outcome_binding_invalid")
        expected_parent, expected_hash, expected_index = expected_children[child_id]
        if (
            payload.get("schema_version") != 1
            or payload.get("parent_operation_id") != expected_parent
            or payload.get("parent_manifest_hash") != expected_hash
            or payload.get("child_index") != expected_index
            or (state == "verified") != bool(payload.get("receipt_hash"))
        ):
            raise ValueError("ticket_child_outcome_binding_invalid")
        if child_id in outcome_by_child and outcome_by_child[child_id] != state:
            raise ValueError("ticket_child_outcome_conflict")
        outcome_by_child[child_id] = state

    receipts_by_child: dict[str, list[dict[str, Any]]] = {}
    for receipt in repository.list_receipts(ticket_id):
        child_id = str(receipt.get("operation_id") or "")
        if child_id in expected_children:
            receipts_by_child.setdefault(child_id, []).append(receipt)
    for child_id, state in outcome_by_child.items():
        receipt_count = len(receipts_by_child.get(child_id) or [])
        if (state == "verified" and receipt_count != 1) or (
            state != "verified" and receipt_count != 0
        ):
            raise ValueError("ticket_child_outcome_receipt_conflict")

    missing = sorted(set(expected_children) - set(outcome_by_child))
    verified = sum(1 for state in outcome_by_child.values() if state == "verified")
    terminal = len(outcome_by_child)
    expected = len(expected_children)
    if missing:
        status = "queued"
        ticket_status = "reconciliation_required"
        reason = "expected_child_outcomes_missing"
    elif verified == expected:
        status = "completed"
        ticket_status = "verification_pending"
        reason = "all_children_verified"
    elif verified:
        status = "partial"
        ticket_status = "reconciliation_required"
        reason = "partial_effect_terminal"
    else:
        status = "failed"
        ticket_status = "escalated"
        states = set(outcome_by_child.values())
        reason = "all_children_cancelled" if states == {"cancelled"} else "no_effect_terminal"
    return {
        "status": status,
        "ticket_status": ticket_status,
        "reason": reason,
        "expected_count": expected,
        "terminal_count": terminal,
        "verified_count": verified,
        "missing_child_operation_ids": missing,
    }


class AsyncChildOutcomeSink:
    def __init__(
        self,
        *,
        repository: TicketRepository,
        event_log: Any | None = None,
    ) -> None:
        self._repository = repository
        self._event_log = event_log

    def record_terminal_child(
        self,
        *,
        parent_operation_id: str,
        parent_manifest_hash: str,
        child_operation_id: str,
        effect_state: str,
        receipt: Mapping[str, Any] | None = None,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        parent_id = _require_id(parent_operation_id, "ticket_parent_operation_id_invalid")
        child_id = _require_id(child_operation_id, "ticket_child_operation_id_invalid")
        manifest_hash = _require_hash(
            parent_manifest_hash, "ticket_parent_manifest_hash_invalid"
        )
        state = str(effect_state or "").strip().casefold()
        if state not in _TERMINAL_CHILD_STATES:
            raise ValueError("ticket_child_outcome_state_invalid")
        manifest_entry = self._repository.get_entry_by_dedupe(
            f"tool-execution-manifest:v1:{parent_id}"
        )
        if manifest_entry is None:
            raise LookupError("ticket_execution_manifest_not_found")
        manifest = normalize_execution_manifest(manifest_entry.get("structured_payload") or {})
        if content_hash(manifest) != manifest_hash:
            raise ValueError("ticket_parent_manifest_hash_conflict")
        child = next(
            (
                item
                for item in manifest["children"]
                if item["child_operation_id"] == child_id
            ),
            None,
        )
        if child is None:
            raise ValueError("ticket_child_operation_not_in_manifest")
        if state == "verified" and receipt is None:
            raise ValueError("ticket_verified_child_receipt_required")
        if state != "verified" and receipt is not None:
            raise ValueError("ticket_no_effect_child_receipt_forbidden")
        normalized_receipt = (
            _normalize_receipt(
                child_operation_id=child_id,
                receipt=receipt,
                persistence=str(manifest["persistence"]),
            )
            if receipt is not None
            else None
        )
        normalized_reason = str(reason_code or "").strip().casefold() or None
        if normalized_reason is not None and not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", normalized_reason):
            raise ValueError("ticket_child_outcome_reason_invalid")
        outcome = _bounded_control_payload(
            {
                "schema_version": 1,
                "parent_operation_id": parent_id,
                "parent_manifest_hash": manifest_hash,
                "child_operation_id": child_id,
                "child_index": child["child_index"],
                "effect_state": state,
                "reason_code": normalized_reason,
                "receipt_hash": (
                    content_hash(normalized_receipt) if normalized_receipt is not None else None
                ),
            }
        )
        persisted = self._repository.record_child_outcome_atomic(
            ticket_id=str(manifest_entry["ticket_id"]),
            request_id=str(manifest_entry["request_id"]),
            outcome=outcome,
            receipt=normalized_receipt,
        )
        aggregate = reduce_ticket_effects(
            self._repository,
            str(manifest_entry["ticket_id"]),
        )
        if self._event_log is not None:
            self._event_log.record(
                "ticket.child_outcome",
                str(manifest_entry["ticket_id"]),
                {
                    "parent_operation_id": parent_id,
                    "parent_manifest_hash": manifest_hash,
                    "child_operation_id": child_id,
                    "effect_state": state,
                    "expected_count": aggregate["expected_count"],
                    "terminal_count": aggregate["terminal_count"],
                    "verified_count": aggregate["verified_count"],
                },
            )
        return {**persisted, "aggregate": aggregate}
