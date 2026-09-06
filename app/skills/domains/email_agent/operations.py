from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Mapping

from app.skills.domains.email_agent.catalog import EmailCatalogService
from app.skills.tool_contracts import (
    ToolArgumentCanonicalizationError,
    ToolCallEnvelope,
    canonical_json,
    thaw_json,
    tool_child_operation_id,
)


EMAIL_PROVIDER_OPERATION_TOOLS = frozenset(
    {
        "email.get_operation",
        "email.apply_labels",
        "email.remove_labels",
        "email.set_read_state",
        "email.archive_messages",
        "email.restore_to_inbox",
    }
)
# Compatibility name retained while the version-10 rollback image exists.
EMAIL_MANAGED_LABEL_TOOLS = EMAIL_PROVIDER_OPERATION_TOOLS
SYSTEM_LABEL_UNREAD_REF = "gmail_system_v1_unread"
SYSTEM_LABEL_INBOX_REF = "gmail_system_v1_inbox"
SYSTEM_LABEL_REFS = frozenset({SYSTEM_LABEL_UNREAD_REF, SYSTEM_LABEL_INBOX_REF})
_OPERATION_REF = re.compile(r"emailop_v1_([0-9a-f]{64})")
_LABEL_REF = re.compile(r"label_v1_[0-9a-f]{24}")


class EmailManagedLabelToolExecutor:
    """Request-confined Gmail label-state operations over the Email ledger."""

    def __init__(
        self,
        *,
        storage: Any,
        permissions: Any,
        max_attempts: int,
        utc_clock: Any | None = None,
    ) -> None:
        self._storage = storage
        self._permissions = permissions
        self._max_attempts = max(1, min(int(max_attempts), 10))
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))
        self._catalog = EmailCatalogService(permissions=permissions, storage=storage)
        if permissions.version >= 2:
            now = self._iso(self._now())
            storage.sync_managed_label_catalog(
                labels=[
                    {
                        "label_ref": self._catalog.label_ref(label.key),
                        "policy_key": label.key,
                        "display_name": label.display_name,
                        "gmail_label_name": label.gmail_label_name,
                        "enabled": label.enabled,
                    }
                    for label in permissions.managed_labels
                ],
                now=now,
            )

    def canonicalize(
        self,
        *,
        tool_id: str,
        validated_arguments: Mapping[str, Any],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_tool_id = str(tool_id or "").strip().casefold()
        if normalized_tool_id not in EMAIL_PROVIDER_OPERATION_TOOLS:
            raise ToolArgumentCanonicalizationError("email_tool_unsupported")
        if self._permissions.authorize(request_context) is None:
            raise ToolArgumentCanonicalizationError("email_tool_unauthorized")
        arguments = dict(validated_arguments)
        if normalized_tool_id == "email.get_operation":
            if set(arguments) != {"operation_ref"}:
                raise ToolArgumentCanonicalizationError("email_operation_ref_invalid")
            return {"operation_ref": self._operation_ref(arguments.get("operation_ref"))}
        message_refs = self._message_refs(arguments.get("message_refs"))
        if normalized_tool_id in {"email.apply_labels", "email.remove_labels"}:
            if not self._permissions.additive_label_writes_ready:
                raise ToolArgumentCanonicalizationError("email_managed_labels_not_configured")
            if set(arguments) != {"message_refs", "label_refs"}:
                raise ToolArgumentCanonicalizationError("email_label_arguments_invalid")
            return {
                "message_refs": message_refs,
                "label_refs": self._label_refs(arguments.get("label_refs")),
            }
        if normalized_tool_id == "email.set_read_state":
            if set(arguments) != {"message_refs", "state"}:
                raise ToolArgumentCanonicalizationError("email_read_state_arguments_invalid")
            state = str(arguments.get("state") or "").strip().casefold()
            if state not in {"read", "unread"}:
                raise ToolArgumentCanonicalizationError("email_read_state_invalid")
            return {"message_refs": message_refs, "state": state}
        if set(arguments) != {"message_refs"}:
            raise ToolArgumentCanonicalizationError("email_inbox_state_arguments_invalid")
        return {"message_refs": message_refs}

    def execute(self, *, envelope: ToolCallEnvelope) -> dict[str, Any]:
        if not isinstance(envelope, ToolCallEnvelope) or envelope.skill_id != "skill.email.agent":
            return self._denied("email_tool_envelope_invalid")
        if envelope.tool_id not in EMAIL_PROVIDER_OPERATION_TOOLS:
            return self._denied("email_tool_unsupported")
        if self._envelope_grant(envelope) is None:
            return self._denied("email_tool_scope_changed")
        arguments = thaw_json(envelope.arguments)
        if envelope.tool_id == "email.get_operation":
            return self._get_operation(arguments=arguments, envelope=envelope)
        if (
            envelope.tool_id in {"email.apply_labels", "email.remove_labels"}
            and not self._permissions.additive_label_writes_ready
        ):
            return self._denied("email_managed_labels_not_configured")
        return self._queue_provider_operation(arguments=arguments, envelope=envelope)

    def _queue_provider_operation(
        self,
        *,
        arguments: dict[str, Any],
        envelope: ToolCallEnvelope,
    ) -> dict[str, Any]:
        message_refs = self._message_refs(arguments.get("message_refs"))
        action, label_refs = self._operation_transition(
            tool_id=envelope.tool_id,
            arguments=arguments,
        )
        if envelope.tool_id in {"email.apply_labels", "email.remove_labels"}:
            resolution = self._catalog.resolve_labels(label_refs)
            if resolution.status != "ok":
                return {
                    "status": "needs_input",
                    "message": "One or more managed labels are unavailable; load the current label catalog.",
                    "missing_fields": ["label_refs"],
                    "payload": {
                        "operation_status": "not_reserved",
                        "child_count": 0,
                        "idempotent_replay": False,
                        "candidates": list(resolution.candidates),
                    },
                }
            label_refs = tuple(sorted(resolution.canonical_refs))
        resolved_messages: list[dict[str, str]] = []
        for message_ref in message_refs:
            resolved = self._storage.resolve_reference(
                user_id=envelope.user_id.strip().casefold(),
                discord_channel_id=envelope.channel_scope.strip(),
                reference=message_ref,
                now=self._iso(self._now()),
            )
            message_id = str((resolved or {}).get("gmail_message_id") or "").strip()
            if not message_id:
                return {
                    "status": "needs_input",
                    "message": "One or more Email references expired; query the messages again.",
                    "missing_fields": ["message_refs"],
                    "payload": {
                        "operation_status": "not_reserved",
                        "child_count": 0,
                        "idempotent_replay": False,
                        "candidates": [],
                    },
                }
            resolved_messages.append({"message_ref": message_ref, "gmail_message_id": message_id})
        by_message_id = {item["gmail_message_id"]: item for item in resolved_messages}
        if len(by_message_id) != len(resolved_messages):
            return self._denied("email_message_refs_duplicate_target")
        children: list[dict[str, Any]] = []
        manifest_children: list[dict[str, Any]] = []
        for index, item in enumerate(
            sorted(resolved_messages, key=lambda value: value["gmail_message_id"]),
            start=1,
        ):
            target_ref = "email_message:" + self._opaque(item["gmail_message_id"])
            child_arguments = {"action": action, "label_refs": list(label_refs)}
            child_id, child_hash = tool_child_operation_id(
                operation_id=envelope.operation_id,
                child_index=index,
                canonical_target_ref=target_ref,
                child_arguments=child_arguments,
            )
            child = {
                "child_operation_id": child_id,
                "child_index": index,
                "gmail_message_id": item["gmail_message_id"],
                "action": action,
                "managed_label_refs": list(label_refs),
                "arguments_hash": child_hash,
                "idempotency_key": child_id,
            }
            children.append(child)
            manifest_children.append(
                {
                    "child_operation_id": child_id,
                    "child_index": index,
                    "gmail_message_id": item["gmail_message_id"],
                    "message_ref": item["message_ref"],
                    "managed_label_refs": list(label_refs),
                    "arguments_hash": child_hash,
                }
            )
        manifest = {
            "version": 1,
            "action": action,
            "max_attempts": self._max_attempts,
            "children": manifest_children,
        }
        manifest_hash = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
        try:
            result = self._storage.reserve_managed_label_operation(
                operation_id=envelope.operation_id,
                tool_id=envelope.tool_id,
                owner_user_id=envelope.user_id.strip().casefold(),
                discord_channel_id=envelope.channel_scope.strip(),
                arguments_hash=envelope.arguments_hash,
                recovery_manifest=manifest,
                recovery_manifest_hash=manifest_hash,
                children=children,
                now=self._iso(self._now()),
                max_attempts=self._max_attempts,
            )
        except ValueError as exc:
            return self._denied(str(exc))
        return {
            "status": "queued",
            "message": f"Queued {len(children)} independent Email mailbox operation(s).",
            "payload": {
                "operation_ref": self._to_operation_ref(envelope.operation_id),
                "operation_status": str(result.get("status") or "queued"),
                "child_count": len(children),
                "idempotent_replay": bool(result.get("idempotent_replay")),
                "candidates": [],
            },
            "job_id": self._to_operation_ref(envelope.operation_id),
            "committed_effect": False,
        }

    def _operation_transition(
        self,
        *,
        tool_id: str,
        arguments: dict[str, Any],
    ) -> tuple[str, tuple[str, ...]]:
        if tool_id == "email.apply_labels":
            return "apply", tuple(self._label_refs(arguments.get("label_refs")))
        if tool_id == "email.remove_labels":
            return "remove", tuple(self._label_refs(arguments.get("label_refs")))
        if tool_id == "email.set_read_state":
            state = str(arguments.get("state") or "").strip().casefold()
            if state not in {"read", "unread"}:
                raise ToolArgumentCanonicalizationError("email_read_state_invalid")
            return ("remove" if state == "read" else "apply"), (SYSTEM_LABEL_UNREAD_REF,)
        if tool_id == "email.archive_messages":
            return "remove", (SYSTEM_LABEL_INBOX_REF,)
        if tool_id == "email.restore_to_inbox":
            return "apply", (SYSTEM_LABEL_INBOX_REF,)
        raise ToolArgumentCanonicalizationError("email_tool_unsupported")

    def _get_operation(
        self,
        *,
        arguments: dict[str, Any],
        envelope: ToolCallEnvelope,
    ) -> dict[str, Any]:
        operation_ref = self._operation_ref(arguments.get("operation_ref"))
        row = self._storage.get_managed_label_operation(
            operation_id=self._from_operation_ref(operation_ref),
            owner_user_id=envelope.user_id.strip().casefold(),
            discord_channel_id=envelope.channel_scope.strip(),
        )
        if row is None:
            return {
                "status": "needs_input",
                "message": "That Email operation is unavailable in this request scope.",
                "missing_fields": ["operation_ref"],
                "payload": {
                    "operation_ref": operation_ref,
                    "operation_status": "unavailable",
                    "child_counts": {},
                    "terminal": False,
                },
            }
        status = str(row.get("status") or "unknown")
        return {
            "status": "ok",
            "message": f"Email operation status is {status}.",
            "payload": {
                "operation_ref": operation_ref,
                "operation_status": status,
                "child_counts": dict(row.get("child_counts") or {}),
                "terminal": status in {"completed", "partial", "failed", "cancelled"},
            },
        }

    def _envelope_grant(self, envelope: ToolCallEnvelope) -> Any | None:
        return self._permissions.authorize(
            {
                "source_interface": envelope.source_interface,
                "identity_bound": True,
                "requested_by_user_id": envelope.user_id,
                "discord_channel_id": envelope.channel_scope,
                "external_user_id": envelope.external_user_id,
                "agent_id": envelope.agent_id,
            }
        )

    @staticmethod
    def _message_refs(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 50:
            raise ToolArgumentCanonicalizationError("email_message_refs_invalid")
        refs = [str(item or "").strip().upper() for item in value]
        if any(not re.fullmatch(r"E(?:[1-9]|[1-4][0-9]|50)", item) for item in refs):
            raise ToolArgumentCanonicalizationError("email_message_ref_invalid")
        if len(refs) != len(set(refs)):
            raise ToolArgumentCanonicalizationError("email_message_ref_duplicate")
        return refs

    def _label_refs(self, value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 10:
            raise ToolArgumentCanonicalizationError("email_label_refs_invalid")
        refs = [str(item or "").strip().casefold() for item in value]
        if any(_LABEL_REF.fullmatch(item) is None for item in refs):
            raise ToolArgumentCanonicalizationError("email_label_ref_invalid")
        if len(refs) != len(set(refs)):
            raise ToolArgumentCanonicalizationError("email_label_ref_duplicate")
        return sorted(refs)

    @staticmethod
    def _operation_ref(value: Any) -> str:
        normalized = str(value or "").strip().casefold()
        if not _OPERATION_REF.fullmatch(normalized):
            raise ToolArgumentCanonicalizationError("email_operation_ref_invalid")
        return normalized

    @staticmethod
    def _to_operation_ref(operation_id: str) -> str:
        return "emailop_v1_" + str(operation_id).removeprefix("toolop_v1_")

    @staticmethod
    def _from_operation_ref(operation_ref: str) -> str:
        match = _OPERATION_REF.fullmatch(operation_ref)
        if match is None:
            raise ToolArgumentCanonicalizationError("email_operation_ref_invalid")
        return "toolop_v1_" + match.group(1)

    def _now(self) -> datetime:
        current = self._utc_clock()
        if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("email_operation_clock_invalid")
        return current.astimezone(UTC).replace(microsecond=0)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _opaque(value: str) -> str:
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _denied(reason: str) -> dict[str, Any]:
        return {
            "status": "policy_denied",
            "message": "The Email operation is unavailable in this request context.",
            "denial_reason": str(reason or "email_operation_denied").strip().casefold(),
        }
