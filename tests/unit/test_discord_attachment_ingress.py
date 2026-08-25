from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

import app.api.discord_attachment_app as attachment_app_module
import app.api.operator_auth as operator_auth
from app.api.discord_attachment_app import create_discord_attachment_app
from app.integrations.discord_attachment.client import DiscordAttachmentIngressClient
from app.integrations.discord_attachment.service import (
    DiscordAttachmentIngressError,
    DiscordAttachmentTransferService,
)
from app.integrations.discord_attachment.types import DiscordAttachmentDescriptor
from app.integrations.discord_attachment.types import DiscordAttachmentReceipt


PDF = b"%PDF-1.4\nDiscord attachment fixture\n%%EOF\n"


class _AsyncBytes(httpx.AsyncByteStream):
    def __init__(self, value: bytes) -> None:
        self.value = value

    async def __aiter__(self):
        midpoint = max(1, len(self.value) // 2)
        yield self.value[:midpoint]
        yield self.value[midpoint:]


def _descriptor(**updates) -> DiscordAttachmentDescriptor:
    values = {
        "guild_id": "100",
        "channel_id": "200",
        "user_id": "300",
        "message_id": "400",
        "attachment_id": "500",
        "filename": "receipt.pdf",
        "content_type": "application/pdf",
        "size_bytes": len(PDF),
        "source_url": "https://cdn.discordapp.com/attachments/200/500/receipt.pdf?ex=test",
        "title": "receipt",
    }
    values.update(updates)
    return DiscordAttachmentDescriptor(**values)


def test_transfer_streams_cdn_bytes_to_gateway_and_preflights_durable_receipt() -> None:
    observed_uploads: list[bytes] = []
    downloads = 0
    receipt_exists = False

    async def download_handler(request: httpx.Request) -> httpx.Response:
        nonlocal downloads
        downloads += 1
        assert request.url.host == "cdn.discordapp.com"
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            headers={"content-length": str(len(PDF)), "content-type": "application/pdf"},
            stream=_AsyncBytes(PDF),
            request=request,
        )

    async def gateway_handler(request: httpx.Request) -> httpx.Response:
        nonlocal receipt_exists
        assert request.headers["host"] == "localhost"
        if request.method == "GET":
            if not receipt_exists:
                return httpx.Response(404, json={"detail": "not_found"}, request=request)
            return httpx.Response(
                200,
                json={
                    "document_id": "doc-1",
                    "intake_id": "intake-1",
                    "state": "queued",
                    "duplicate": True,
                    "enqueue_confirmed": True,
                },
                request=request,
            )
        body = await request.aread()
        observed_uploads.append(body)
        assert request.headers["x-jarvis-ingress-source"] == "discord"
        assert len(request.headers["x-jarvis-ingress-external-id"]) == 64
        assert PDF in body
        assert b"cdn.discordapp.com" not in body
        receipt_exists = True
        return httpx.Response(
            202,
            json={
                "document_id": "doc-1",
                "intake_id": "intake-1",
                "state": "queued",
                "duplicate": False,
                "enqueue_confirmed": True,
            },
            request=request,
        )

    async def exercise() -> None:
        download_client = httpx.AsyncClient(transport=httpx.MockTransport(download_handler))
        gateway_client = httpx.AsyncClient(
            base_url="http://document-gateway:8010",
            transport=httpx.MockTransport(gateway_handler),
        )
        service = DiscordAttachmentTransferService(
            gateway_base_url="http://document-gateway:8010",
            operator_key="synthetic-key",
            max_attachment_bytes=1024,
            timeout_seconds=10,
            download_client=download_client,
            gateway_client=gateway_client,
            boundary_factory=lambda: "hardyai-test-boundary",
        )
        first = await service.submit(_descriptor())
        second = await service.submit(_descriptor())
        assert first.duplicate is False
        assert second.duplicate is True
        await download_client.aclose()
        await gateway_client.aclose()

    asyncio.run(exercise())
    assert downloads == 1
    assert len(observed_uploads) == 1


def test_transfer_uses_cdn_length_when_discord_image_metadata_size_differs() -> None:
    observed_content_length = ""

    async def download_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": str(len(PDF)), "content-type": "application/pdf"},
            stream=_AsyncBytes(PDF),
            request=request,
        )

    async def gateway_handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_content_length
        if request.method == "GET":
            return httpx.Response(404, json={"detail": "not_found"}, request=request)
        observed_content_length = request.headers["content-length"]
        body = await request.aread()
        assert PDF in body
        return httpx.Response(
            202,
            json={
                "document_id": "doc-1",
                "intake_id": "intake-1",
                "state": "queued",
                "duplicate": False,
                "enqueue_confirmed": True,
            },
            request=request,
        )

    async def exercise() -> None:
        download_client = httpx.AsyncClient(transport=httpx.MockTransport(download_handler))
        gateway_client = httpx.AsyncClient(
            base_url="http://document-gateway:8010",
            transport=httpx.MockTransport(gateway_handler),
        )
        service = DiscordAttachmentTransferService(
            gateway_base_url="http://document-gateway:8010",
            operator_key="synthetic-key",
            max_attachment_bytes=1024,
            timeout_seconds=10,
            download_client=download_client,
            gateway_client=gateway_client,
            boundary_factory=lambda: "hardyai-test-boundary",
        )
        receipt = await service.submit(_descriptor(size_bytes=len(PDF) + 100))
        assert receipt.enqueue_confirmed is True
        expected = len(
            service._multipart_prefix(
                boundary="hardyai-test-boundary",
                filename="receipt.pdf",
                title="receipt",
                media_type="application/pdf",
            )
        ) + len(PDF) + len(b"\r\n--hardyai-test-boundary--\r\n")
        assert observed_content_length == str(expected)
        await download_client.aclose()
        await gateway_client.aclose()

    asyncio.run(exercise())


def test_transfer_rejects_cdn_representation_over_hard_limit() -> None:
    async def download_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": "2048", "content-type": "application/pdf"},
            stream=_AsyncBytes(PDF),
            request=request,
        )

    async def gateway_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={"detail": "not_found"}, request=request)
        raise AssertionError("oversized CDN representation must not reach the gateway")

    async def exercise() -> None:
        download_client = httpx.AsyncClient(transport=httpx.MockTransport(download_handler))
        gateway_client = httpx.AsyncClient(
            base_url="http://document-gateway:8010",
            transport=httpx.MockTransport(gateway_handler),
        )
        service = DiscordAttachmentTransferService(
            gateway_base_url="http://document-gateway:8010",
            operator_key="synthetic-key",
            max_attachment_bytes=1024,
            timeout_seconds=10,
            download_client=download_client,
            gateway_client=gateway_client,
        )
        with pytest.raises(DiscordAttachmentIngressError, match="discord_attachment_too_large"):
            await service.submit(_descriptor())
        await download_client.aclose()
        await gateway_client.aclose()

    asyncio.run(exercise())


def test_transfer_rejects_non_discord_url_before_network() -> None:
    service = DiscordAttachmentTransferService(
        gateway_base_url="http://document-gateway:8010",
        operator_key="synthetic-key",
        max_attachment_bytes=1024,
        timeout_seconds=10,
    )

    async def exercise() -> None:
        with pytest.raises(DiscordAttachmentIngressError, match="discord_attachment_url_rejected"):
            await service.submit(_descriptor(source_url="https://example.com/private.pdf"))
        await service.close()

    asyncio.run(exercise())


def test_core_ingress_client_sends_metadata_only_with_operator_auth(tmp_path) -> None:
    key = tmp_path / "operator.key"
    key.write_text("synthetic-key", encoding="utf-8")
    observed: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            200,
            json={
                "filename": "receipt.pdf",
                "document_id": "doc-1",
                "intake_id": "intake-1",
                "state": "queued",
                "duplicate": False,
                "enqueue_confirmed": True,
            },
            request=request,
        )

    async def exercise() -> None:
        client = DiscordAttachmentIngressClient(
            base_url="http://discord-attachment-ingress:8020",
            operator_key_path=str(key),
            transport=httpx.MockTransport(handler),
        )
        receipt = await client.submit(_descriptor())
        assert receipt.document_id == "doc-1"
        await client.close()

    asyncio.run(exercise())
    assert observed[0].headers["host"] == "localhost"
    assert observed[0].headers["x-jarvis-operator-key"] == "synthetic-key"
    body = observed[0].content
    assert PDF not in body
    assert b"source_url" in body


def test_attachment_sidecar_requires_operator_auth_and_small_json(monkeypatch) -> None:
    class TransferStub:
        def __init__(self) -> None:
            self.submitted = []
            self.closed = False

        async def submit(self, descriptor):
            self.submitted.append(descriptor)
            return DiscordAttachmentReceipt(
                filename=descriptor.filename,
                document_id="doc-1",
                intake_id="intake-1",
                state="queued",
                duplicate=False,
                enqueue_confirmed=True,
            )

        async def close(self):
            self.closed = True

    monkeypatch.setattr(
        operator_auth,
        "settings",
        type(
            "Settings",
            (),
            {"operator_api_key": "secret", "app_env": "test", "operator_session_ttl_seconds": 3600},
        )(),
    )
    monkeypatch.setattr(
        attachment_app_module,
        "settings",
        type("Settings", (), {"discord_attachment_ingress_enabled": True, "offline_mode": False})(),
    )
    service = TransferStub()
    with TestClient(create_discord_attachment_app(service)) as client:
        unauthorized = client.post("/discord-attachments", json=_descriptor().model_dump())
        assert unauthorized.status_code == 401
        accepted = client.post(
            "/discord-attachments",
            headers={"x-jarvis-operator-key": "secret"},
            json=_descriptor().model_dump(),
        )
        assert accepted.status_code == 200
        assert accepted.json()["filename"] == "receipt.pdf"
        assert accepted.headers["cache-control"] == "no-store"
        assert accepted.headers["x-content-type-options"] == "nosniff"
        oversized = client.post(
            "/discord-attachments",
            headers={
                "x-jarvis-operator-key": "secret",
                "content-type": "application/json",
                "content-length": "9000",
            },
            content=b"{}",
        )
        assert oversized.status_code == 413
        assert oversized.headers["cache-control"] == "no-store"
    assert len(service.submitted) == 1
    assert service.closed is True
