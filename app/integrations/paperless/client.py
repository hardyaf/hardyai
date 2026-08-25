from __future__ import annotations

from pathlib import Path

import httpx

from app.integrations.local_service import validate_local_http_service_url


def validate_local_service_url(value: str) -> str:
    return validate_local_http_service_url(value, label="Paperless base URL")


class PaperlessClient:
    def __init__(
        self,
        *,
        base_url: str,
        token_path: str,
        api_version: int,
        server_version: str = "3.0.5",
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = validate_local_service_url(base_url)
        self.api_version = max(1, int(api_version))
        self.server_version = str(server_version or "").strip()
        configured_token_file = Path(token_path).expanduser()
        if configured_token_file.is_symlink():
            raise RuntimeError("Paperless token path must not be a symlink")
        token_file = configured_token_file.resolve()
        try:
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("Paperless token file is unavailable") from exc
        if not token or len(token) > 512 or any(character.isspace() for character in token):
            raise RuntimeError("Paperless token file is invalid")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(max(1.0, min(float(timeout_seconds), 300.0))),
            headers={
                "Authorization": f"Token {token}",
                "Accept": f"application/json; version={self.api_version}",
                "User-Agent": "HardyAI-DocumentGateway/1",
            },
            follow_redirects=False,
            transport=transport,
        )

    def request(self, method: str, path: str, **kwargs):
        response = self._client.request(method, path, **kwargs)
        response.raise_for_status()
        self.validate_response(response)
        return response

    def stream(self, method: str, path: str, **kwargs):
        return self._client.stream(method, path, **kwargs)

    def validate_response(self, response: httpx.Response) -> None:
        api_version = str(response.headers.get("x-api-version") or "").strip()
        server_version = str(response.headers.get("x-version") or "").strip()
        if api_version != str(self.api_version):
            raise RuntimeError("paperless_api_version_mismatch")
        if not server_version or (self.server_version and server_version != self.server_version):
            raise RuntimeError("paperless_server_version_mismatch")

    def ready(self) -> bool:
        try:
            self.request("GET", "/api/documents/", params={"page_size": 1})
            return True
        except (httpx.HTTPError, RuntimeError):
            return False

    def close(self) -> None:
        self._client.close()
