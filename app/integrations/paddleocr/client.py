from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import BinaryIO

import httpx

from app.integrations.local_service import validate_local_http_service_url


class PaddleOCRClientError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = str(code)[:120]
        super().__init__(self.code)


def _http_error_code(response: httpx.Response) -> str:
    detail = ""
    try:
        value = response.json()
    except (ValueError, TypeError):
        value = None
    if isinstance(value, dict) and isinstance(value.get("detail"), str):
        candidate = str(value["detail"]).strip().casefold()
        if re.fullmatch(r"[a-z0-9_]{1,80}", candidate):
            detail = candidate
    suffix = f"_{detail}" if detail else ""
    return f"paddleocr_http_{int(response.status_code)}{suffix}"[:120]


class PaddleOCRClient:
    """Bounded client for the private, CPU-only OCR service."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key_path: str,
        server_version: str,
        timeout_seconds: float = 300.0,
        max_input_bytes: int = 50 * 1024 * 1024,
        max_response_bytes: int = 64 * 1024 * 1024,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = validate_local_http_service_url(base_url, label="PaddleOCR base URL")
        self.server_version = str(server_version or "").strip()
        self.max_input_bytes = max(1024, min(int(max_input_bytes), 100 * 1024 * 1024))
        self.max_response_bytes = max(1024, min(int(max_response_bytes), 256 * 1024 * 1024))
        key_path = Path(api_key_path).expanduser()
        if key_path.is_symlink():
            raise RuntimeError("PaddleOCR API key path must not be a symlink")
        try:
            api_key = key_path.resolve().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("PaddleOCR API key file is unavailable") from exc
        if not api_key or len(api_key) > 512 or any(character.isspace() for character in api_key):
            raise RuntimeError("PaddleOCR API key file is invalid")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(max(1.0, min(float(timeout_seconds), 900.0))),
            headers={
                "X-Api-Key": api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "HardyAI-DocumentWorker-OCR/1",
            },
            follow_redirects=False,
            transport=transport,
        )

    def infer(self, *, stream: BinaryIO, filename: str, media_type: str) -> dict:
        payload = stream.read(self.max_input_bytes + 1)
        if len(payload) > self.max_input_bytes:
            raise RuntimeError("paddleocr_input_too_large")
        response = self._client.post(
            "/ocr",
            json={
                "filename": str(filename)[:180],
                "media_type": str(media_type)[:100],
                "file_base64": base64.b64encode(payload).decode("ascii"),
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PaddleOCRClientError(_http_error_code(exc.response)) from exc
        if len(response.content) > self.max_response_bytes:
            raise RuntimeError("paddleocr_response_too_large")
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("paddleocr_response_invalid")
        return value

    def ready(self) -> bool:
        try:
            response = self._client.get("/ready")
            response.raise_for_status()
            value = response.json()
            observed = str(value.get("version") or "").strip() if isinstance(value, dict) else ""
            return bool(
                isinstance(value, dict)
                and value.get("status") == "ready"
                and (not self.server_version or observed == self.server_version)
            )
        except (httpx.HTTPError, RuntimeError, ValueError):
            return False

    def close(self) -> None:
        self._client.close()
