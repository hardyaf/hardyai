from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.local_service import validate_local_http_service_url


class DocumentGatewayClient:
    """Content-bounded control/query client; it never downloads source binaries."""

    def __init__(
        self,
        *,
        base_url: str,
        operator_key_path: str,
        timeout_seconds: float = 15.0,
        max_response_bytes: int = 512 * 1024,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = validate_local_http_service_url(base_url, label="Document Gateway URL")
        key_path = Path(operator_key_path).expanduser()
        if key_path.is_symlink():
            raise RuntimeError("Document Gateway key path must not be a symlink")
        try:
            key = key_path.resolve().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("Document Gateway key is unavailable") from exc
        if not key or len(key) > 512 or any(character.isspace() for character in key):
            raise RuntimeError("Document Gateway key is invalid")
        self.max_response_bytes = max(1024, min(int(max_response_bytes), 2 * 1024 * 1024))
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "X-Jarvis-Operator-Key": key,
                "Accept": "application/json",
                "Host": "localhost",
                "User-Agent": "HardyAI-Core-DocumentQuery/1",
            },
            timeout=httpx.Timeout(max(1.0, min(float(timeout_seconds), 60.0))),
            follow_redirects=False,
            transport=transport,
        )

    def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._client.request(method, path, **kwargs)
        response.raise_for_status()
        if len(response.content) > self.max_response_bytes:
            raise RuntimeError("document_gateway_response_too_large")
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("document_gateway_response_invalid")
        return value

    def ready(self) -> bool:
        try:
            return self._json("GET", "/documents/ready").get("status") == "ready"
        except (httpx.HTTPError, RuntimeError, ValueError):
            return False

    def status(self, document_id: str) -> dict[str, Any]:
        return self._json("GET", f"/documents/{quote(document_id, safe='')}")

    def find(self, *, query: str, limit: int) -> dict[str, Any]:
        return self._json(
            "GET",
            "/documents/search",
            params={"query": str(query)[:200], "limit": max(1, min(int(limit), 20))},
        )

    def evidence(
        self,
        *,
        document_id: str,
        block_id: str | None = None,
        page_number: int | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 20))}
        if block_id:
            params["block_id"] = str(block_id)[:120]
        if page_number is not None:
            params["page_number"] = max(1, int(page_number))
        return self._json(
            "GET",
            f"/documents/{quote(document_id, safe='')}/evidence",
            params=params,
        )

    def reprocess(self, *, document_id: str, idempotency_key: str) -> dict[str, Any]:
        return self._json(
            "POST",
            f"/documents/{quote(document_id, safe='')}/reprocess",
            json={"idempotency_key": str(idempotency_key)[:120]},
        )

    def propose_metadata(
        self,
        *,
        document_id: str,
        field_name: str,
        proposed_value: str,
    ) -> dict[str, Any]:
        return self._json(
            "POST",
            f"/documents/{quote(document_id, safe='')}/metadata-proposals",
            json={"field_name": field_name, "proposed_value": proposed_value},
        )

    def bind_metadata_review(
        self,
        *,
        document_id: str,
        proposal_id: str,
        review_id: str,
    ) -> None:
        response = self._client.post(
            f"/documents/{quote(document_id, safe='')}/metadata-proposals/"
            f"{quote(proposal_id, safe='')}/review-binding",
            json={"review_id": review_id},
        )
        response.raise_for_status()
        if response.content:
            raise RuntimeError("document_gateway_binding_response_invalid")

    @staticmethod
    def source_path(document_id: str) -> str:
        return f"/documents/{quote(document_id, safe='')}/source"

    def close(self) -> None:
        self._client.close()
