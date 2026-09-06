from __future__ import annotations

from typing import Any, Mapping

from app.skills.tool_contracts import (
    ToolArgumentCanonicalizationError,
    ToolCallEnvelope,
    thaw_json,
)


CALENDAR_TYPED_TOOLS = frozenset({"calendar.query_events"})


def describe_capability(
    *,
    services: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    del context
    calendar_service = services.get("calendar_service")
    if calendar_service is None:
        return {
            "configured": False,
            "authorized_here": False,
            "availability": "unavailable",
            "access_note": "Calendar is not configured in this runtime.",
        }
    return {
        "configured": True,
        "authorized_here": True,
        "availability": "available",
        "access_note": "Authorized Calendar reads are available in this request context.",
    }


class CalendarToolHandler:
    """Typed Calendar reads over the existing domain/provider boundary."""

    SKILL_ID = "skill.productivity.calendar"

    def canonicalize_tool_arguments(
        self,
        *,
        tool_id: str,
        validated_arguments: Mapping[str, Any],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        del request_context
        normalized_tool_id = str(tool_id or "").strip().casefold()
        if normalized_tool_id not in CALENDAR_TYPED_TOOLS:
            raise ToolArgumentCanonicalizationError("calendar_tool_unsupported")
        arguments = dict(validated_arguments)
        scope = str(arguments.get("calendar_scope") or "").strip()
        if not scope:
            raise ToolArgumentCanonicalizationError("calendar_scope_missing")
        time_basis = str(arguments.get("time_basis") or "").strip().casefold()
        if time_basis not in {"local_calendar", "absolute"}:
            raise ToolArgumentCanonicalizationError("calendar_time_basis_invalid")
        normalized: dict[str, Any] = {
            "start": str(arguments.get("start") or "").strip(),
            "end": str(arguments.get("end") or "").strip(),
            "calendar_scope": scope,
            "time_basis": time_basis,
            "order": str(arguments.get("order") or "oldest").strip().casefold(),
            "limit": int(arguments.get("limit", 20)),
        }
        text = str(arguments.get("text") or "").strip()
        if text:
            normalized["text"] = text
        return normalized

    def execute_tool(
        self,
        *,
        envelope: ToolCallEnvelope,
        services: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(envelope, ToolCallEnvelope) or envelope.skill_id != self.SKILL_ID:
            return self._denied("calendar_tool_envelope_invalid")
        if envelope.tool_id not in CALENDAR_TYPED_TOOLS:
            return self._denied("calendar_tool_unsupported")
        service = services.get("calendar_service")
        if service is None:
            return self._denied("calendar_service_unavailable")
        arguments = thaw_json(envelope.arguments)
        return service.query_events(
            start=str(arguments.get("start") or ""),
            end=str(arguments.get("end") or ""),
            calendar_scope=str(arguments.get("calendar_scope") or ""),
            time_basis=str(arguments.get("time_basis") or ""),
            text=str(arguments.get("text") or "").strip() or None,
            order=str(arguments.get("order") or "oldest"),
            limit=int(arguments.get("limit", 20)),
        )

    @staticmethod
    def _denied(reason: str) -> dict[str, Any]:
        return {
            "status": "policy_denied",
            "message": "This Calendar operation is not available in the current request context.",
            "denial_reason": reason,
        }


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
