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

    def clear(self) -> None:
        self._switches.clear()
        self._actions.clear()

