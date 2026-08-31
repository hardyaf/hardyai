from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from app.db.sqlite_store import SQLiteStore


class ListsStorage(Protocol):
    def list_records(self, *, owner_user_id: str) -> list[dict[str, object]]:
        """Return stable list records for one exact owner scope."""

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

    def get_list_record_by_id(
        self,
        *,
        owner_user_id: str,
        list_id: str,
    ) -> dict[str, object] | None:
        """Return one stable list identity within one exact owner scope."""

    def create_collection(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        list_name_normalized: str,
        created_by: str,
        timestamp: str,
        operation_id: str,
        arguments_hash: str,
    ) -> dict[str, object]:
        """Create one list and its completed operation identity atomically."""

    def add_items(
        self,
        *,
        owner_user_id: str,
        list_id: str,
        item_names: list[str],
        added_by: str,
        timestamp: str,
        operation_id: str,
        arguments_hash: str,
    ) -> dict[str, object]:
        """Add one bounded item array and its operation identity atomically."""

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

    def list_records(self, *, owner_user_id: str) -> list[dict[str, object]]:
        return [
            dict(item)
            for item in self._sqlite_store.list_lists(owner_user_id.strip().lower())
        ]

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

    def get_list_record_by_id(
        self,
        *,
        owner_user_id: str,
        list_id: str,
    ) -> dict[str, object] | None:
        row = self._sqlite_store.get_list_by_id(
            owner_user_id.strip().lower(),
            list_id.strip(),
        )
        return dict(row) if isinstance(row, dict) else None

    def create_collection(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        list_name_normalized: str,
        created_by: str,
        timestamp: str,
        operation_id: str,
        arguments_hash: str,
    ) -> dict[str, object]:
        return self._sqlite_store.create_list_with_operation(
            owner_user_id=owner_user_id,
            list_name=list_name,
            list_name_normalized=list_name_normalized,
            created_by=created_by,
            timestamp=timestamp,
            operation_id=operation_id,
            arguments_hash=arguments_hash,
        )

    def add_items(
        self,
        *,
        owner_user_id: str,
        list_id: str,
        item_names: list[str],
        added_by: str,
        timestamp: str,
        operation_id: str,
        arguments_hash: str,
    ) -> dict[str, object]:
        return self._sqlite_store.add_list_items_with_operation(
            owner_user_id=owner_user_id,
            list_id=list_id,
            item_names=item_names,
            added_by=added_by,
            timestamp=timestamp,
            operation_id=operation_id,
            arguments_hash=arguments_hash,
        )

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
        self._list_ids: dict[tuple[str, str], str] = {}
        self._list_updated_at: dict[tuple[str, str], str] = {}
        self._operations: dict[str, dict[str, object]] = {}

    def _owner_bucket(self, owner_user_id: str) -> dict[str, list[dict[str, object]]]:
        owner = owner_user_id.strip().lower() or "all"
        bucket = self._lists_by_owner.get(owner)
        if bucket is None:
            bucket = {}
            self._lists_by_owner[owner] = bucket
        return bucket

    def list_names(self, *, owner_user_id: str) -> list[str]:
        return list(self._owner_bucket(owner_user_id).keys())

    def list_records(self, *, owner_user_id: str) -> list[dict[str, object]]:
        owner = owner_user_id.strip().lower() or "all"
        bucket = self._owner_bucket(owner)
        return [
            {
                "list_id": self._list_ids.setdefault((owner, name), str(uuid4())),
                "owner_user_id": owner,
                "list_name": name,
                "list_name_normalized": name,
                "created_by": "memory",
                "created_at": self._list_updated_at.get((owner, name)),
                "updated_at": self._list_updated_at.get((owner, name)),
            }
            for name in bucket
        ]

    def ensure_list(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        created_by: str,
        timestamp: str,
    ) -> str:
        del created_by
        owner = owner_user_id.strip().lower() or "all"
        bucket = self._owner_bucket(owner_user_id)
        bucket.setdefault(list_name, [])
        self._list_ids.setdefault((owner, list_name), str(uuid4()))
        self._list_updated_at[(owner, list_name)] = timestamp
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
        owner = owner_user_id.strip().lower() or "all"
        bucket = self._owner_bucket(owner_user_id)
        if list_name not in bucket:
            return None
        return {
            "list_id": self._list_ids.setdefault((owner, list_name), str(uuid4())),
            "owner_user_id": owner,
            "list_name": list_name,
            "list_name_normalized": list_name,
            "updated_at": self._list_updated_at.get((owner, list_name)),
        }

    def get_list_record_by_id(
        self,
        *,
        owner_user_id: str,
        list_id: str,
    ) -> dict[str, object] | None:
        for record in self.list_records(owner_user_id=owner_user_id):
            if str(record.get("list_id") or "") == list_id:
                return record
        return None

    def create_collection(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        list_name_normalized: str,
        created_by: str,
        timestamp: str,
        operation_id: str,
        arguments_hash: str,
    ) -> dict[str, object]:
        owner = owner_user_id.strip().lower() or "all"
        existing_operation = self._operations.get(operation_id)
        if existing_operation is not None:
            if (
                existing_operation.get("owner_user_id") != owner
                or existing_operation.get("action") != "lists.create_collection"
                or existing_operation.get("arguments_hash") != arguments_hash
            ):
                raise ValueError("list_operation_id_conflict")
            record = self.get_list_record_by_id(
                owner_user_id=owner,
                list_id=str(existing_operation.get("target_ref") or ""),
            )
            if record is None:
                raise ValueError("list_operation_replay_target_missing")
            return {
                **record,
                "created": bool(existing_operation.get("created")),
                "idempotent_replay": True,
            }
        bucket = self._owner_bucket(owner)
        created = list_name_normalized not in bucket
        bucket.setdefault(list_name_normalized, [])
        list_id = self._list_ids.setdefault((owner, list_name_normalized), str(uuid4()))
        self._list_updated_at[(owner, list_name_normalized)] = timestamp
        self._operations[operation_id] = {
            "owner_user_id": owner,
            "action": "lists.create_collection",
            "target_ref": list_id,
            "arguments_hash": arguments_hash,
            "created": created,
        }
        return {
            "list_id": list_id,
            "owner_user_id": owner,
            "list_name": list_name if created else list_name_normalized,
            "list_name_normalized": list_name_normalized,
            "created_by": created_by,
            "created_at": timestamp,
            "updated_at": timestamp,
            "created": created,
            "idempotent_replay": False,
        }

    def add_items(
        self,
        *,
        owner_user_id: str,
        list_id: str,
        item_names: list[str],
        added_by: str,
        timestamp: str,
        operation_id: str,
        arguments_hash: str,
    ) -> dict[str, object]:
        owner = owner_user_id.strip().lower() or "all"
        existing_operation = self._operations.get(operation_id)
        if existing_operation is not None:
            if (
                existing_operation.get("owner_user_id") != owner
                or existing_operation.get("action") != "lists.add_items"
                or existing_operation.get("target_ref") != list_id
                or existing_operation.get("arguments_hash") != arguments_hash
            ):
                raise ValueError("list_operation_id_conflict")
            item_ids = set(existing_operation.get("item_ids") or [])
            record = self.get_list_record_by_id(owner_user_id=owner, list_id=list_id)
            if record is None:
                raise ValueError("list_operation_replay_target_missing")
            entries = self.list_item_entries(
                owner_user_id=owner,
                list_name=str(record["list_name_normalized"]),
            )
            replayed = [entry for entry in entries if str(entry.get("item_id") or "") in item_ids]
            if len(replayed) != len(item_ids):
                raise ValueError("list_operation_replay_target_missing")
            return {
                "list_id": list_id,
                "items": replayed,
                "existing_item_count": int(existing_operation.get("existing_item_count") or 0),
                "idempotent_replay": True,
            }
        record = self.get_list_record_by_id(owner_user_id=owner, list_id=list_id)
        if record is None:
            raise ValueError("list_collection_not_authorized")
        list_name_normalized = str(record["list_name_normalized"])
        entries = self._owner_bucket(owner)[list_name_normalized]
        existing_count = len(entries)
        inserted: list[dict[str, object]] = []
        for item_name in item_names:
            entry = {
                "item_id": str(uuid4()),
                "list_id": list_id,
                "item_name": item_name,
                "checked": False,
                "position": len(entries) + 1,
                "operation_id": operation_id,
                "added_by": added_by,
                "added_at": timestamp,
                "updated_at": timestamp,
            }
            entries.append(entry)
            inserted.append(dict(entry))
        self._list_updated_at[(owner, list_name_normalized)] = timestamp
        self._operations[operation_id] = {
            "owner_user_id": owner,
            "action": "lists.add_items",
            "target_ref": list_id,
            "arguments_hash": arguments_hash,
            "existing_item_count": existing_count,
            "item_ids": [str(item["item_id"]) for item in inserted],
        }
        return {
            "list_id": list_id,
            "items": inserted,
            "existing_item_count": existing_count,
            "idempotent_replay": False,
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
        self._list_ids.clear()
        self._list_updated_at.clear()
        self._operations.clear()
