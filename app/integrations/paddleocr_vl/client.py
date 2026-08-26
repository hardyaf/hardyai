from __future__ import annotations

import base64
import re
from typing import BinaryIO

import httpx

from app.accelerator.client import accelerator_request_headers
from app.integrations.local_service import validate_local_http_service_url


class PaddleOCRVLClientError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = str(code)[:120]
        super().__init__(self.code)


def _error_code(response: httpx.Response) -> str:
    detail = ""
    try:
        value = response.json()
    except (TypeError, ValueError):
        value = None
    if isinstance(value, dict) and isinstance(value.get("detail"), str):
        candidate = str(value["detail"]).strip().casefold()
        if re.fullmatch(r"[a-z0-9_]{1,100}", candidate):
            detail = candidate
    return f"paddleocr_vl_http_{int(response.status_code)}{f'_{detail}' if detail else ''}"[:120]


class PaddleOCRVLClient:
    """Bounded low-priority client for the accelerator-admitted full VLM pipeline."""

    def __init__(
        self,
        *,
        base_url: str,
        framework_version: str,
        pipeline_version: str,
        timeout_seconds: float = 120.0,
        max_input_bytes: int = 50 * 1024 * 1024,
        max_response_bytes: int = 16 * 1024 * 1024,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = validate_local_http_service_url(base_url, label="PaddleOCR-VL admission URL")
        self.framework_version = str(framework_version or "").strip()
        self.pipeline_version = str(pipeline_version or "").strip()
        self.max_input_bytes = max(1024, min(int(max_input_bytes), 100 * 1024 * 1024))
        self.max_response_bytes = max(1024, min(int(max_response_bytes), 64 * 1024 * 1024))
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(max(5.0, min(float(timeout_seconds), 900.0))),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "HardyAI-DocumentWorker-VLM/1",
            },
            follow_redirects=False,
            transport=transport,
        )

    def infer(self, *, stream: BinaryIO, filename: str, media_type: str) -> dict:
        payload = stream.read(self.max_input_bytes + 1)
        if len(payload) > self.max_input_bytes:
            raise PaddleOCRVLClientError("paddleocr_vl_input_too_large")
        response = self._client.post(
            "/v1/document-vlm",
            headers=accelerator_request_headers("document_vlm"),
            json={
                "filename": str(filename)[:180],
                "media_type": str(media_type)[:100],
                "file_base64": base64.b64encode(payload).decode("ascii"),
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PaddleOCRVLClientError(_error_code(exc.response)) from exc
        if len(response.content) > self.max_response_bytes:
            raise PaddleOCRVLClientError("paddleocr_vl_response_too_large")
        value = response.json()
        if not isinstance(value, dict):
            raise PaddleOCRVLClientError("paddleocr_vl_response_invalid")
        return value

    def ready(self) -> bool:
        try:
            response = self._client.get(
                "/v1/document-vlm/ready",
                headers=accelerator_request_headers("document_vlm"),
            )
            response.raise_for_status()
            value = response.json()
            return bool(
                isinstance(value, dict)
                and value.get("status") == "ready"
                and (not self.framework_version or value.get("provider_version") == self.framework_version)
                and (not self.pipeline_version or value.get("pipeline_version") == self.pipeline_version)
            )
        except (httpx.HTTPError, RuntimeError, ValueError):
            return False

    def close(self) -> None:
        self._client.close()
