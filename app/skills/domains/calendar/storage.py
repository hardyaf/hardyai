from __future__ import annotations

from typing import Any, Protocol


class CalendarStorage(Protocol):
    def append_event(self, event: dict[str, Any]) -> int:
        """Append one local event and return current event count."""

    def list_events(self, *, person_name: str | None = None) -> list[dict[str, Any]]:
        """List local events optionally filtered by person."""

    def clear(self) -> None:
        """Clear local storage."""

    def append_event_once(self, *, operation_id: str, event: dict[str, Any]) -> tuple[int, bool]:
        """Append one exact local operation at most once for this process lifetime."""


class InMemoryCalendarStorage:
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._operation_events: dict[str, dict[str, Any]] = {}

    def append_event(self, event: dict[str, Any]) -> int:
        self._events.append(dict(event))
        return len(self._events)

    def append_event_once(self, *, operation_id: str, event: dict[str, Any]) -> tuple[int, bool]:
        normalized = str(operation_id or "").strip()
        existing = self._operation_events.get(normalized)
        if existing is not None:
            if existing != event:
                raise ValueError("calendar_local_operation_conflict")
            return len(self._events), False
        copied = dict(event)
        self._events.append(copied)
        self._operation_events[normalized] = copied
        return len(self._events), True

    def list_events(self, *, person_name: str | None = None) -> list[dict[str, Any]]:
        target = (person_name or "").strip().lower()
        if target:
            return [
                dict(event)
                for event in self._events
                if str(event.get("person_name") or "").strip().lower() == target
            ]
        return [dict(event) for event in self._events]

    def clear(self) -> None:
        self._events.clear()
        self._operation_events.clear()
