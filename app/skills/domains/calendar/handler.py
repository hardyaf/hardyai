from __future__ import annotations

from typing import Any


def run(
    *,
    intent: str,
    entities: dict[str, Any],
    services: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    del context
    calendar_service = services.get("calendar_service")
    if calendar_service is None:
        return {"status": "error", "message": "Calendar service unavailable."}

    if intent == "calendar.add_event":
        when_hint = str(entities.get("when_hint")).strip() if entities.get("when_hint") is not None else None
        when_hint = when_hint or None
        invitee_names_raw = entities.get("invitee_names")
        invitee_names: list[str] | None = None
        if isinstance(invitee_names_raw, list):
            invitee_names = [str(item).strip() for item in invitee_names_raw if str(item).strip()]
        if entities.get("invite_explicit") is not True:
            invitee_names = None
        return calendar_service.add_event(
            event_title=str(entities.get("event_title") or ""),
            when_hint=when_hint,
            invitee_names=invitee_names,
        )

    if intent == "calendar.view":
        window = str(entities.get("window") or "daily").strip().lower()
        if window not in {"daily", "weekly"}:
            window = "daily"
        person_name = str(entities.get("person_name") or "").strip() or None
        return calendar_service.view(
            person_name=person_name,
            window=window,
        )

    if intent == "calendar.update_event":
        return calendar_service.update_event(
            event_reference=str(entities.get("event_reference") or ""),
            new_event_title=str(entities.get("new_event_title") or "").strip() or None,
            new_when_hint=str(entities.get("new_when_hint") or "").strip() or None,
            all_day=_optional_bool(entities.get("all_day")),
            event_id=str(entities.get("event_id") or "").strip() or None,
            calendar_id=str(entities.get("calendar_id") or "").strip() or None,
        )

    if intent == "calendar.delete_event":
        return calendar_service.delete_event(
            event_reference=str(entities.get("event_reference") or ""),
            event_id=str(entities.get("event_id") or "").strip() or None,
            calendar_id=str(entities.get("calendar_id") or "").strip() or None,
        )

    return {"status": "error", "message": f"Unsupported calendar intent `{intent}`."}


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().casefold()
    if normalized in {"true", "1", "yes", "on", "all_day", "all-day"}:
        return True
    if normalized in {"false", "0", "no", "off", "timed"}:
        return False
    return None
