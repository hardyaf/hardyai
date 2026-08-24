from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.tickets.repository import content_hash
from app.tickets.types import iso_utc


def _normalized_names(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def build_operation_receipt(
    *,
    intent: str,
    entities: dict[str, Any],
    context: dict[str, Any],
    result: dict[str, Any],
    services: dict[str, Any],
) -> dict[str, Any] | None:
    list_service = services.get("lists_service")
    if list_service is None:
        return None

    request_id = str(context.get("request_id") or "untracked").strip() or "untracked"
    owner_user_id = str(
        result.get("owner_user_id")
        or context.get("list_owner_user_id")
        or context.get("requested_by_user_id")
        or "all"
    ).strip()
    list_name = str(
        result.get("list_name")
        or result.get("resolved_list_name")
        or entities.get("list_name")
        or ""
    ).strip().lower()
    if not list_name:
        return None

    snapshot = list_service.source_snapshot(
        list_name=list_name,
        owner_user_id=owner_user_id,
    )
    list_id = str(snapshot.get("list_id") or result.get("list_id") or "").strip() or None
    resource_key = f"list:{owner_user_id}:{list_id or list_name}"
    action = intent.split(".", 1)[-1]
    expected: dict[str, Any] = {}

    if intent == "lists.create_list":
        expected = {"exists": True}
    elif intent == "lists.add_item":
        added = _normalized_names(result.get("added_items"))
        if not added:
            added = _normalized_names(result.get("item_texts"))
        if not added:
            single = str(result.get("item_text") or entities.get("item_text") or "").strip()
            added = [single] if single else []
        expected = {"exists": True, "items_present": added}
    elif intent == "lists.get_items":
        expected = {
            "exists": bool(snapshot.get("exists")),
            "snapshot_items": _normalized_names(result.get("items")),
        }
    elif intent == "lists.delete_list":
        expected = {"exists": False}
    elif intent == "lists.remove_item":
        removed = _normalized_names(result.get("removed_items"))
        if not removed:
            single = str(result.get("item_text") or entities.get("item_text") or "").strip()
            removed = [single] if single else []
        expected = {"exists": True, "items_absent": removed}
        if result.get("removed_all"):
            expected = {"exists": True, "snapshot_items": []}
    elif intent == "lists.mark_item_done":
        item_id = str(result.get("item_id") or "").strip() or None
        item_name = str(result.get("item_text") or entities.get("item_text") or "").strip()
        if str(result.get("completion_mode") or "").strip().lower() == "remove":
            expected = {"exists": True, "items_absent": [item_name] if item_name else []}
        else:
            expected = {
                "exists": True,
                "checked_item_id": item_id,
                "checked_item_name": item_name or None,
            }
    else:
        return None

    idempotency_key = content_hash(
        {
            "request_id": request_id,
            "intent": intent,
            "entities": entities,
            "resource_key": resource_key,
        }
    )
    operation_id = str(uuid5(NAMESPACE_URL, f"jarvis:{idempotency_key}"))
    result_status = str(result.get("status") or "").strip().lower()
    committed = result_status in {"ok", "partial"}
    return {
        "operation_id": operation_id,
        "idempotency_key": idempotency_key,
        "capability": intent,
        "action": action,
        "resource_key": resource_key,
        "provider_resource_id": list_id,
        "provider_revision": str(snapshot.get("source_revision") or content_hash(snapshot)),
        "status": "committed" if committed else "attempted",
        "committed_at": iso_utc() if committed else None,
        "expected_effect": expected,
        "validator_name": "lists.sqlite",
        "validator_version": "1",
        "resource_locator": {
            "owner_user_id": owner_user_id,
            "list_name": list_name,
            "list_id": list_id,
        },
        "execution_observation": snapshot,
        "result": {key: value for key, value in result.items() if not str(key).startswith("_")},
    }
