from __future__ import annotations

from app.db.sqlite_store import SQLiteStore
from app.memory.types import MemoryEntry


class SQLiteMemoryStore:
    def __init__(self, sqlite_store: SQLiteStore) -> None:
        self._sqlite_store = sqlite_store

    def add_entry(self, entry: MemoryEntry) -> None:
        self._sqlite_store.insert_memory_entry(entry=entry)

    def recent_entries(self, limit: int = 50) -> list[MemoryEntry]:
        return self._sqlite_store.recent_memory_entries(limit=limit)

