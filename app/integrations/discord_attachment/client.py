from __future__ import annotations

from pathlib import Path

import httpx

from app.integrations.discord_attachment.types import (
    DiscordAttachmentDescriptor,
    DiscordAttachmentReceipt,
)
from app.integrations.local_service import validate_local_http_service_url


class DiscordAttachmentIngressClient:
    """Metadata-only Core client; attachment bytes never enter the Core process."""

    def __init__(
        self,
        *,
        base_url: str,
        operator_key_path: str,
        timeout_seconds: float = 180.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized_url = validate_local_http_service_url(base_url, label="Discord attachment ingress URL")
        key_path = Path(operator_key_path).expanduser()
        if key_path.is_symlink():
            raise RuntimeError("Discord attachment ingress key path must not be a symlink")
        try:
            key = key_path.resolve().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("Discord attachment ingress key is unavailable") from exc
        if not key or len(key) > 512 or any(character.isspace() for character in key):
            raise RuntimeError("Discord attachment ingress key is invalid")
        self._client = httpx.AsyncClient(
            base_url=normalized_url,
            headers={
                "X-Jarvis-Operator-Key": key,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Host": "localhost",
                "User-Agent": "HardyAI-Core-DiscordAttachment/1",
            },
            timeout=httpx.Timeout(max(5.0, min(float(timeout_seconds), 600.0))),
            follow_redirects=False,
            transport=transport,
        )

    async def submit(self, descriptor: DiscordAttachmentDescriptor) -> DiscordAttachmentReceipt:
        response = await self._client.post(
            "/discord-attachments",
            content=descriptor.model_dump_json().encode("utf-8"),
        )
        response.raise_for_status()
        if len(response.content) > 16384:
            raise RuntimeError("discord_attachment_ingress_response_too_large")
        return DiscordAttachmentReceipt.model_validate(response.json())

    async def close(self) -> None:
        await self._client.aclose()

