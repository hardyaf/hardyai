from __future__ import annotations

from typing import Any, Protocol


class CalendarStorage(Protocol):
    def append_event(self, event: dict[str, Any]) -> int:
        """Append one local event and return current event count."""

    def list_events(self, *, person_name: str | None = None) -> list[dict[str, Any]]:
        """List local events optionally filtered by person."""

    def clear(self) -> None:
        """Clear local storage."""


class InMemoryCalendarStorage:
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def append_event(self, event: dict[str, Any]) -> int:
        self._events.append(dict(event))
        return len(self._events)

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

