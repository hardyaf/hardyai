from __future__ import annotations

import re
from typing import Any

from app.core.types import MAIN_ACTION_INTENTS


REPAIR_STATUSES = {
    "resolved_action",
    "needs_clarification",
    "not_actionable",
}
REPAIR_ALLOWED_INTENTS = {intent.value for intent in MAIN_ACTION_INTENTS}


def normalize_repair_payload(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    status = str(raw.get("status") or "").strip().lower()
    if status not in REPAIR_STATUSES:
        return None

    intent: str | None = None
    if status in {"resolved_action", "needs_clarification"}:
        candidate = str(raw.get("intent") or "").strip().lower()
        if candidate not in REPAIR_ALLOWED_INTENTS:
            return None
        intent = candidate

    confidence = _coerce_confidence(raw.get("confidence"))
    if confidence is None:
        confidence = 0.65 if status == "resolved_action" else (0.5 if status == "needs_clarification" else 0.0)

    reasoning = str(raw.get("reasoning") or "").strip()
    if not reasoning:
        return None

    entities = raw.get("entities")
    if not isinstance(entities, dict):
        entities = {}
    entities = _normalize_entities(intent=intent, entities=entities)

    missing_fields = _coerce_string_list(raw.get("missing_fields"))
    if missing_fields is None:
        return None
    missing_fields = _normalize_missing_fields(intent=intent, missing_fields=missing_fields)

    message = _coerce_optional_string(raw.get("message"))
    question = _coerce_optional_string(raw.get("question"))
    source = _coerce_optional_string(raw.get("source"))
    inferred_intent = _coerce_optional_string(raw.get("inferred_intent"))
    inferred_entities = raw.get("inferred_entities")
    if not isinstance(inferred_entities, dict):
        inferred_entities = {}

    if status == "resolved_action":
        if missing_fields:
            return None
    elif status == "needs_clarification":
        if not missing_fields:
            return None
        if message is None and question is None:
            return None
    else:
        intent = None
        entities = {}
        missing_fields = []
        question = None

    return {
        "status": status,
        "intent": intent,
        "confidence": confidence,
        "reasoning": reasoning,
        "entities": entities,
        "missing_fields": missing_fields,
        "message": message,
        "question": question,
        "source": source,
        "inferred_intent": inferred_intent,
        "inferred_entities": inferred_entities,
    }


def _normalize_entities(intent: str | None, entities: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(entities)
    if intent == "calendar.add_event":
        title = _pick_first_text(
            normalized,
            [
                "event_title",
                "event_name",
                "title",
                "name",
                "subject",
                "event",
            ],
        )
        when_hint = _pick_first_text(
            normalized,
            [
                "when_hint",
                "when",
                "start_time",
                "start",
                "start_at",
                "time",
                "date",
                "datetime",
            ],
        )
        person_name = _pick_first_text(
            normalized,
            ["person_name", "person", "owner", "calendar_owner"],
        )
        invitee_names = _coerce_name_list(
            normalized.get("invitee_names")
            or normalized.get("invitees")
            or normalized.get("attendees")
            or normalized.get("guests")
        )

        if title:
            normalized["event_title"] = title
        if when_hint:
            normalized["when_hint"] = when_hint
        if invitee_names:
            normalized["invitee_names"] = invitee_names
        normalized.pop("person_name", None)
        return normalized

    if intent in {"calendar.update_event", "calendar.delete_event"}:
        event_reference = _pick_first_text(
            normalized,
            ["event_reference", "event_name", "event_title", "event", "title", "name", "reference"],
        )
        event_id = _pick_first_text(normalized, ["event_id", "google_event_id"])
        calendar_id = _pick_first_text(normalized, ["calendar_id", "host_calendar_id"])
        if event_reference:
            normalized["event_reference"] = event_reference
        if event_id:
            normalized["event_id"] = event_id
        if calendar_id:
            normalized["calendar_id"] = calendar_id
        if intent == "calendar.update_event":
            new_title = _pick_first_text(
                normalized,
                ["new_event_title", "new_title", "updated_title", "replacement_title", "rename_to"],
            )
            new_when = _pick_first_text(
                normalized,
                ["new_when_hint", "new_when", "new_time", "when_hint", "when", "start_time", "date"],
            )
            all_day = _coerce_optional_bool(normalized.get("all_day"))
            if new_title:
                normalized["new_event_title"] = new_title
            if new_when:
                normalized["new_when_hint"] = new_when
            if all_day is not None:
                normalized["all_day"] = all_day
        return normalized

    if intent == "home.set_switch":
        switch_name = _pick_first_text(normalized, ["switch_name", "switch", "device", "light"])
        action = _pick_first_text(normalized, ["action", "state"])
        if switch_name:
            normalized["switch_name"] = switch_name
        if action:
            normalized["action"] = action
        return normalized

    if intent in {"lists.add_item", "lists.remove_item", "lists.mark_item_done"}:
        item_text = _pick_first_text(normalized, ["item_text", "item"])
        list_name = _pick_first_text(normalized, ["list_name", "list"])
        completion_mode = _pick_first_text(normalized, ["completion_mode", "mode", "mark_mode"])
        if item_text:
            normalized["item_text"] = item_text
        if list_name:
            normalized["list_name"] = list_name
        if completion_mode:
            normalized["completion_mode"] = completion_mode
        return normalized

    if intent in {"lists.get_items", "lists.create_list", "lists.delete_list"}:
        list_name = _pick_first_text(normalized, ["list_name", "list"])
        if list_name:
            normalized["list_name"] = list_name
        return normalized

    return normalized


def _normalize_missing_fields(intent: str | None, missing_fields: list[str]) -> list[str]:
    if intent is None:
        return missing_fields
    normalized: list[str] = []
    for field in missing_fields:
        canonical = _canonical_field_name(intent=intent, field_name=field)
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized


def _canonical_field_name(intent: str, field_name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "", field_name.strip().lower())
    if intent == "calendar.add_event":
        if cleaned in {"eventtitle", "event_name", "eventname", "title", "name", "subject", "event"}:
            return "event_title"
        if cleaned in {"whenhint", "when", "starttime", "start_time", "start", "startat", "start_at", "time", "date", "datetime"}:
            return "when_hint"
    if intent in {"calendar.update_event", "calendar.delete_event"}:
        if cleaned in {
            "eventreference", "event_reference", "event", "eventname", "event_name", "eventtitle", "event_title"
        }:
            return "event_reference"
        if cleaned in {"eventid", "event_id", "googleeventid", "google_event_id"}:
            return "event_id"
        if cleaned in {"calendarid", "calendar_id", "hostcalendarid", "host_calendar_id"}:
            return "calendar_id"
    if intent == "calendar.update_event":
        if cleaned in {"changes", "change", "requestedchange", "requested_change"}:
            return "changes"
        if cleaned in {"neweventtitle", "new_event_title", "newtitle", "new_title", "renameto", "rename_to"}:
            return "new_event_title"
        if cleaned in {"newwhenhint", "new_when_hint", "newwhen", "new_when", "newtime", "new_time"}:
            return "new_when_hint"
    if intent == "home.set_switch":
        if cleaned in {"switch", "switchname", "switch_name", "device", "light"}:
            return "switch_name"
        if cleaned in {"state", "action"}:
            return "action"
    if intent in {
        "lists.add_item",
        "lists.get_items",
        "lists.create_list",
        "lists.delete_list",
        "lists.remove_item",
        "lists.mark_item_done",
    }:
        if cleaned in {"list", "listname", "list_name"}:
            return "list_name"
    if intent in {"lists.add_item", "lists.remove_item", "lists.mark_item_done"} and cleaned in {
        "item",
        "itemtext",
        "item_text",
    }:
        return "item_text"
    if intent == "lists.mark_item_done" and cleaned in {"completionmode", "completion_mode", "mode", "markmode"}:
        return "completion_mode"
    return field_name


def _pick_first_text(container: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = container.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _coerce_name_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidate = re.sub(r"^\s*invite(?:\s+to)?\s+", "", value.strip(), flags=re.IGNORECASE)
        parts = re.split(r"\s*(?:,| and | & )\s*", candidate)
        names = [part.strip(" .") for part in parts if part.strip(" .")]
        return _dedupe_names(names)
    if isinstance(value, list):
        names = [str(item).strip(" .") for item in value if str(item).strip(" .")]
        return _dedupe_names(names)
    return []


def _dedupe_names(names: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(name)
    return deduped


def _coerce_confidence(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    confidence = float(value)
    if confidence < 0.0 or confidence > 1.0:
        return None
    return confidence


def _coerce_string_list(value: Any) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    items: list[str] = []
    for entry in value:
        text = str(entry).strip()
        if text:
            items.append(text)
    return items


def _coerce_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().casefold()
    if normalized in {"true", "1", "yes", "on", "all_day", "all-day", "all day"}:
        return True
    if normalized in {"false", "0", "no", "off", "timed"}:
        return False
    return None
