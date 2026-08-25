from __future__ import annotations

import asyncio

from fastapi import Request
from fastapi.responses import JSONResponse

from app.api.paddleocr_app import _bounded_request, _image_format_allowed


def _request(method: str, path: str, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": headers or [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8030),
        }
    )


def test_ready_bypasses_ocr_body_length_requirement() -> None:
    async def call_next(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ready"})

    response = asyncio.run(_bounded_request(_request("GET", "/ready"), call_next))

    assert response.status_code == 200


def test_ocr_requires_content_length() -> None:
    async def call_next(_request: Request) -> JSONResponse:
        raise AssertionError("missing content length must be rejected before the endpoint")

    response = asyncio.run(_bounded_request(_request("POST", "/ocr"), call_next))

    assert response.status_code == 411


def test_jpeg_policy_accepts_pillow_jpeg_family_only() -> None:
    assert _image_format_allowed(media_type="image/jpeg", observed="jpeg") is True
    assert _image_format_allowed(media_type="image/jpeg", observed="mpo") is True
    assert _image_format_allowed(media_type="image/jpeg", observed="png") is False


def test_png_policy_does_not_accept_jpeg_family() -> None:
    assert _image_format_allowed(media_type="image/png", observed="png") is True
    assert _image_format_allowed(media_type="image/png", observed="jpeg") is False
    assert _image_format_allowed(media_type="image/png", observed="mpo") is False
