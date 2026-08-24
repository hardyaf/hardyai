from __future__ import annotations

import re
from typing import Any


def _split_list_items(item_text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", item_text).strip(" ,.")
    if not cleaned:
        return []

    parts: list[str]
    if "," in cleaned:
        comma_normalized = re.sub(r"\s+(?:and|&)\s+", ", ", cleaned, flags=re.IGNORECASE)
        parts = [part.strip(" ,.") for part in comma_normalized.split(",") if part.strip(" ,.")]
    elif re.search(r"\s+(?:and|&)\s+", cleaned, flags=re.IGNORECASE):
        parts = [
            part.strip(" ,.")
            for part in re.split(r"\s+(?:and|&)\s+", cleaned, flags=re.IGNORECASE)
            if part.strip(" ,.")
        ]
        if len(parts) == 2:
            left_tokens = [token.strip(" ,.") for token in parts[0].split() if token.strip(" ,.")]
            if len(left_tokens) >= 2:
                parts = left_tokens + [parts[1]]
    else:
        parts = [cleaned]

    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(part)
    return deduped


def _format_quoted_items(items: list[str]) -> str:
    quoted = [f'"{item}"' for item in items if item]
    if not quoted:
        return "items"
    if len(quoted) == 1:
        return quoted[0]
    if len(quoted) == 2:
        return f"{quoted[0]} and {quoted[1]}"
    return f"{', '.join(quoted[:-1])}, and {quoted[-1]}"


def _add_multiple_list_items(
    *,
    list_service: Any,
    list_name: str,
    item_text: str,
    parsed_items: list[str],
    owner_user_id: str,
    requested_by_user_id: str,
    request_id: str,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for index, item in enumerate(parsed_items):
        item_result = list_service.add_item(
            list_name=list_name,
            item_text=item,
            owner_user_id=owner_user_id,
            added_by=requested_by_user_id,
            operation_id=f"{request_id}:lists.add_item:{index}",
        )
        results.append(
            {
                "item_text": item,
                "status": str(item_result.get("status") or "").strip().lower(),
                "result": item_result,
            }
        )

    successful = [entry for entry in results if entry["status"] == "ok"]
    if not successful:
        return results[0]["result"] if results else {"status": "error", "message": "No items to add."}

    resolved_list_name = str(successful[-1]["result"].get("list_name") or list_name).strip()
    current_items = list_service.get_items(
        list_name=resolved_list_name,
        owner_user_id=owner_user_id,
    )
    merged_result = current_items if str(current_items.get("status") or "").strip().lower() == "ok" else {}

    added_items = [entry["item_text"] for entry in successful]
    failed_entries = [entry for entry in results if entry["status"] != "ok"]
    if failed_entries:
        failed_items = [
            {
                "item_text": entry["item_text"],
                "status": entry["status"],
                "message": str(entry["result"].get("message") or "").strip() or None,
            }
            for entry in failed_entries
        ]
        added_text = _format_quoted_items(added_items)
        return {
            **merged_result,
            "status": "partial",
            "list_name": resolved_list_name,
            "item_text": item_text,
            "item_texts": parsed_items,
            "added_count": len(added_items),
            "added_items": added_items,
            "failed_count": len(failed_items),
            "failed_items": failed_items,
            "message": f"Added {added_text} to {resolved_list_name}, but some items failed.",
        }

    added_text = _format_quoted_items(added_items)
    return {
        **merged_result,
        "status": "ok",
        "list_name": resolved_list_name,
        "item_text": item_text,
        "item_texts": parsed_items,
        "added_count": len(added_items),
        "added_items": added_items,
        "message": f"Added {added_text} to {resolved_list_name}.",
    }


def _remove_multiple_list_items(
    *,
    list_service: Any,
    list_name: str,
    item_text: str,
    parsed_items: list[str],
    owner_user_id: str,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for item in parsed_items:
        item_result = list_service.remove_item(
            list_name=list_name,
            item_text=item,
            owner_user_id=owner_user_id,
        )
        results.append(
            {
                "item_text": item,
                "status": str(item_result.get("status") or "").strip().lower(),
                "result": item_result,
            }
        )

    successful = [entry for entry in results if entry["status"] == "ok"]
    if not successful:
        return results[0]["result"] if results else {"status": "error", "message": "No items to remove."}

    resolved_list_name = str(successful[-1]["result"].get("list_name") or list_name).strip()
    current_items = list_service.get_items(
        list_name=resolved_list_name,
        owner_user_id=owner_user_id,
    )
    merged_result = current_items if str(current_items.get("status") or "").strip().lower() == "ok" else {}

    removed_items = [entry["item_text"] for entry in successful]
    failed_entries = [entry for entry in results if entry["status"] != "ok"]
    if failed_entries:
        failed_items = [
            {
                "item_text": entry["item_text"],
                "status": entry["status"],
                "message": str(entry["result"].get("message") or "").strip() or None,
            }
            for entry in failed_entries
        ]
        removed_text = _format_quoted_items(removed_items)
        return {
            **merged_result,
            "status": "partial",
            "list_name": resolved_list_name,
            "item_text": item_text,
            "item_texts": parsed_items,
            "removed_count": len(removed_items),
            "removed_items": removed_items,
            "failed_count": len(failed_items),
            "failed_items": failed_items,
            "message": f"Removed {removed_text} from {resolved_list_name}, but some removals failed.",
        }

    removed_text = _format_quoted_items(removed_items)
    return {
        **merged_result,
        "status": "ok",
        "list_name": resolved_list_name,
        "item_text": item_text,
        "item_texts": parsed_items,
        "removed_count": len(removed_items),
        "removed_items": removed_items,
        "message": f"Removed {removed_text} from {resolved_list_name}.",
    }


def run(
    *,
    intent: str,
    entities: dict[str, Any],
    services: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    list_service = services.get("lists_service")
    if list_service is None:
        return {"status": "error", "message": "Lists service unavailable."}

    requested_by_user_id = str(context.get("requested_by_user_id") or "all").strip() or "all"
    owner_user_id = str(context.get("list_owner_user_id") or requested_by_user_id).strip() or "all"
    request_id = str(context.get("request_id") or "untracked").strip() or "untracked"

    if intent != "lists.create_list" and hasattr(list_service, "resolve_owner_for_list"):
        owner_user_id = list_service.resolve_owner_for_list(
            list_name=str(entities.get("list_name") or ""),
            preferred_owner_user_id=owner_user_id,
        )

    if intent == "lists.create_list":
        return list_service.create_list(
            list_name=str(entities.get("list_name") or ""),
            owner_user_id=owner_user_id,
            created_by=requested_by_user_id,
        )

    if intent == "lists.add_item":
        list_name = str(entities.get("list_name") or "")
        item_text = str(entities.get("item_text") or "")
        parsed_items = _split_list_items(item_text)
        if len(parsed_items) <= 1:
            add_result = list_service.add_item(
                list_name=list_name,
                item_text=item_text,
                owner_user_id=owner_user_id,
                added_by=requested_by_user_id,
                operation_id=f"{request_id}:lists.add_item:0",
            )
            add_status = str(add_result.get("status") or "").strip().lower()
            resolved_list_name = str(
                add_result.get("resolved_list_name")
                or add_result.get("list_name")
                or list_name
                or ""
            ).strip().lower()
            if add_status == "unknown_list" and resolved_list_name in {"groceries", "to-do"}:
                create_result = list_service.create_list(
                    resolved_list_name,
                    owner_user_id=owner_user_id,
                    created_by=requested_by_user_id,
                )
                if str(create_result.get("status") or "").strip().lower() == "ok":
                    retry_result = list_service.add_item(
                        list_name=resolved_list_name,
                        item_text=item_text,
                        owner_user_id=owner_user_id,
                        added_by=requested_by_user_id,
                        operation_id=f"{request_id}:lists.add_item:0",
                    )
                    if str(retry_result.get("status") or "").strip().lower() == "ok":
                        retry_result["auto_created_list"] = True
                        return retry_result
            return add_result
        return _add_multiple_list_items(
            list_service=list_service,
            list_name=list_name,
            item_text=item_text,
            parsed_items=parsed_items,
            owner_user_id=owner_user_id,
            requested_by_user_id=requested_by_user_id,
            request_id=request_id,
        )

    if intent == "lists.get_items":
        return list_service.get_items(
            list_name=str(entities.get("list_name") or ""),
            owner_user_id=owner_user_id,
        )

    if intent == "lists.delete_list":
        return list_service.delete_list(
            list_name=str(entities.get("list_name") or ""),
            owner_user_id=owner_user_id,
        )

    if intent == "lists.remove_item":
        list_name = str(entities.get("list_name") or "")
        item_text = str(entities.get("item_text") or "")
        parsed_items = _split_list_items(item_text)
        if len(parsed_items) <= 1:
            return list_service.remove_item(
                list_name=list_name,
                item_text=item_text,
                owner_user_id=owner_user_id,
            )
        return _remove_multiple_list_items(
            list_service=list_service,
            list_name=list_name,
            item_text=item_text,
            parsed_items=parsed_items,
            owner_user_id=owner_user_id,
        )

    if intent == "lists.mark_item_done":
        return list_service.mark_item_done(
            list_name=str(entities.get("list_name") or ""),
            item_text=str(entities.get("item_text") or ""),
            completion_mode=str(entities.get("completion_mode") or "").strip() or None,
            owner_user_id=owner_user_id,
        )

    return {"status": "error", "message": f"Unsupported lists intent `{intent}`."}
