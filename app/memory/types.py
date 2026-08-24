from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class MemoryEntry:
    timestamp: str
    session_id: str
    user_id: str
    source: str
    intent: str
    route: str
    request_text: str
    response_summary: str
    metadata: dict[str, Any]
    operation_id: str | None = None


class MemoryStore(Protocol):
    def add_entry(self, entry: MemoryEntry) -> None:
        """Persist one memory entry."""

    def recent_entries(self, limit: int = 50) -> list[MemoryEntry]:
        """Read recent memory entries."""
