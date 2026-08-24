from __future__ import annotations

from typing import Any


def _clean_text(value: Any) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def _combine_message_and_question(message: str | None, question: str | None) -> str | None:
    if message and question:
        if question.lower() in message.lower():
            return message
        return f"{message}\n{question}"
    if message:
        return message
    if question:
        return question
    return None


def _fallback_text(
    *,
    intent: str,
    route: str,
    result: dict[str, Any],
    status: str | None,
    dialog_mode: str | None,
) -> str:
    sync_status = _clean_text(result.get("sync_status"))
    event = result.get("event")
    if intent == "calendar.add_event" and isinstance(event, dict):
        title = _clean_text(event.get("event_title"))
        when_hint = _clean_text(event.get("when_hint"))
        local_only_suffix = ""
        if sync_status == "not_synced_to_google":
            local_only_suffix = " Saved locally on the house calendar (not synced to Google yet)."
        if title and when_hint:
            return f'Added "{title}" ({when_hint}).{local_only_suffix}'
        if title:
            return f'Added "{title}".{local_only_suffix}'

    if intent == "calendar.view":
        summary = _clean_text(result.get("summary"))
        if summary:
            return summary

    if intent == "calendar.update_event" and isinstance(event, dict):
        title = _clean_text(event.get("event_title"))
        if title and event.get("all_day") is True:
            return f'Updated "{title}" to an all-day event.'
        if title:
            return f'Updated "{title}".'

    if intent == "calendar.delete_event" and isinstance(event, dict):
        title = _clean_text(event.get("event_title"))
        if title:
            return f'Deleted "{title}" from the calendar.'

    if intent == "lists.add_item":
        item_text = _clean_text(result.get("item_text"))
        list_name = _clean_text(result.get("list_name"))
        if item_text and list_name:
            return f'Added "{item_text}" to {list_name}.'

    if intent == "lists.create_list":
        list_name = _clean_text(result.get("list_name"))
        created = result.get("created")
        if list_name and created is True:
            return f"Created `{list_name}`."
        if list_name and created is False:
            return f"`{list_name}` already exists."

    if intent == "lists.get_items":
        list_name = _clean_text(result.get("list_name"))
        items = result.get("items")
        if list_name and isinstance(items, list):
            cleaned_items = [str(item).strip() for item in items if str(item).strip()]
            if not cleaned_items:
                return f"{list_name} is empty."
            return f"{list_name}: {', '.join(cleaned_items)}"

    if intent == "lists.delete_list":
        list_name = _clean_text(result.get("list_name"))
        if list_name:
            return f"Deleted `{list_name}`."

    if intent == "lists.remove_item":
        list_name = _clean_text(result.get("list_name"))
        item_text = _clean_text(result.get("item_text"))
        removed_all = bool(result.get("removed_all"))
        if list_name and removed_all:
            return f"Removed all items from `{list_name}`."
        if list_name and item_text:
            return f"Removed `{item_text}` from `{list_name}`."

    if intent == "lists.mark_item_done":
        list_name = _clean_text(result.get("list_name"))
        item_text = _clean_text(result.get("item_text"))
        mode = _clean_text(result.get("completion_mode"))
        if list_name and item_text and mode == "remove":
            return f"Removed `{item_text}` from `{list_name}`."
        if list_name and item_text:
            return f"Marked `{item_text}` as done in `{list_name}`."

    if intent == "home.set_switch":
        switch_name = _clean_text(result.get("switch_name"))
        action = _clean_text(result.get("action"))
        if switch_name and action:
            return f"Set {switch_name} {action}."

    if dialog_mode == "conversation_pending":
        return "I am waiting on your clarification."

    if status:
        return f"[{route}] {intent}: {status}"
    return f"[{route}] {intent}"


def build_assistant_payload(
    *,
    intent: str,
    route: str,
    result: dict[str, Any],
    dialog: dict[str, Any],
    show_debug_labels: bool = True,
) -> dict[str, Any]:
    message = _clean_text(result.get("message"))
    question = _clean_text(result.get("question"))
    status = _clean_text(result.get("status"))
    dialog_mode = _clean_text(dialog.get("mode")) or "command_action"
    awaiting_fields = dialog.get("awaiting_fields")
    if not isinstance(awaiting_fields, list):
        awaiting_fields = []
    debug_intent_label = _clean_text(result.get("debug_intent_label"))

    text = _combine_message_and_question(message, question)
    if text is None:
        text = _fallback_text(
            intent=intent,
            route=route,
            result=result,
            status=status,
            dialog_mode=dialog_mode,
        )
    if debug_intent_label and show_debug_labels:
        suffix = f"({debug_intent_label})"
        if not text.endswith(suffix):
            text = f"{text} {suffix}"

    return {
        "text": text,
        "message": message,
        "question": question,
        "mode": dialog_mode,
        "turn_complete": dialog.get("turn_complete") is not False,
        "pending_intent": _clean_text(dialog.get("pending_intent")),
        "awaiting_fields": [str(item) for item in awaiting_fields if str(item).strip()],
        "status": status,
        "debug_intent_label": debug_intent_label if show_debug_labels else None,
    }
