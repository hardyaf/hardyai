from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.google.calendar_live import GoogleCalendarLiveService
from app.skills.domains.calendar.storage import CalendarStorage, InMemoryCalendarStorage


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CalendarService:
    def __init__(
        self,
        google_live: GoogleCalendarLiveService | None = None,
        storage: CalendarStorage | None = None,
        suggested_contacts: list[str] | None = None,
    ) -> None:
        self._google_live = google_live
        self._storage = storage or InMemoryCalendarStorage()
        self._suggested_contacts = self._normalize_invitees(suggested_contacts)

    def tool_timezone(self, request_context: dict[str, Any]) -> str | None:
        del request_context
        if self._google_live is None:
            return "UTC"
        try:
            return self._google_live.query_timezone()
        except Exception:  # pragma: no cover - defensive protected-config boundary
            return None

    def query_events(
        self,
        *,
        start: str,
        end: str,
        calendar_scope: str,
        time_basis: str,
        text: str | None = None,
        order: str = "oldest",
        limit: int = 20,
    ) -> dict[str, object]:
        try:
            start_at = self._aware_datetime(start)
            end_at = self._aware_datetime(end)
            normalized_limit = self._query_limit(limit)
        except ValueError:
            return {
                "status": "error",
                "message": "Calendar query arguments were invalid.",
            }
        normalized_scope = str(calendar_scope or "").strip()
        normalized_time_basis = str(time_basis or "").strip().casefold()
        normalized_text = str(text or "").strip()
        normalized_order = str(order or "oldest").strip().casefold()
        if not normalized_scope or len(normalized_scope) > 100:
            return {"status": "error", "message": "Calendar scope was invalid."}
        if (
            normalized_time_basis not in {"local_calendar", "absolute"}
            or len(normalized_text) > 200
            or normalized_order not in {"oldest", "newest"}
        ):
            return {"status": "error", "message": "Calendar query arguments were invalid."}

        if normalized_time_basis == "local_calendar":
            try:
                timezone_name = self.tool_timezone({})
                if not timezone_name:
                    raise ValueError("calendar_timezone_unavailable")
                start_at = self._local_wall_datetime(start, timezone_name=timezone_name)
                end_at = self._local_wall_datetime(end, timezone_name=timezone_name)
            except ValueError:
                return {
                    "status": "error",
                    "message": "Calendar local-time boundaries were invalid.",
                }
            if start_at >= end_at:
                return {
                    "status": "error",
                    "message": "Calendar query start must be before its exclusive end.",
                }
        elif start_at >= end_at:
            return {
                "status": "error",
                "message": "Calendar query start must be before its exclusive end.",
            }

        if self._google_live is not None:
            try:
                result = self._google_live.query_events(
                    start=start_at.isoformat(),
                    end=end_at.isoformat(),
                    calendar_scope=normalized_scope,
                    text=normalized_text or None,
                    order=normalized_order,
                    limit=normalized_limit,
                )
            except Exception:  # pragma: no cover - defensive provider boundary
                return {
                    "status": "retryable_error",
                    "message": "The live Calendar provider was unavailable.",
                }
            if not isinstance(result, dict):
                return {
                    "status": "error",
                    "message": "The live Calendar provider returned an invalid response.",
                }
            return result

        return self._query_local_events(
            start=start_at,
            end=end_at,
            calendar_scope=normalized_scope,
            text=normalized_text,
            order=normalized_order,
            limit=normalized_limit,
        )

    def add_event(
        self,
        event_title: str,
        when_hint: str | None = None,
        invitee_names: list[str] | None = None,
    ) -> dict[str, object]:
        normalized_title = event_title.strip()
        normalized_when_hint = when_hint.strip() if when_hint else ""
        normalized_invitees = self._normalize_invitees(invitee_names)

        missing_fields: list[str] = []
        if not normalized_title or self._is_placeholder_title(normalized_title):
            missing_fields.append("event_title")
        if not normalized_when_hint:
            missing_fields.append("when_hint")

        if missing_fields:
            message = "Event title and schedule are required."
            if missing_fields == ["event_title"]:
                message = "Event title is required."
            elif missing_fields == ["when_hint"]:
                message = "Event schedule is required (for example: `tomorrow at noon` or `daily`)."
            return {
                "status": "needs_input",
                "message": message,
                "missing_fields": missing_fields,
            }

        if self._google_live is not None:
            try:
                live_result = self._google_live.add_event(
                    event_title=normalized_title,
                    when_hint=normalized_when_hint,
                    invitee_names=normalized_invitees,
                )
            except Exception as exc:  # pragma: no cover - defensive live dependency wrapper
                return {
                    "status": "error",
                    "source": "google_live",
                    "message": f"Google Calendar write failed: {exc}",
                }
            if not isinstance(live_result, dict):
                return {
                    "status": "error",
                    "source": "google_live",
                    "message": "Google Calendar write failed: invalid response payload.",
                }
            if live_result.get("status") == "ok":
                return live_result
            return {
                "status": "error",
                "source": "google_live",
                "message": str(live_result.get("message") or "Google Calendar write failed."),
            }

        event = {
            "event_title": normalized_title,
            "when_hint": normalized_when_hint,
            "invitee_names": normalized_invitees,
        }
        count = self._storage.append_event(event)
        suggested_contacts = list(self._suggested_contacts)
        if normalized_invitees:
            suggested_contacts = [name for name in suggested_contacts if name not in normalized_invitees]
        invite_prompt = "Should I invite anyone so this also appears on their personal calendar?"
        if suggested_contacts:
            invite_prompt = (
                f"Should I invite {self._format_contact_names(suggested_contacts)} so this also appears "
                "on their personal calendar?"
            )
        return {
            "status": "ok",
            "source": "local_stub",
            "host_calendar": "house",
            "event": event,
            "count": count,
            "sync_status": "not_synced_to_google",
            "invite_flow": {
                "status": "suggested" if suggested_contacts else "not_configured",
                "prompt": invite_prompt,
                "suggested_contacts": suggested_contacts,
                "recognized_invitees": normalized_invitees,
            },
        }

    def view(self, person_name: str | None = None, window: str = "daily") -> dict[str, object]:
        if self._google_live is not None:
            try:
                live_result = self._google_live.get_calendar_view(person_name=person_name, window=window)
            except Exception as exc:  # pragma: no cover - defensive live dependency wrapper
                return {
                    "status": "error",
                    "source": "google_live",
                    "message": f"Google Calendar view failed: {exc}",
                }
            if not isinstance(live_result, dict):
                return {
                    "status": "error",
                    "source": "google_live",
                    "message": "Google Calendar view failed: invalid response payload.",
                }
            return live_result

        events = self._storage.list_events(person_name=person_name)
        lines = [f"Calendar view ({window}):"]
        if not events:
            lines.append("- No events found.")
        else:
            for event in events:
                lines.append(f"- {event.get('event_title', '(untitled event)')}")
        return {
            "status": "ok",
            "source": "local_stub",
            "window": window,
            "person_name": person_name,
            "event_count": len(events),
            "events": events,
            "summary": "\n".join(lines),
            "generated_at": _utc_now(),
        }

    def update_event(
        self,
        *,
        event_reference: str,
        new_event_title: str | None = None,
        new_when_hint: str | None = None,
        all_day: bool | None = None,
        event_id: str | None = None,
        calendar_id: str | None = None,
    ) -> dict[str, object]:
        if self._google_live is None:
            return {
                "status": "error",
                "source": "local_stub",
                "message": "Existing calendar events can only be updated when Google Calendar is connected.",
                "error_code": "google_calendar_required",
            }
        try:
            result = self._google_live.update_event(
                event_reference=event_reference,
                new_event_title=new_event_title,
                new_when_hint=new_when_hint,
                all_day=all_day,
                event_id=event_id,
                calendar_id=calendar_id,
            )
        except Exception as exc:  # pragma: no cover - defensive live dependency wrapper
            return {
                "status": "error",
                "source": "google_live",
                "message": f"Google Calendar update failed: {exc}",
            }
        if not isinstance(result, dict):
            return {
                "status": "error",
                "source": "google_live",
                "message": "Google Calendar update failed: invalid response payload.",
            }
        return result

    def delete_event(
        self,
        *,
        event_reference: str,
        event_id: str | None = None,
        calendar_id: str | None = None,
    ) -> dict[str, object]:
        if self._google_live is None:
            return {
                "status": "error",
                "source": "local_stub",
                "message": "Existing calendar events can only be deleted when Google Calendar is connected.",
                "error_code": "google_calendar_required",
            }
        try:
            result = self._google_live.delete_event(
                event_reference=event_reference,
                event_id=event_id,
                calendar_id=calendar_id,
            )
        except Exception as exc:  # pragma: no cover - defensive live dependency wrapper
            return {
                "status": "error",
                "source": "google_live",
                "message": f"Google Calendar delete failed: {exc}",
            }
        if not isinstance(result, dict):
            return {
                "status": "error",
                "source": "google_live",
                "message": "Google Calendar delete failed: invalid response payload.",
            }
        return result

    def source_event_by_id(self, *, calendar_id: str, event_id: str) -> dict[str, object]:
        if self._google_live is None:
            return {
                "status": "error",
                "source": "local_stub",
                "error_code": "local_calendar_not_durable",
            }
        return self._google_live.get_event_by_id(calendar_id=calendar_id, event_id=event_id)

    def reset(self) -> None:
        self._storage.clear()

    def _query_local_events(
        self,
        *,
        start: datetime,
        end: datetime,
        calendar_scope: str,
        text: str,
        order: str,
        limit: int,
    ) -> dict[str, object]:
        all_events = self._storage.list_events(person_name=None)
        known_people = sorted(
            {
                str(item.get("person_name") or "").strip()
                for item in all_events
                if str(item.get("person_name") or "").strip()
            },
            key=str.casefold,
        )
        is_default = calendar_scope.casefold() in {
            "default",
            "house",
            "home",
            "household",
            "my",
            "our",
        }
        selected_name: str | None = None
        if not is_default:
            matches = [name for name in known_people if name.casefold() == calendar_scope.casefold()]
            if len(matches) != 1:
                return self._local_query_result(
                    status="needs_input",
                    message="Choose one available local Calendar scope.",
                    start=start,
                    end=end,
                    requested_scope=calendar_scope,
                    display_name=calendar_scope,
                    resolved=False,
                    is_default=False,
                    candidates=known_people[:10],
                    events=[],
                    truncated=False,
                    coverage_complete=False,
                )
            selected_name = matches[0]

        selected_events = [
            item
            for item in all_events
            if selected_name is None
            or str(item.get("person_name") or "").strip().casefold() == selected_name.casefold()
        ]
        projected: list[tuple[datetime, dict[str, Any]]] = []
        unstructured_count = 0
        text_key = text.casefold()
        for index, item in enumerate(selected_events):
            raw_start = str(item.get("start_at") or item.get("start") or "").strip()
            raw_end = str(item.get("end_at") or item.get("end") or raw_start).strip()
            try:
                event_start = self._aware_datetime(raw_start)
                event_end = self._aware_datetime(raw_end)
            except ValueError:
                unstructured_count += 1
                continue
            if event_start >= end or event_end <= start:
                continue
            title = self._bounded_text(item.get("event_title") or item.get("title"), 200, "(untitled event)")
            location = self._bounded_text(item.get("location"), 300, "")
            if text_key and text_key not in f"{title}\n{location}".casefold():
                continue
            ref_material = f"{index}\n{title}\n{event_start.isoformat()}\n{event_end.isoformat()}"
            projected.append(
                (
                    event_start,
                    {
                        "event_ref": "calendar_event_v1_"
                        + hashlib.sha256(ref_material.encode("utf-8")).hexdigest()[:32],
                        "title": title,
                        "start": event_start.isoformat(),
                        "end": event_end.isoformat(),
                        "all_day": bool(item.get("all_day", False)),
                        "location": location,
                        "calendar_name": selected_name or "Local default",
                    },
                )
            )
        projected.sort(key=lambda row: row[0], reverse=order == "newest")
        truncated = len(projected) > limit
        return self._local_query_result(
            status="ok",
            message=f"Found {min(len(projected), limit)} local Calendar event(s).",
            start=start,
            end=end,
            requested_scope=calendar_scope,
            display_name=selected_name or "Local default",
            resolved=True,
            is_default=is_default,
            candidates=[],
            events=[row[1] for row in projected[:limit]],
            truncated=truncated,
            coverage_complete=unstructured_count == 0 and not truncated,
        )

    @staticmethod
    def _local_query_result(
        *,
        status: str,
        message: str,
        start: datetime,
        end: datetime,
        requested_scope: str,
        display_name: str,
        resolved: bool,
        is_default: bool,
        candidates: list[str],
        events: list[dict[str, Any]],
        truncated: bool,
        coverage_complete: bool,
    ) -> dict[str, object]:
        return {
            "status": status,
            "message": message,
            "missing_fields": ["calendar_scope"] if not resolved else [],
            "untrusted": True,
            "payload": {
                "events": events,
                "normalized_range": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "timezone": "UTC",
                },
                "calendar_scope": {
                    "requested": requested_scope,
                    "display_name": display_name,
                    "resolved": resolved,
                    "is_default": is_default,
                    "candidates": candidates,
                },
                "source": {
                    "kind": "local_in_memory",
                    "synchronized": False,
                    "coverage_complete": coverage_complete,
                    "queried_at": _utc_now(),
                },
                "truncated": truncated,
            },
        }

    @staticmethod
    def _aware_datetime(value: str) -> datetime:
        normalized = str(value or "").strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("calendar_datetime_invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("calendar_datetime_not_aware")
        return parsed

    @classmethod
    def _local_wall_datetime(cls, value: str, *, timezone_name: str) -> datetime:
        parsed = cls._aware_datetime(value)
        try:
            selected_timezone = ZoneInfo(str(timezone_name or "").strip())
        except Exception as exc:
            raise ValueError("calendar_timezone_invalid") from exc
        wall_time = parsed.replace(tzinfo=None)
        candidates: list[datetime] = []
        seen_instants: set[datetime] = set()
        for fold in (0, 1):
            candidate = wall_time.replace(tzinfo=selected_timezone, fold=fold)
            round_trip = candidate.astimezone(timezone.utc).astimezone(selected_timezone)
            if round_trip.replace(tzinfo=None) != wall_time:
                continue
            instant = candidate.astimezone(timezone.utc)
            if instant in seen_instants:
                continue
            seen_instants.add(instant)
            candidates.append(candidate)
        if not candidates:
            raise ValueError("calendar_local_time_nonexistent")
        matching_offset = [
            candidate
            for candidate in candidates
            if candidate.utcoffset() == parsed.utcoffset()
        ]
        if len(matching_offset) == 1:
            return matching_offset[0]
        if len(candidates) == 1:
            return candidates[0]
        raise ValueError("calendar_local_time_ambiguous")

    @staticmethod
    def _query_limit(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("calendar_limit_invalid")
        normalized = int(value)
        if normalized < 1 or normalized > 100:
            raise ValueError("calendar_limit_invalid")
        return normalized

    @staticmethod
    def _bounded_text(value: Any, limit: int, default: str) -> str:
        normalized = " ".join(str(value or "").split()).strip()
        return (normalized or default)[:limit]

    @staticmethod
    def _is_placeholder_title(title: str) -> bool:
        normalized = " ".join(title.lower().split())
        if normalized.startswith("a "):
            normalized = normalized[2:]
        elif normalized.startswith("an "):
            normalized = normalized[3:]
        elif normalized.startswith("the "):
            normalized = normalized[4:]
        return normalized in {"event", "meeting", "appointment", "calendar event", "something", "it"}

    @staticmethod
    def _normalize_invitees(invitee_names: list[str] | None) -> list[str]:
        if not invitee_names:
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in invitee_names:
            name = str(item).strip(" .,'\"")
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(name)
        return normalized

    @staticmethod
    def _format_contact_names(names: list[str]) -> str:
        if len(names) <= 1:
            return names[0] if names else "anyone"
        if len(names) == 2:
            return f"{names[0]} or {names[1]}"
        return f"{', '.join(names[:-1])}, or {names[-1]}"
