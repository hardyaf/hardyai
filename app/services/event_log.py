from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EventRecord:
    timestamp: str
    event_type: str
    session_id: str
    payload: dict[str, Any]


class EventPersistence(Protocol):
    def insert_event(
        self,
        timestamp: str,
        event_type: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Persist one event row."""

    def recent_events(self, limit: int) -> list[dict[str, Any]]:
        """Read persisted events."""


class EventLogService:
    def __init__(self, persistence: EventPersistence | None = None) -> None:
        self._events: list[EventRecord] = []
        self._persistence = persistence

    def record(self, event_type: str, session_id: str, payload: dict[str, Any] | None = None) -> None:
        payload = payload or {}
        self._events.append(
            EventRecord(
                timestamp=_utc_now(),
                event_type=event_type,
                session_id=session_id,
                payload=payload,
            )
        )
        if self._persistence is not None:
            latest = self._events[-1]
            self._persistence.insert_event(
                timestamp=latest.timestamp,
                event_type=event_type,
                session_id=session_id,
                payload=payload,
            )

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if self._persistence is not None:
            return self._persistence.recent_events(limit=limit)
        return [
            {
                "timestamp": event.timestamp,
                "event_type": event.event_type,
                "session_id": event.session_id,
                "payload": event.payload,
            }
            for event in self._events[-limit:]
        ]

    def reset(self) -> None:
        self._events.clear()
