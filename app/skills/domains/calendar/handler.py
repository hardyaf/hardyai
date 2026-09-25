from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from app.skills.domains.calendar.receipts import build_typed_operation_receipt
from app.skills.tool_contracts import (
    ToolArgumentCanonicalizationError,
    ToolCallEnvelope,
    thaw_json,
)


CALENDAR_TYPED_TOOLS = frozenset(
    {
        "calendar.query_events",
        "calendar.get_event",
        "calendar.create_event",
        "calendar.create_event_with_invites",
        "calendar.update_event",
        "calendar.delete_event",
    }
)
CALENDAR_WRITE_TOOLS = CALENDAR_TYPED_TOOLS - {
    "calendar.query_events",
    "calendar.get_event",
}


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
    """Typed Calendar reads and bounded writes over the existing provider boundary."""

    SKILL_ID = "skill.productivity.calendar"

    def __init__(self, *, calendar_service: Any | None = None) -> None:
        self._calendar_service = calendar_service

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
        if normalized_tool_id in CALENDAR_WRITE_TOOLS:
            if self._calendar_service is None:
                raise ToolArgumentCanonicalizationError("calendar_service_unavailable")
            try:
                normalized = self._canonical_write_arguments(
                    tool_id=normalized_tool_id,
                    arguments=arguments,
                )
                return self._calendar_service.canonicalize_typed_write(
                    tool_id=normalized_tool_id,
                    arguments=normalized,
                )
            except ToolArgumentCanonicalizationError:
                raise
            except ValueError as exc:
                raise ToolArgumentCanonicalizationError(str(exc)) from exc
        scope = str(arguments.get("calendar_scope") or "").strip()
        if not scope:
            raise ToolArgumentCanonicalizationError("calendar_scope_missing")
        if normalized_tool_id == "calendar.get_event":
            event_ref = str(arguments.get("event_ref") or "").strip().casefold()
            event_start = str(arguments.get("event_start") or "").strip()
            if not re.fullmatch(r"calendar_event_v1_[0-9a-f]{32}", event_ref):
                raise ToolArgumentCanonicalizationError("calendar_event_ref_invalid")
            if not event_start:
                raise ToolArgumentCanonicalizationError("calendar_event_start_invalid")
            return {
                "calendar_scope": scope,
                "event_ref": event_ref,
                "event_start": event_start,
            }
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
        if envelope.tool_id == "calendar.query_events":
            return service.query_events(
                start=str(arguments.get("start") or ""),
                end=str(arguments.get("end") or ""),
                calendar_scope=str(arguments.get("calendar_scope") or ""),
                time_basis=str(arguments.get("time_basis") or ""),
                text=str(arguments.get("text") or "").strip() or None,
                order=str(arguments.get("order") or "oldest"),
                limit=int(arguments.get("limit", 20)),
            )
        if envelope.tool_id == "calendar.get_event":
            return service.get_typed_event(
                calendar_scope=str(arguments.get("calendar_scope") or ""),
                event_ref=str(arguments.get("event_ref") or ""),
                event_start=str(arguments.get("event_start") or ""),
            )
        result = service.execute_typed_write(
            tool_id=envelope.tool_id,
            operation_id=envelope.operation_id,
            arguments_hash=envelope.arguments_hash,
            arguments=arguments,
        )
        if result.get("status") == "ok" and result.get("source") == "google_live":
            receipt = build_typed_operation_receipt(envelope=envelope, result=result)
            if receipt is not None:
                result["_operation_receipt"] = receipt
        return result

    def _canonical_write_arguments(
        self,
        *,
        tool_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        calendar_scope = str(arguments.get("calendar_scope") or "").strip()
        timezone_name = str(arguments.get("timezone") or "").strip()
        if not calendar_scope:
            raise ToolArgumentCanonicalizationError("calendar_scope_missing")
        try:
            ZoneInfo(timezone_name)
        except Exception as exc:
            raise ToolArgumentCanonicalizationError("calendar_timezone_invalid") from exc
        canonical: dict[str, Any] = {
            "calendar_scope": calendar_scope,
            "timezone": timezone_name,
        }
        for key in ("calendar_ref", "resource_version"):
            value = str(arguments.get(key) or "").strip()
            if value:
                canonical[key] = value

        if tool_id in {"calendar.create_event", "calendar.create_event_with_invites"}:
            canonical.update(
                self._canonical_event_spec(
                    title=arguments.get("title"),
                    start=arguments.get("start"),
                    end=arguments.get("end"),
                    all_day=arguments.get("all_day"),
                    timezone_name=timezone_name,
                    location=arguments.get("location"),
                    description=arguments.get("description"),
                )
            )
            invitees = arguments.get("invitee_emails")
            if tool_id == "calendar.create_event":
                if invitees is not None:
                    raise ToolArgumentCanonicalizationError("calendar_invitees_forbidden")
            else:
                canonical["invitee_emails"] = self._invitee_emails(invitees)
            if arguments.get("recurrence") is not None:
                canonical["recurrence"] = self._canonical_recurrence(
                    arguments.get("recurrence"), timezone_name=timezone_name
                )
            return canonical

        canonical["event_ref"] = str(arguments.get("event_ref") or "").strip().casefold()
        canonical["event_start"] = str(arguments.get("event_start") or "").strip()
        edit_scope = str(arguments.get("edit_scope") or "single_event").strip().casefold()
        if edit_scope not in {"single_event", "occurrence", "series"}:
            raise ToolArgumentCanonicalizationError("calendar_edit_scope_invalid")
        canonical["edit_scope"] = edit_scope
        if not re.fullmatch(r"calendar_event_v1_[0-9a-f]{32}", canonical["event_ref"]):
            raise ToolArgumentCanonicalizationError("calendar_event_ref_invalid")
        if not canonical["event_start"]:
            raise ToolArgumentCanonicalizationError("calendar_event_start_invalid")
        if tool_id == "calendar.delete_event":
            return canonical

        raw_patch = arguments.get("patch")
        if not isinstance(raw_patch, Mapping) or not raw_patch:
            raise ToolArgumentCanonicalizationError("calendar_patch_invalid")
        patch = dict(raw_patch)
        allowed = {
            "title",
            "start",
            "end",
            "all_day",
            "location",
            "description",
            "recurrence",
            "clear_recurrence",
        }
        if set(patch) - allowed:
            raise ToolArgumentCanonicalizationError("calendar_patch_field_forbidden")
        timing = {"start", "end", "all_day"} & set(patch)
        if timing and timing != {"start", "end", "all_day"}:
            raise ToolArgumentCanonicalizationError("calendar_patch_interval_incomplete")
        normalized_patch: dict[str, Any] = {}
        if timing:
            interval = self._canonical_event_spec(
                title="unchanged",
                start=patch.get("start"),
                end=patch.get("end"),
                all_day=patch.get("all_day"),
                timezone_name=timezone_name,
                location=None,
                description=None,
            )
            normalized_patch.update(
                {key: interval[key] for key in ("start", "end", "all_day")}
            )
        for field, maximum in (("title", 200), ("location", 300), ("description", 2000)):
            if field in patch:
                value = str(patch.get(field) or "").strip()
                if not value or len(value) > maximum:
                    raise ToolArgumentCanonicalizationError(f"calendar_patch_{field}_invalid")
                normalized_patch[field] = value
        if "recurrence" in patch and patch.get("clear_recurrence") is True:
            raise ToolArgumentCanonicalizationError("calendar_recurrence_patch_ambiguous")
        if "recurrence" in patch or "clear_recurrence" in patch:
            if edit_scope != "series":
                raise ToolArgumentCanonicalizationError(
                    "calendar_recurrence_update_requires_series"
                )
            if "clear_recurrence" in patch:
                if patch.get("clear_recurrence") is not True:
                    raise ToolArgumentCanonicalizationError(
                        "calendar_clear_recurrence_invalid"
                    )
                normalized_patch["recurrence"] = None
            else:
                normalized_patch["recurrence"] = self._canonical_recurrence(
                    patch.get("recurrence"), timezone_name=timezone_name
                )
        canonical["patch"] = normalized_patch
        return canonical

    @staticmethod
    def _canonical_recurrence(value: Any, *, timezone_name: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ToolArgumentCanonicalizationError("calendar_recurrence_invalid")
        recurrence = dict(value)
        allowed = {"frequency", "interval", "count", "until", "by_weekday"}
        if set(recurrence) - allowed:
            raise ToolArgumentCanonicalizationError("calendar_recurrence_field_forbidden")
        frequency = str(recurrence.get("frequency") or "").strip().casefold()
        if frequency not in {"daily", "weekly", "monthly", "yearly"}:
            raise ToolArgumentCanonicalizationError("calendar_recurrence_frequency_invalid")
        interval = recurrence.get("interval", 1)
        if isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= 52:
            raise ToolArgumentCanonicalizationError("calendar_recurrence_interval_invalid")
        count = recurrence.get("count")
        until = str(recurrence.get("until") or "").strip()
        if count is not None and until:
            raise ToolArgumentCanonicalizationError("calendar_recurrence_end_ambiguous")
        if count is not None and (
            isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 366
        ):
            raise ToolArgumentCanonicalizationError("calendar_recurrence_count_invalid")
        if until:
            try:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", until):
                    date.fromisoformat(until)
                else:
                    parsed = datetime.fromisoformat(until.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError
                    parsed.astimezone(ZoneInfo(timezone_name))
            except Exception as exc:
                raise ToolArgumentCanonicalizationError("calendar_recurrence_until_invalid") from exc
        raw_days = recurrence.get("by_weekday") or []
        if not isinstance(raw_days, (list, tuple)) or len(raw_days) > 7:
            raise ToolArgumentCanonicalizationError("calendar_recurrence_weekdays_invalid")
        days = [str(item or "").strip().upper() for item in raw_days]
        if any(day not in {"MO", "TU", "WE", "TH", "FR", "SA", "SU"} for day in days):
            raise ToolArgumentCanonicalizationError("calendar_recurrence_weekday_invalid")
        if len(days) != len(set(days)) or (days and frequency != "weekly"):
            raise ToolArgumentCanonicalizationError("calendar_recurrence_weekdays_invalid")
        result: dict[str, Any] = {"frequency": frequency, "interval": interval}
        if count is not None:
            result["count"] = count
        if until:
            result["until"] = until
        if days:
            result["by_weekday"] = days
        return result

    @staticmethod
    def _canonical_event_spec(
        *,
        title: Any,
        start: Any,
        end: Any,
        all_day: Any,
        timezone_name: str,
        location: Any,
        description: Any,
    ) -> dict[str, Any]:
        normalized_title = str(title or "").strip()
        if not normalized_title or len(normalized_title) > 200:
            raise ToolArgumentCanonicalizationError("calendar_event_title_invalid")
        if not isinstance(all_day, bool):
            raise ToolArgumentCanonicalizationError("calendar_event_all_day_invalid")
        raw_start = str(start or "").strip()
        raw_end = str(end or "").strip()
        try:
            if all_day:
                parsed_start = date.fromisoformat(raw_start)
                parsed_end = date.fromisoformat(raw_end)
                if parsed_start >= parsed_end:
                    raise ValueError
                normalized_start = parsed_start.isoformat()
                normalized_end = parsed_end.isoformat()
            else:
                parsed_start_dt = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
                parsed_end_dt = datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
                if (
                    parsed_start_dt.tzinfo is None
                    or parsed_end_dt.tzinfo is None
                    or parsed_start_dt >= parsed_end_dt
                ):
                    raise ValueError
                normalized_start = parsed_start_dt.isoformat(timespec="seconds")
                normalized_end = parsed_end_dt.isoformat(timespec="seconds")
        except Exception as exc:
            raise ToolArgumentCanonicalizationError("calendar_event_interval_invalid") from exc
        result: dict[str, Any] = {
            "title": normalized_title,
            "start": normalized_start,
            "end": normalized_end,
            "all_day": all_day,
        }
        for field, raw, maximum in (
            ("location", location, 300),
            ("description", description, 2000),
        ):
            value = str(raw or "").strip()
            if value:
                if len(value) > maximum:
                    raise ToolArgumentCanonicalizationError(f"calendar_event_{field}_invalid")
                result[field] = value
        return result

    @staticmethod
    def _invitee_emails(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 20:
            raise ToolArgumentCanonicalizationError("calendar_invitee_emails_invalid")
        normalized = sorted(str(item or "").strip().casefold() for item in value)
        if any(
            not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", item) or len(item) > 254
            for item in normalized
        ):
            raise ToolArgumentCanonicalizationError("calendar_invitee_email_invalid")
        if len(normalized) != len(set(normalized)):
            raise ToolArgumentCanonicalizationError("calendar_invitee_email_duplicate")
        return normalized

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
