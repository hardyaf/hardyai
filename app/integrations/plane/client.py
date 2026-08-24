from __future__ import annotations

from typing import Any

import httpx


class PlaneClient:
    """Small self-hosted Plane REST client.

    Jarvis only uses supported HTTP APIs and supplies an external ID on create so
    a retry can reconcile an ambiguous response without creating a second card.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        workspace_slug: str,
        project_id: str,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        required = {
            "PLANE_API_BASE_URL": base_url,
            "PLANE_API_KEY": api_key,
            "PLANE_WORKSPACE_SLUG": workspace_slug,
            "PLANE_PROJECT_ID": project_id,
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise ValueError(f"Missing Plane configuration: {', '.join(missing)}")
        self._base_url = base_url.rstrip("/")
        self._workspace_slug = workspace_slug.strip()
        self._project_id = project_id.strip()
        self._client = httpx.Client(
            headers={"X-API-Key": api_key.strip(), "Accept": "application/json"},
            timeout=max(1.0, float(timeout_seconds)),
            transport=transport,
        )

    @property
    def project_path(self) -> str:
        return (
            f"/api/v1/workspaces/{self._workspace_slug}/projects/"
            f"{self._project_id}"
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._client.request(method, f"{self._base_url}{path}", **kwargs)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    def list_states(self) -> list[dict[str, Any]]:
        payload = self._request("GET", f"{self.project_path}/states/")
        if isinstance(payload, list):
            return [dict(item) for item in payload if isinstance(item, dict)]
        results = payload.get("results") if isinstance(payload, dict) else None
        return [dict(item) for item in results or [] if isinstance(item, dict)]

    def _list_work_items(
        self,
        *,
        cursor: str | None = None,
        external_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"per_page": 100}
        if cursor:
            params["cursor"] = cursor
        if external_id:
            params["external_id"] = external_id
            params["external_source"] = "jarvis"
        payload = self._request("GET", f"{self.project_path}/work-items/", params=params)
        if isinstance(payload, list):
            return {"results": payload}
        return dict(payload) if isinstance(payload, dict) else {"results": []}

    def find_work_item_by_external_id(self, external_id: str) -> dict[str, Any] | None:
        cursor: str | None = None
        # A hard page cap keeps a misbehaving board from becoming an open loop.
        for _ in range(5):
            page = self._list_work_items(cursor=cursor, external_id=external_id)
            for item in page.get("results") or []:
                if isinstance(item, dict) and str(item.get("external_id") or "") == external_id:
                    return dict(item)
            next_cursor = page.get("next_cursor")
            if not next_cursor or str(next_cursor) == str(cursor):
                break
            cursor = str(next_cursor)
        return None

    def create_work_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request("POST", f"{self.project_path}/work-items/", json=payload)
        return dict(result) if isinstance(result, dict) else {}

    def update_work_item(self, work_item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request(
            "PATCH",
            f"{self.project_path}/work-items/{work_item_id}/",
            json=payload,
        )
        return dict(result) if isinstance(result, dict) else {}

    def create_comment(self, work_item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request(
            "POST",
            f"{self.project_path}/work-items/{work_item_id}/comments/",
            json=payload,
        )
        return dict(result) if isinstance(result, dict) else {}
