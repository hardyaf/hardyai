from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from app.skills.domains.lists.service import ListsService
from app.skills.domains.lists.storage import ListsStorage
from app.skills.tool_contracts import (
    ToolArgumentCanonicalizationError,
    ToolCallEnvelope,
    thaw_json,
)


LISTS_TYPED_TOOLS = frozenset(
    {
        "lists.list_collections",
        "lists.get_collection",
        "lists.create_collection",
        "lists.add_items",
    }
)
_COLLECTION_REF_PREFIX = "collection_v1:"
_ITEM_REF_PREFIX = "item_v1:"
_DEICTIC_NAMES = frozenset({"it", "that", "this", "same", "same list", "that list", "this list"})


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ListsToolHandler:
    """Typed Lists operations over the existing Lists storage authority."""

    SKILL_ID = "skill.lists.core"

    def __init__(self, *, storage: ListsStorage) -> None:
        self._storage = storage

    def canonicalize_tool_arguments(
        self,
        *,
        tool_id: str,
        validated_arguments: Mapping[str, Any],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_tool_id = str(tool_id or "").strip().casefold()
        if normalized_tool_id not in LISTS_TYPED_TOOLS:
            raise ToolArgumentCanonicalizationError("lists_tool_unsupported")
        owner = self._requesting_user(request_context)
        arguments = dict(validated_arguments)

        if normalized_tool_id == "lists.list_collections":
            if arguments:
                raise ToolArgumentCanonicalizationError("lists_collection_discovery_arguments_invalid")
            return {}
        if normalized_tool_id == "lists.create_collection":
            if set(arguments) != {"name"}:
                raise ToolArgumentCanonicalizationError("lists_collection_name_invalid")
            return {"name": self._collection_name(arguments.get("name"))}

        limit = self._limit(arguments.get("limit", 100)) if normalized_tool_id == "lists.get_collection" else None
        selector = self._selector(arguments=arguments, owner_user_id=owner)
        result: dict[str, Any]
        if selector.get("collection_ref"):
            result = {"collection_ref": selector["collection_ref"]}
        else:
            result = {"name": selector["name"]}
        if limit is not None:
            result["limit"] = limit
        if normalized_tool_id == "lists.add_items":
            raw_items = arguments.get("items")
            if not isinstance(raw_items, (list, tuple)):
                raise ToolArgumentCanonicalizationError("lists_items_invalid")
            items = [str(item).strip() for item in raw_items]
            if any(not item for item in items):
                raise ToolArgumentCanonicalizationError("lists_item_empty")
            result["items"] = items
        return result

    def execute_tool(
        self,
        *,
        envelope: ToolCallEnvelope,
        services: dict[str, Any],
    ) -> dict[str, Any]:
        del services
        if not isinstance(envelope, ToolCallEnvelope) or envelope.skill_id != self.SKILL_ID:
            return self._denied("lists_tool_envelope_invalid")
        if envelope.tool_id not in LISTS_TYPED_TOOLS:
            return self._denied("lists_tool_unsupported")
        owner = envelope.user_id.strip().lower()
        if not owner:
            return self._denied("lists_tool_user_missing")
        arguments = thaw_json(envelope.arguments)
        try:
            if envelope.tool_id == "lists.list_collections":
                return self._list_collections(owner_user_id=owner, arguments=arguments)
            if envelope.tool_id == "lists.get_collection":
                return self._get_collection(owner_user_id=owner, arguments=arguments)
            if envelope.tool_id == "lists.create_collection":
                return self._create_collection(owner_user_id=owner, arguments=arguments, envelope=envelope)
            return self._add_items(owner_user_id=owner, arguments=arguments, envelope=envelope)
        except ValueError as exc:
            code = str(exc).strip().casefold()
            if code == "list_operation_id_conflict":
                return self._denied(code)
            return {
                "status": "error",
                "message": "The Lists operation could not complete safely.",
            }

    def _list_collections(
        self,
        *,
        owner_user_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        limit = self._limit(arguments.get("limit", 100))
        records = self._authorized_records(owner_user_id)
        projected = [self._collection(record, requesting_user=owner_user_id) for record in records[:limit]]
        return {
            "status": "ok",
            "message": f"Found {len(projected)} authorized list collection(s).",
            "payload": {
                "collections": projected,
                "owner_scope": "personal_and_shared",
                "truncated": len(records) > limit,
            },
        }

    def _get_collection(
        self,
        *,
        owner_user_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        record, candidates = self._resolve_arguments(arguments, owner_user_id=owner_user_id)
        limit = self._limit(arguments.get("limit", 100))
        if record is None:
            return {
                "status": "needs_input",
                "message": "Please choose one exact list collection.",
                "missing_fields": ["collection_ref"],
                "payload": {
                    "items": [],
                    "owner_scope": "unresolved",
                    "truncated": False,
                    "candidates": candidates,
                },
            }
        entries = self._storage.list_item_entries(
            owner_user_id=str(record["owner_user_id"]),
            list_name=str(record["list_name_normalized"]),
        )
        items = [self._item(entry) for entry in entries[:limit]]
        return {
            "status": "ok",
            "message": f"Retrieved {len(items)} item(s) from the selected list.",
            "payload": {
                "collection": self._collection(record, requesting_user=owner_user_id),
                "items": items,
                "owner_scope": self._owner_scope(record, owner_user_id),
                "truncated": len(entries) > limit,
                "candidates": [],
            },
        }

    def _create_collection(
        self,
        *,
        owner_user_id: str,
        arguments: dict[str, Any],
        envelope: ToolCallEnvelope,
    ) -> dict[str, Any]:
        name = self._collection_name(arguments.get("name"))
        row = self._storage.create_collection(
            owner_user_id=owner_user_id,
            list_name=name,
            list_name_normalized=name,
            created_by=envelope.user_id,
            timestamp=_utc_now(),
            operation_id=envelope.operation_id,
            arguments_hash=envelope.arguments_hash,
        )
        created = bool(row.get("created"))
        return {
            "status": "ok",
            "message": (
                "Created an empty list collection; no items were added by this call."
                if created
                else "The list collection already exists; no items were added by this call."
            ),
            "payload": {
                "collection": self._collection(row, requesting_user=owner_user_id),
                "created": created,
                "idempotent_replay": bool(row.get("idempotent_replay")),
            },
            "receipt_id": self._receipt_ref(envelope.operation_id),
            "committed_effect": created,
        }

    def _add_items(
        self,
        *,
        owner_user_id: str,
        arguments: dict[str, Any],
        envelope: ToolCallEnvelope,
    ) -> dict[str, Any]:
        record, candidates = self._resolve_arguments(arguments, owner_user_id=owner_user_id)
        if record is None:
            return {
                "status": "needs_input",
                "message": "Please choose one exact list collection before adding items.",
                "missing_fields": ["collection_ref"],
                "payload": {
                    "added_items": [],
                    "existing_item_count": 0,
                    "failed_items": [],
                    "candidates": candidates,
                    "idempotent_replay": False,
                },
            }
        raw_items = arguments.get("items")
        if not isinstance(raw_items, list):
            return self._denied("lists_items_invalid")
        items = [str(item).strip() for item in raw_items]
        if not 1 <= len(items) <= 50 or any(not item or len(item) > 500 for item in items):
            return self._denied("lists_items_invalid")
        result = self._storage.add_items(
            owner_user_id=str(record["owner_user_id"]),
            list_id=str(record["list_id"]),
            item_names=items,
            added_by=envelope.user_id,
            timestamp=_utc_now(),
            operation_id=envelope.operation_id,
            arguments_hash=envelope.arguments_hash,
        )
        added = [self._item(item) for item in result.get("items") or []]
        return {
            "status": "ok",
            "message": f"Added {len(added)} item(s) to the selected list.",
            "payload": {
                "collection_ref": self._collection_ref(str(record["list_id"])),
                "added_items": added,
                "existing_item_count": int(result.get("existing_item_count") or 0),
                "failed_items": [],
                "candidates": [],
                "idempotent_replay": bool(result.get("idempotent_replay")),
            },
            "receipt_id": self._receipt_ref(envelope.operation_id),
            "committed_effect": not bool(result.get("idempotent_replay")),
        }

    def _selector(self, *, arguments: dict[str, Any], owner_user_id: str) -> dict[str, str]:
        collection_ref = str(arguments.get("collection_ref") or "").strip()
        name = str(arguments.get("name") or "").strip()
        if bool(collection_ref) == bool(name):
            raise ToolArgumentCanonicalizationError("lists_collection_selector_invalid")
        if collection_ref:
            record = self._record_for_ref(collection_ref, owner_user_id=owner_user_id)
            if record is None:
                raise ToolArgumentCanonicalizationError("lists_collection_not_authorized")
            return {"collection_ref": self._collection_ref(str(record["list_id"]))}
        normalized_name = self._collection_name(name)
        matches = self._records_for_name(normalized_name, owner_user_id=owner_user_id)
        if len(matches) == 1:
            return {"collection_ref": self._collection_ref(str(matches[0]["list_id"]))}
        return {"name": normalized_name}

    def _resolve_arguments(
        self,
        arguments: Mapping[str, Any],
        *,
        owner_user_id: str,
    ) -> tuple[dict[str, object] | None, list[dict[str, Any]]]:
        collection_ref = str(arguments.get("collection_ref") or "").strip()
        if collection_ref:
            return self._record_for_ref(collection_ref, owner_user_id=owner_user_id), []
        name = self._collection_name(arguments.get("name"))
        matches = self._records_for_name(name, owner_user_id=owner_user_id)
        if len(matches) == 1:
            return matches[0], []
        return None, [self._collection(item, requesting_user=owner_user_id) for item in matches[:5]]

    def _authorized_records(self, owner_user_id: str) -> list[dict[str, object]]:
        owners = [owner_user_id]
        if owner_user_id != "all":
            owners.append("all")
        records: list[dict[str, object]] = []
        for owner in owners:
            records.extend(self._storage.list_records(owner_user_id=owner))
        records.sort(
            key=lambda item: (
                0 if str(item.get("owner_user_id") or "") == owner_user_id else 1,
                str(item.get("list_name_normalized") or ""),
                str(item.get("list_id") or ""),
            )
        )
        return records

    def _records_for_name(self, name: str, *, owner_user_id: str) -> list[dict[str, object]]:
        return [
            record
            for record in self._authorized_records(owner_user_id)
            if str(record.get("list_name_normalized") or "") == name
        ]

    def _record_for_ref(
        self,
        collection_ref: str,
        *,
        owner_user_id: str,
    ) -> dict[str, object] | None:
        list_id = self._parse_collection_ref(collection_ref)
        for owner in (owner_user_id, "all") if owner_user_id != "all" else (owner_user_id,):
            record = self._storage.get_list_record_by_id(owner_user_id=owner, list_id=list_id)
            if record is not None:
                return record
        return None

    def _collection(self, record: Mapping[str, Any], *, requesting_user: str) -> dict[str, Any]:
        owner = str(record.get("owner_user_id") or "")
        name = str(record.get("list_name") or record.get("list_name_normalized") or "").strip()
        entries = self._storage.list_item_entries(
            owner_user_id=owner,
            list_name=str(record.get("list_name_normalized") or ""),
        )
        return {
            "collection_ref": self._collection_ref(str(record.get("list_id") or "")),
            "name": name,
            "owner_scope": self._owner_scope(record, requesting_user),
            "item_count": len(entries),
            "updated_at": str(record.get("updated_at") or ""),
        }

    @staticmethod
    def _item(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "item_ref": _ITEM_REF_PREFIX + str(record.get("item_id") or ""),
            "text": str(record.get("item_name") or ""),
            "checked": bool(record.get("checked")),
            "position": max(1, int(record.get("position") or 1)),
        }

    @staticmethod
    def _owner_scope(record: Mapping[str, Any], requesting_user: str) -> str:
        return "personal" if str(record.get("owner_user_id") or "") == requesting_user else "shared"

    @staticmethod
    def _requesting_user(context: Mapping[str, Any]) -> str:
        user = str(context.get("requested_by_user_id") or context.get("user_id") or "").strip().lower()
        if not user:
            raise ToolArgumentCanonicalizationError("lists_tool_user_missing")
        return user

    @staticmethod
    def _collection_name(value: Any) -> str:
        normalized = ListsService.normalize_list_name(str(value or ""))
        if not normalized or len(normalized) > 100 or normalized in _DEICTIC_NAMES:
            raise ToolArgumentCanonicalizationError("lists_collection_name_invalid")
        return normalized

    @staticmethod
    def _limit(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            raise ToolArgumentCanonicalizationError("lists_limit_invalid")
        return value

    @staticmethod
    def _collection_ref(list_id: str) -> str:
        cleaned = str(list_id or "").strip()
        if not cleaned or len(cleaned) > 200:
            raise ValueError("list_collection_ref_invalid")
        return _COLLECTION_REF_PREFIX + cleaned

    @staticmethod
    def _parse_collection_ref(value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned.startswith(_COLLECTION_REF_PREFIX):
            raise ToolArgumentCanonicalizationError("lists_collection_ref_invalid")
        list_id = cleaned[len(_COLLECTION_REF_PREFIX) :]
        if not list_id or len(list_id) > 200 or any(char.isspace() for char in list_id):
            raise ToolArgumentCanonicalizationError("lists_collection_ref_invalid")
        return list_id

    @staticmethod
    def _receipt_ref(operation_id: str) -> str:
        return "list_receipt:" + str(operation_id)

    @staticmethod
    def _denied(code: str) -> dict[str, Any]:
        return {
            "status": "policy_denied",
            "message": "The Lists operation is not authorized in this request context.",
            "denial_reason": str(code or "lists_tool_denied").strip().casefold(),
        }
