from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.memory.types import MemoryEntry, MemoryStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryService:
    def __init__(self, store: MemoryStore | None = None) -> None:
        self._store = store

    def record_interaction(
        self,
        *,
        session_id: str,
        user_id: str,
        source: str,
        intent: str,
        route: str,
        request_text: str,
        response_summary: str,
        metadata: dict[str, Any] | None = None,
        operation_id: str | None = None,
    ) -> None:
        if self._store is None:
            return
        self._store.add_entry(
            MemoryEntry(
                timestamp=_utc_now(),
                session_id=session_id,
                user_id=user_id,
                source=source,
                intent=intent,
                route=route,
                request_text=request_text,
                response_summary=response_summary,
                metadata=metadata or {},
                operation_id=operation_id,
            )
        )

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if self._store is None:
            return []
        return [
            {
                "timestamp": item.timestamp,
                "session_id": item.session_id,
                "user_id": item.user_id,
                "source": item.source,
                "intent": item.intent,
                "route": item.route,
                "request_text": item.request_text,
                "response_summary": item.response_summary,
                "metadata": item.metadata,
            }
            for item in self._store.recent_entries(limit=limit)
        ]
