from __future__ import annotations

from datetime import datetime, timezone

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
