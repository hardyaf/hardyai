from __future__ import annotations

from pathlib import Path

import httpx

from app.integrations.local_service import validate_local_http_service_url


class DoclingClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key_path: str,
        server_version: str,
        timeout_seconds: float,
        max_response_bytes: int = 64 * 1024 * 1024,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = validate_local_http_service_url(base_url, label="Docling base URL")
        self.server_version = str(server_version or "").strip()
        self.max_response_bytes = max(1024, min(int(max_response_bytes), 256 * 1024 * 1024))
        configured_key_file = Path(api_key_path).expanduser()
        if configured_key_file.is_symlink():
            raise RuntimeError("Docling API key path must not be a symlink")
        try:
            api_key = configured_key_file.resolve().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("Docling API key file is unavailable") from exc
        if not api_key or len(api_key) > 512 or any(character.isspace() for character in api_key):
            raise RuntimeError("Docling API key file is invalid")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(max(1.0, min(float(timeout_seconds), 900.0))),
            headers={
                "X-Api-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "HardyAI-DocumentWorker/1",
            },
            follow_redirects=False,
            transport=transport,
        )

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        request = self._client.build_request(method, path, **kwargs)
        response = self._client.send(request, stream=True)
        try:
            response.raise_for_status()
            declared = response.headers.get("content-length")
            if declared:
                try:
                    declared_bytes = int(declared)
                except ValueError as exc:
                    raise RuntimeError("docling_response_length_invalid") from exc
                if declared_bytes < 0:
                    raise RuntimeError("docling_response_length_invalid")
                if declared_bytes > self.max_response_bytes:
                    raise RuntimeError("docling_response_too_large")
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > self.max_response_bytes:
                    raise RuntimeError("docling_response_too_large")
            return httpx.Response(
                status_code=response.status_code,
                headers=response.headers,
                content=bytes(content),
                request=request,
                extensions=response.extensions,
            )
        finally:
            response.close()

    @staticmethod
    def _version_from(value: object) -> str:
        if not isinstance(value, dict):
            return ""
        for key in ("docling-serve", "docling_serve", "version"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate.removeprefix("v")
        return ""

    def ready(self) -> bool:
        try:
            response = self.request("GET", "/version")
            observed = self._version_from(response.json())
            return bool(observed and (not self.server_version or observed == self.server_version))
        except (httpx.HTTPError, RuntimeError, ValueError):
            return False

    def close(self) -> None:
        self._client.close()
