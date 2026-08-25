from __future__ import annotations

import asyncio
import hashlib
import json
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlsplit

from app.api.security_headers import SECURITY_HEADERS


class _BodyRejected(RuntimeError):
    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


async def _json_response(send, *, status_code: int, code: str) -> None:
    body = json.dumps({"detail": code}, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    headers.extend((name.lower().encode("ascii"), value.encode("ascii")) for name, value in SECURITY_HEADERS.items())
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


def _headers(scope: dict[str, Any]) -> dict[bytes, bytes]:
    return {bytes(name).lower(): bytes(value) for name, value in scope.get("headers", [])}


def _principal_bucket(headers: dict[bytes, bytes], scope: dict[str, Any]) -> str:
    operator_key = headers.get(b"x-jarvis-operator-key", b"")[:1024]
    if operator_key:
        material = b"key:" + operator_key
    else:
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get(b"cookie", b"").decode("latin-1"))
        except Exception:
            cookies = SimpleCookie()
        session = cookies.get("jarvis_operator_session")
        if session is not None:
            material = b"session:" + session.value.encode("utf-8")[:1024]
        else:
            client = scope.get("client") or ("unknown", 0)
            material = f"client:{client[0]}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class DocumentRequestGuard:
    """Top-level guard applied before Starlette creates or parses multipart objects."""

    def __init__(
        self,
        app,
        *,
        max_request_bytes: int,
        body_timeout_seconds: float,
        global_concurrency: int,
        per_principal_concurrency: int,
        app_env: str,
    ) -> None:
        self.app = app
        self.max_request_bytes = max(1, int(max_request_bytes))
        self.body_timeout_seconds = max(1.0, float(body_timeout_seconds))
        self.global_concurrency = max(1, int(global_concurrency))
        self.per_principal_concurrency = max(1, int(per_principal_concurrency))
        self.app_env = str(app_env).strip().casefold()
        self._lock = asyncio.Lock()
        self._active = 0
        self._principal_active: dict[str, int] = {}

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        raw_headers = [(bytes(name).lower(), bytes(value)) for name, value in scope.get("headers", [])]
        path = str(scope.get("path") or "")
        protected = path.startswith("/documents") or path.startswith("/operator/session")
        if not protected:
            await self.app(scope, receive, send)
            return
        if len([value for name, value in raw_headers if name == b"host"]) != 1:
            await _json_response(send, status_code=400, code="invalid_host_header")
            return
        if not self._transport_allowed(scope, headers):
            await _json_response(send, status_code=400, code="document_transport_requires_loopback_or_tls")
            return
        is_upload = path == "/documents" and str(scope.get("method") or "").upper() == "POST"
        if not is_upload:
            await self.app(scope, receive, send)
            return
        content_lengths = [value for name, value in raw_headers if name == b"content-length"]
        if len(content_lengths) > 1 or (content_lengths and b"," in content_lengths[0]):
            await _json_response(send, status_code=400, code="ambiguous_content_length")
            return
        if content_lengths and b"transfer-encoding" in headers:
            await _json_response(send, status_code=400, code="ambiguous_body_framing")
            return
        content_length = content_lengths[0] if content_lengths else None
        if content_length:
            if not content_length.isdigit():
                await _json_response(send, status_code=400, code="invalid_content_length")
                return
            try:
                declared = int(content_length)
            except ValueError:
                await _json_response(send, status_code=400, code="invalid_content_length")
                return
            if declared < 0 or declared > self.max_request_bytes:
                await _json_response(send, status_code=413, code="request_body_too_large")
                return
        bucket = _principal_bucket(headers, scope)
        concurrency_rejected = False
        async with self._lock:
            principal_count = self._principal_active.get(bucket, 0)
            if self._active >= self.global_concurrency or principal_count >= self.per_principal_concurrency:
                concurrency_rejected = True
            else:
                self._active += 1
                self._principal_active[bucket] = principal_count + 1
        if concurrency_rejected:
            await _json_response(send, status_code=429, code="document_upload_concurrency_exceeded")
            return
        started = asyncio.get_running_loop().time()
        observed = 0
        response_started = False

        async def guarded_receive():
            nonlocal observed
            remaining = self.body_timeout_seconds - (asyncio.get_running_loop().time() - started)
            if remaining <= 0:
                raise _BodyRejected(408, "request_body_timeout")
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except TimeoutError as exc:
                raise _BodyRejected(408, "request_body_timeout") from exc
            if message.get("type") == "http.request":
                observed += len(message.get("body") or b"")
                if observed > self.max_request_bytes:
                    raise _BodyRejected(413, "request_body_too_large")
            return message

        async def guarded_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, guarded_receive, guarded_send)
        except _BodyRejected as exc:
            if not response_started:
                await _json_response(send, status_code=exc.status_code, code=exc.code)
        finally:
            async with self._lock:
                self._active -= 1
                next_count = self._principal_active.get(bucket, 1) - 1
                if next_count <= 0:
                    self._principal_active.pop(bucket, None)
                else:
                    self._principal_active[bucket] = next_count

    def _transport_allowed(self, scope: dict[str, Any], headers: dict[bytes, bytes]) -> bool:
        if str(scope.get("scheme") or "").casefold() == "https":
            return True
        host = headers.get(b"host", b"").decode("latin-1").strip()
        hostname = urlsplit(f"//{host}").hostname or ""
        if hostname.casefold() in {"localhost", "127.0.0.1", "::1"}:
            return True
        return self.app_env == "test" and hostname.casefold() == "testserver"
