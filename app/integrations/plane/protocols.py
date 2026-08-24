from __future__ import annotations

from typing import Any, Protocol


class WorkBoardClient(Protocol):
    """Narrow board contract used by the durable projection worker."""

    def find_work_item_by_external_id(self, external_id: str) -> dict[str, Any] | None: ...

    def create_work_item(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def update_work_item(self, work_item_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def list_states(self) -> list[dict[str, Any]]: ...

    def create_comment(self, work_item_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

