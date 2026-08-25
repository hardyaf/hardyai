from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.integrations.discord_attachment.types import (
    DiscordAttachmentDescriptor,
    DiscordAttachmentReceipt,
)
from app.integrations.local_service import validate_local_http_service_url
from app.skills.domains.documents.ingestion import (
    DocumentValidationError,
    sanitize_filename,
    sanitize_title,
)


_MEDIA_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_ALLOWED_CDN_HOSTS = frozenset({"cdn.discordapp.com", "media.discordapp.net"})


class DiscordAttachmentIngressError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _validated_source_url(descriptor: DiscordAttachmentDescriptor) -> str:
    parsed = urlsplit(descriptor.source_url)
    if (
        parsed.scheme != "https"
        or str(parsed.hostname or "").casefold() not in _ALLOWED_CDN_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise DiscordAttachmentIngressError("discord_attachment_url_rejected")
    parts = [part for part in parsed.path.split("/") if part]
    if (
        len(parts) < 4
        or parts[0] != "attachments"
        or parts[1] != descriptor.channel_id
        or parts[2] != descriptor.attachment_id
    ):
        raise DiscordAttachmentIngressError("discord_attachment_url_identity_mismatch")
    return descriptor.source_url


def _validated_media(descriptor: DiscordAttachmentDescriptor) -> tuple[str, str, str]:
    try:
        filename = sanitize_filename(descriptor.filename)
        title = sanitize_title(descriptor.title, fallback=Path(filename).stem)
    except DocumentValidationError as exc:
        raise DiscordAttachmentIngressError(exc.code) from exc
    expected = _MEDIA_BY_EXTENSION.get(Path(filename).suffix.casefold())
    if expected is None:
        raise DiscordAttachmentIngressError("unsupported_extension")
    declared = descriptor.content_type
    if declared and declared not in {expected, "application/octet-stream"}:
        raise DiscordAttachmentIngressError("declared_type_mismatch")
    return filename, title, expected


def _external_id(descriptor: DiscordAttachmentDescriptor) -> str:
    material = ":".join(
        (
            "discord",
            descriptor.guild_id or "dm",
            descriptor.channel_id,
            descriptor.message_id,
            descriptor.attachment_id,
        )
    )
    return hashlib.sha256(material.encode("ascii")).hexdigest()


class _MultipartStream(httpx.AsyncByteStream):
    def __init__(
        self,
        *,
        source: AsyncIterator[bytes],
        expected_size: int,
        prefix: bytes,
        suffix: bytes,
    ) -> None:
        self._source = source
        self._expected_size = expected_size
        self.prefix = prefix
        self.suffix = suffix

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.prefix
        observed = 0
        async for chunk in self._source:
            observed += len(chunk)
            if observed > self._expected_size:
                raise DiscordAttachmentIngressError("discord_attachment_size_mismatch")
            if chunk:
                yield chunk
        if observed != self._expected_size:
            raise DiscordAttachmentIngressError("discord_attachment_size_mismatch")
        yield self.suffix


class DiscordAttachmentTransferService:
    """Streams allowlisted Discord CDN files directly into the no-egress gateway."""

    def __init__(
        self,
        *,
        gateway_base_url: str,
        operator_key: str,
        max_attachment_bytes: int,
        timeout_seconds: float,
        download_client: httpx.AsyncClient | None = None,
        gateway_client: httpx.AsyncClient | None = None,
        boundary_factory: Callable[[], str] | None = None,
    ) -> None:
        self.gateway_base_url = validate_local_http_service_url(
            gateway_base_url,
            label="Document Gateway URL",
        )
        key = str(operator_key or "").strip()
        if not key or len(key) > 512 or any(character.isspace() for character in key):
            raise RuntimeError("Discord attachment gateway key is invalid")
        self._gateway_headers = {
            "X-Jarvis-Operator-Key": key,
            "Accept": "application/json",
            "Host": "localhost",
            "User-Agent": "HardyAI-DiscordIngress/1",
        }
        self.max_attachment_bytes = max(1024, min(int(max_attachment_bytes), 104857600))
        timeout = httpx.Timeout(max(5.0, min(float(timeout_seconds), 600.0)))
        self._download_headers = {
            "Accept-Encoding": "identity",
            "User-Agent": "HardyAI-DiscordIngress/1",
        }
        self._download_client = download_client or httpx.AsyncClient(
            headers=self._download_headers,
            timeout=timeout,
            follow_redirects=False,
        )
        self._gateway_client = gateway_client or httpx.AsyncClient(
            base_url=self.gateway_base_url,
            headers=self._gateway_headers,
            timeout=timeout,
            follow_redirects=False,
        )
        self._owns_download_client = download_client is None
        self._owns_gateway_client = gateway_client is None
        self._boundary_factory = boundary_factory or (lambda: f"hardyai-{secrets.token_hex(16)}")

    @classmethod
    def from_settings(cls, settings) -> "DiscordAttachmentTransferService":
        key_path = Path(settings.document_gateway_operator_key_path).expanduser()
        if key_path.is_symlink():
            raise RuntimeError("Discord attachment gateway key path must not be a symlink")
        try:
            key = key_path.resolve().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("Discord attachment gateway key is unavailable") from exc
        return cls(
            gateway_base_url=settings.document_gateway_base_url,
            operator_key=key,
            max_attachment_bytes=settings.documents_max_upload_bytes,
            timeout_seconds=settings.discord_attachment_ingress_timeout_seconds,
        )

    async def submit(self, descriptor: DiscordAttachmentDescriptor) -> DiscordAttachmentReceipt:
        if descriptor.size_bytes > self.max_attachment_bytes:
            raise DiscordAttachmentIngressError("discord_attachment_too_large")
        source_url = _validated_source_url(descriptor)
        filename, title, media_type = _validated_media(descriptor)
        external_id = _external_id(descriptor)
        receipt_path = f"/documents/ingress-receipts/discord/{external_id}"
        try:
            existing = await self._gateway_client.get(receipt_path, headers=self._gateway_headers)
        except httpx.HTTPError as exc:
            raise DiscordAttachmentIngressError("document_gateway_receipt_check_failed") from exc
        if existing.status_code == 200:
            return self._receipt_response(filename, existing)
        if existing.status_code != 404:
            raise DiscordAttachmentIngressError("document_gateway_receipt_check_failed")

        try:
            async with self._download_client.stream(
                "GET",
                source_url,
                headers=self._download_headers,
            ) as source:
                if source.status_code != 200:
                    raise DiscordAttachmentIngressError("discord_attachment_download_failed")
                raw_length = str(source.headers.get("content-length") or "").strip()
                if not raw_length.isdigit() or int(raw_length) <= 0:
                    raise DiscordAttachmentIngressError("discord_attachment_size_unavailable")
                source_size = int(raw_length)
                if source_size > self.max_attachment_bytes:
                    raise DiscordAttachmentIngressError("discord_attachment_too_large")
                source_type = str(source.headers.get("content-type") or "").split(";", 1)[0].strip().casefold()
                if source_type and source_type not in {media_type, "application/octet-stream"}:
                    raise DiscordAttachmentIngressError("discord_attachment_cdn_type_mismatch")
                boundary = self._boundary_factory()
                prefix = self._multipart_prefix(
                    boundary=boundary,
                    filename=filename,
                    title=title,
                    media_type=media_type,
                )
                suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
                content = _MultipartStream(
                    source=source.aiter_raw(chunk_size=65536),
                    expected_size=source_size,
                    prefix=prefix,
                    suffix=suffix,
                )
                response = await self._gateway_client.post(
                    "/documents",
                    headers={
                        "Content-Type": f"multipart/form-data; boundary={boundary}",
                        "Content-Length": str(len(prefix) + source_size + len(suffix)),
                        "X-Jarvis-Ingress-Source": "discord",
                        "X-Jarvis-Ingress-External-Id": external_id,
                        **self._gateway_headers,
                    },
                    content=content,
                )
        except DiscordAttachmentIngressError:
            raise
        except httpx.HTTPError as exc:
            raise DiscordAttachmentIngressError("discord_attachment_transfer_failed") from exc
        if response.status_code not in {200, 202}:
            raise DiscordAttachmentIngressError("document_gateway_upload_failed")
        if len(response.content) > 16384:
            raise DiscordAttachmentIngressError("document_gateway_response_too_large")
        return self._receipt_response(filename, response)

    @staticmethod
    def _multipart_prefix(*, boundary: str, filename: str, title: str, media_type: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="title"\r\n\r\n'
            f"{title}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
            f"Content-Type: {media_type}\r\n\r\n"
        ).encode("utf-8")

    @staticmethod
    def _receipt(filename: str, value: object) -> DiscordAttachmentReceipt:
        if not isinstance(value, dict):
            raise DiscordAttachmentIngressError("document_gateway_response_invalid")
        try:
            return DiscordAttachmentReceipt(
                filename=filename,
                document_id=str(value["document_id"]),
                intake_id=str(value["intake_id"]),
                state=str(value["state"]),
                duplicate=bool(value.get("duplicate")),
                enqueue_confirmed=bool(value.get("enqueue_confirmed")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DiscordAttachmentIngressError("document_gateway_response_invalid") from exc

    @classmethod
    def _receipt_response(cls, filename: str, response: httpx.Response) -> DiscordAttachmentReceipt:
        if len(response.content) > 16384:
            raise DiscordAttachmentIngressError("document_gateway_response_too_large")
        try:
            value = response.json()
        except ValueError as exc:
            raise DiscordAttachmentIngressError("document_gateway_response_invalid") from exc
        return cls._receipt(filename, value)

    async def close(self) -> None:
        if self._owns_download_client:
            await self._download_client.aclose()
        if self._owns_gateway_client:
            await self._gateway_client.aclose()
