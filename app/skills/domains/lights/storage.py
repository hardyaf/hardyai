from __future__ import annotations

from typing import Any, Protocol

from app.db.sqlite_store import SQLiteStore


class LightsStorage(Protocol):
    def list_switches(self) -> list[dict[str, Any]]:
        """Return all known switches."""

    def get_switch(self, name: str) -> dict[str, Any] | None:
        """Return one switch by normalized name."""

    def upsert_switch(
        self,
        *,
        name: str,
        room_name: str | None,
        state: str,
        updated_at: str,
    ) -> None:
        """Create/update one switch state."""

    def insert_action_log(
        self,
        *,
        timestamp: str,
        switch_name: str,
        action: str,
        state_after: str,
        source_interface: str | None,
        requested_by_user_id: str | None,
    ) -> None:
        """Append one switch action log row."""

    def recent_actions(self, *, limit: int) -> list[dict[str, Any]]:
        """Return recent switch action history."""

    def set_device_state(
        self,
        *,
        name: str,
        state: str,
        timestamp: str,
        source_interface: str | None,
        requested_by_user_id: str | None,
        operation_id: str,
        arguments_hash: str,
    ) -> dict[str, Any]:
        """Atomically set one exact simulated device and record one operation."""

    def clear(self) -> None:
        """Clear in-memory state when applicable."""


class SQLiteLightsStorage:
    def __init__(self, sqlite_store: SQLiteStore) -> None:
        self._sqlite_store = sqlite_store

    def list_switches(self) -> list[dict[str, Any]]:
        return self._sqlite_store.list_switches()

    def get_switch(self, name: str) -> dict[str, Any] | None:
        return self._sqlite_store.get_switch(name)

    def upsert_switch(
        self,
        *,
        name: str,
        room_name: str | None,
        state: str,
        updated_at: str,
    ) -> None:
        self._sqlite_store.upsert_switch(
            name=name,
            room_name=room_name,
            state=state,
            updated_at=updated_at,
        )

    def insert_action_log(
        self,
        *,
        timestamp: str,
        switch_name: str,
        action: str,
        state_after: str,
        source_interface: str | None,
        requested_by_user_id: str | None,
    ) -> None:
        self._sqlite_store.insert_switch_action_log(
            timestamp=timestamp,
            switch_name=switch_name,
            action=action,
            state_after=state_after,
            source_interface=source_interface,
            requested_by_user_id=requested_by_user_id,
        )

    def recent_actions(self, *, limit: int) -> list[dict[str, Any]]:
        return self._sqlite_store.recent_switch_actions(limit=limit)

    def set_device_state(
        self,
        *,
        name: str,
        state: str,
        timestamp: str,
        source_interface: str | None,
        requested_by_user_id: str | None,
        operation_id: str,
        arguments_hash: str,
    ) -> dict[str, Any]:
        return self._sqlite_store.set_switch_state_with_operation(
            name=name,
            state=state,
            timestamp=timestamp,
            source_interface=source_interface,
            requested_by_user_id=requested_by_user_id,
            operation_id=operation_id,
            arguments_hash=arguments_hash,
        )

    def clear(self) -> None:
        # SQL-backed rows are cleared by store-level reset routines.
        return None


class InMemoryLightsStorage:
    def __init__(self) -> None:
        self._switches: dict[str, dict[str, Any]] = {}
        self._actions: list[dict[str, Any]] = []

    def list_switches(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "room_name": value.get("room_name"),
                "state": value.get("state", "off"),
                "updated_at": value.get("updated_at"),
            }
            for name, value in sorted(self._switches.items())
        ]

    def get_switch(self, name: str) -> dict[str, Any] | None:
        row = self._switches.get(name)
        if row is None:
            return None
        return {
            "name": name,
            "room_name": row.get("room_name"),
            "state": row.get("state", "off"),
            "updated_at": row.get("updated_at"),
        }

    def upsert_switch(
        self,
        *,
        name: str,
        room_name: str | None,
        state: str,
        updated_at: str,
    ) -> None:
        self._switches[name] = {
            "room_name": room_name,
            "state": state,
            "updated_at": updated_at,
        }

    def insert_action_log(
        self,
        *,
        timestamp: str,
        switch_name: str,
        action: str,
        state_after: str,
        source_interface: str | None,
        requested_by_user_id: str | None,
    ) -> None:
        self._actions.append(
            {
                "timestamp": timestamp,
                "switch_name": switch_name,
                "action": action,
                "state_after": state_after,
                "source_interface": source_interface,
                "requested_by_user_id": requested_by_user_id,
            }
        )

    def recent_actions(self, *, limit: int) -> list[dict[str, Any]]:
        bounded = max(1, min(limit, 1000))
        return list(reversed(self._actions[-bounded:]))

    def set_device_state(
        self,
        *,
        name: str,
        state: str,
        timestamp: str,
        source_interface: str | None,
        requested_by_user_id: str | None,
        operation_id: str,
        arguments_hash: str,
    ) -> dict[str, Any]:
        for action in self._actions:
            if str(action.get("operation_id") or "") != operation_id:
                continue
            if (
                str(action.get("switch_name") or "") != name
                or str(action.get("action") or "") != state
                or str(action.get("arguments_hash") or "") != arguments_hash
            ):
                raise ValueError("home_operation_id_conflict")
            return {
                "switch": {
                    "name": name,
                    "room_name": self._switches.get(name, {}).get("room_name"),
                    "state": str(action.get("state_after") or state),
                    "updated_at": str(action.get("timestamp") or timestamp),
                },
                "changed": bool(action.get("changed")),
                "idempotent_replay": True,
            }
        current = self._switches.get(name)
        if current is None:
            raise ValueError("home_device_not_found")
        changed = str(current.get("state") or "") != state
        if changed:
            current["state"] = state
            current["updated_at"] = timestamp
        self._actions.append(
            {
                "timestamp": timestamp,
                "switch_name": name,
                "action": state,
                "state_after": state,
                "source_interface": source_interface,
                "requested_by_user_id": requested_by_user_id,
                "operation_id": operation_id,
                "arguments_hash": arguments_hash,
                "changed": changed,
            }
        )
        return {
            "switch": {
                "name": name,
                "room_name": current.get("room_name"),
                "state": state,
                "updated_at": current.get("updated_at"),
            },
            "changed": changed,
            "idempotent_replay": False,
        }

    def clear(self) -> None:
        self._switches.clear()
        self._actions.clear()
