from __future__ import annotations

from app.memory.types import MemoryEntry, MemoryStore


class CompositeMemoryStore:
    def __init__(self, stores: list[MemoryStore]) -> None:
        self._stores = [store for store in stores]

    def add_entry(self, entry: MemoryEntry) -> None:
        for store in self._stores:
            store.add_entry(entry)

    def recent_entries(self, limit: int = 50) -> list[MemoryEntry]:
        if not self._stores:
            return []
        # Read from the first store by default (SQLite should be first).
        return self._stores[0].recent_entries(limit=limit)

