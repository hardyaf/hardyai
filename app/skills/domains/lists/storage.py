from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from app.db.sqlite_store import SQLiteStore


class ListsStorage(Protocol):
    def list_names(self, *, owner_user_id: str) -> list[str]:
        """Return normalized list names for one owner."""

    def ensure_list(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        created_by: str,
        timestamp: str,
    ) -> str:
        """Create list if missing and return normalized list name."""

    def list_items(self, *, owner_user_id: str, list_name: str) -> list[str]:
        """Return items for one normalized list name."""

    def add_item(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        item_name: str,
        added_by: str,
        timestamp: str,
        operation_id: str | None = None,
    ) -> dict[str, object] | None:
        """Add one item idempotently and return its stable row."""

    def get_list_record(self, *, owner_user_id: str, list_name: str) -> dict[str, object] | None:
        """Return stable list identity and revision fields."""

    def list_item_entries(self, *, owner_user_id: str, list_name: str) -> list[dict[str, object]]:
        """Return item rows for one normalized list name."""

    def remove_item_by_id(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        item_id: str,
        timestamp: str,
    ) -> bool:
        """Remove one item by id from a list and return success."""

    def remove_all_items(self, *, owner_user_id: str, list_name: str, timestamp: str) -> int:
        """Remove all items from a list and return the removed count."""

    def set_item_checked(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        item_id: str,
        checked: bool,
        timestamp: str,
    ) -> bool:
        """Set checked state for one item and return success."""

    def delete_list(self, *, owner_user_id: str, list_name: str) -> bool:
        """Delete one list and its items."""

    def clear(self) -> None:
        """Clear in-memory storage when applicable."""


class SQLiteListsStorage:
    def __init__(self, sqlite_store: SQLiteStore) -> None:
        self._sqlite_store = sqlite_store

    def list_names(self, *, owner_user_id: str) -> list[str]:
        return [
            str(item["list_name_normalized"])
            for item in self._sqlite_store.list_lists(owner_user_id.strip().lower())
        ]

    def ensure_list(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        created_by: str,
        timestamp: str,
    ) -> str:
        row = self._sqlite_store.upsert_list(
            owner_user_id=owner_user_id.strip().lower(),
            list_name=list_name,
            list_name_normalized=list_name,
            created_by=created_by,
            created_at=timestamp,
            updated_at=timestamp,
        )
        return str(row.get("list_name_normalized") or list_name)

    def list_items(self, *, owner_user_id: str, list_name: str) -> list[str]:
        list_row = self._sqlite_store.get_list_by_normalized_name(
            owner_user_id.strip().lower(),
            list_name.strip().lower(),
        )
        if list_row is None:
            return []
        items = self._sqlite_store.list_list_items(str(list_row["list_id"]))
        return [str(item["item_name"]) for item in items]

    def add_item(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        item_name: str,
        added_by: str,
        timestamp: str,
        operation_id: str | None = None,
    ) -> dict[str, object] | None:
        list_row = self._sqlite_store.get_list_by_normalized_name(
            owner_user_id.strip().lower(),
            list_name.strip().lower(),
        )
        if list_row is None:
            return None
        return self._sqlite_store.add_list_item(
            list_id=str(list_row["list_id"]),
            item_name=item_name,
            added_by=added_by,
            long_desc=None,
            qty=None,
            checked=False,
            added_at=timestamp,
            updated_at=timestamp,
            operation_id=operation_id,
        )

    def get_list_record(self, *, owner_user_id: str, list_name: str) -> dict[str, object] | None:
        row = self._sqlite_store.get_list_by_normalized_name(
            owner_user_id.strip().lower(),
            list_name.strip().lower(),
        )
        return dict(row) if isinstance(row, dict) else None

    def list_item_entries(self, *, owner_user_id: str, list_name: str) -> list[dict[str, object]]:
        list_row = self._sqlite_store.get_list_by_normalized_name(
            owner_user_id.strip().lower(),
            list_name.strip().lower(),
        )
        if list_row is None:
            return []
        rows = self._sqlite_store.list_list_items(str(list_row["list_id"]))
        return [
            {
                "item_id": str(row["item_id"]),
                "item_name": str(row["item_name"]),
                "checked": bool(row["checked"]),
                "position": int(row["position"]),
                "operation_id": row.get("operation_id"),
            }
            for row in rows
        ]

    def remove_item_by_id(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        item_id: str,
        timestamp: str,
    ) -> bool:
        list_row = self._sqlite_store.get_list_by_normalized_name(
            owner_user_id.strip().lower(),
            list_name.strip().lower(),
        )
        if list_row is None:
            return False
        return self._sqlite_store.delete_list_item(item_id, updated_at=timestamp)

    def remove_all_items(self, *, owner_user_id: str, list_name: str, timestamp: str) -> int:
        list_row = self._sqlite_store.get_list_by_normalized_name(
            owner_user_id.strip().lower(),
            list_name.strip().lower(),
        )
        if list_row is None:
            return 0
        return self._sqlite_store.delete_all_list_items(
            str(list_row["list_id"]),
            updated_at=timestamp,
        )

    def set_item_checked(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        item_id: str,
        checked: bool,
        timestamp: str,
    ) -> bool:
        list_row = self._sqlite_store.get_list_by_normalized_name(
            owner_user_id.strip().lower(),
            list_name.strip().lower(),
        )
        if list_row is None:
            return False
        return self._sqlite_store.set_list_item_checked(
            item_id=item_id,
            checked=checked,
            updated_at=timestamp,
        )

    def delete_list(self, *, owner_user_id: str, list_name: str) -> bool:
        list_row = self._sqlite_store.get_list_by_normalized_name(
            owner_user_id.strip().lower(),
            list_name.strip().lower(),
        )
        if list_row is None:
            return False
        return self._sqlite_store.delete_list(str(list_row["list_id"]))

    def clear(self) -> None:
        # SQL-backed list rows are cleared by store-level reset routines.
        return None


class InMemoryListsStorage:
    def __init__(self) -> None:
        self._lists_by_owner: dict[str, dict[str, list[dict[str, object]]]] = {}

    def _owner_bucket(self, owner_user_id: str) -> dict[str, list[dict[str, object]]]:
        owner = owner_user_id.strip().lower() or "all"
        bucket = self._lists_by_owner.get(owner)
        if bucket is None:
            bucket = {}
            self._lists_by_owner[owner] = bucket
        return bucket

    def list_names(self, *, owner_user_id: str) -> list[str]:
        return list(self._owner_bucket(owner_user_id).keys())

    def ensure_list(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        created_by: str,
        timestamp: str,
    ) -> str:
        del created_by
        del timestamp
        bucket = self._owner_bucket(owner_user_id)
        bucket.setdefault(list_name, [])
        return list_name

    def list_items(self, *, owner_user_id: str, list_name: str) -> list[str]:
        bucket = self._owner_bucket(owner_user_id)
        items = bucket.get(list_name, [])
        return [str(entry.get("item_name") or "") for entry in items]

    def add_item(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        item_name: str,
        added_by: str,
        timestamp: str,
        operation_id: str | None = None,
    ) -> dict[str, object] | None:
        del added_by
        del timestamp
        bucket = self._owner_bucket(owner_user_id)
        items = bucket.get(list_name)
        if items is None:
            return None
        if operation_id:
            for existing in items:
                if str(existing.get("operation_id") or "") == operation_id:
                    replay = dict(existing)
                    replay["idempotent_replay"] = True
                    return replay
        position = len(items) + 1
        entry = {
            "item_id": str(uuid4()),
            "list_id": f"memory:{owner_user_id}:{list_name}",
            "item_name": item_name,
            "checked": False,
            "position": position,
            "operation_id": operation_id,
            "idempotent_replay": False,
        }
        items.append(entry)
        return dict(entry)

    def get_list_record(self, *, owner_user_id: str, list_name: str) -> dict[str, object] | None:
        bucket = self._owner_bucket(owner_user_id)
        if list_name not in bucket:
            return None
        return {
            "list_id": f"memory:{owner_user_id}:{list_name}",
            "owner_user_id": owner_user_id,
            "list_name": list_name,
            "list_name_normalized": list_name,
            "updated_at": None,
        }

    def list_item_entries(self, *, owner_user_id: str, list_name: str) -> list[dict[str, object]]:
        bucket = self._owner_bucket(owner_user_id)
        items = bucket.get(list_name, [])
        return [dict(item) for item in items]

    def remove_item_by_id(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        item_id: str,
        timestamp: str,
    ) -> bool:
        del timestamp
        bucket = self._owner_bucket(owner_user_id)
        items = bucket.get(list_name)
        if items is None:
            return False
        for index, item in enumerate(items):
            if str(item.get("item_id") or "") != item_id:
                continue
            del items[index]
            for pos, entry in enumerate(items, start=1):
                entry["position"] = pos
            return True
        return False

    def remove_all_items(self, *, owner_user_id: str, list_name: str, timestamp: str) -> int:
        del timestamp
        bucket = self._owner_bucket(owner_user_id)
        items = bucket.get(list_name)
        if items is None:
            return 0
        count = len(items)
        bucket[list_name] = []
        return count

    def set_item_checked(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        item_id: str,
        checked: bool,
        timestamp: str,
    ) -> bool:
        del timestamp
        bucket = self._owner_bucket(owner_user_id)
        items = bucket.get(list_name)
        if items is None:
            return False
        for entry in items:
            if str(entry.get("item_id") or "") != item_id:
                continue
            entry["checked"] = bool(checked)
            return True
        return False

    def delete_list(self, *, owner_user_id: str, list_name: str) -> bool:
        bucket = self._owner_bucket(owner_user_id)
        if list_name not in bucket:
            return False
        del bucket[list_name]
        return True

    def clear(self) -> None:
        self._lists_by_owner.clear()
