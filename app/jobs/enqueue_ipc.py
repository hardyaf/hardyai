from __future__ import annotations

import json
import os
import re
import socket
import socketserver
import stat
import struct
from pathlib import Path
from threading import Thread
from typing import Any
from uuid import UUID

from app.skills.domains.documents.ports import DurableDocumentEnqueuePort


_MAX_MESSAGE_BYTES = 4096
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_AF_UNIX = getattr(socket, "AF_UNIX", None)


def _validated_uuid(value: Any) -> str:
    parsed = UUID(str(value or ""))
    return str(parsed)


def _validated_sha256(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ValueError("invalid sha256")
    return normalized


class UnixDocumentEnqueueClient:
    def __init__(self, socket_path: str, *, timeout_seconds: float = 3.0) -> None:
        self.socket_path = str(socket_path)
        self.timeout_seconds = max(0.1, min(float(timeout_seconds), 10.0))

    def enqueue_document(self, *, document_id: str, intake_id: str, sha256: str) -> str:
        return self._request(
            {
                "version": 1,
                "operation": "enqueue_document_archive",
                "document_id": _validated_uuid(document_id),
                "intake_id": _validated_uuid(intake_id),
                "sha256": _validated_sha256(sha256),
            }
        )

    def enqueue_processing(
        self,
        *,
        document_id: str,
        source_version_id: str,
        run_id: str,
    ) -> str:
        return self._request(
            {
                "version": 1,
                "operation": "enqueue_document_process",
                "document_id": _validated_uuid(document_id),
                "source_version_id": _validated_uuid(source_version_id),
                "run_id": _validated_uuid(run_id),
            }
        )

    def _request(self, request: dict[str, Any]) -> str:
        if _AF_UNIX is None:
            raise RuntimeError("Unix sockets are not available on this platform")
        socket_path = Path(self.socket_path)
        socket_stat = socket_path.stat()
        if not stat.S_ISSOCK(socket_stat.st_mode):
            raise RuntimeError("document enqueue path is not a socket")
        if os.name == "posix" and (
            socket_stat.st_uid != os.getuid() or stat.S_IMODE(socket_stat.st_mode) & 0o077
        ):
            raise RuntimeError("document enqueue socket ownership or mode is unsafe")
        encoded = json.dumps(request, separators=(",", ":"), sort_keys=True).encode("ascii") + b"\n"
        with socket.socket(_AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(self.timeout_seconds)
            client.connect(self.socket_path)
            client.sendall(encoded)
            client.shutdown(socket.SHUT_WR)
            response = _read_message(client)
        value = json.loads(response.decode("utf-8"))
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise RuntimeError(str(value.get("error") if isinstance(value, dict) else "enqueue_failed"))
        return _validated_uuid(value.get("job_id"))


def _read_message(stream: socket.socket) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.recv(min(1024, _MAX_MESSAGE_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > _MAX_MESSAGE_BYTES:
            raise ValueError("message_too_large")
        if b"\n" in chunk:
            break
    message = b"".join(chunks)
    if b"\n" in message:
        message = message.split(b"\n", 1)[0]
    if not message:
        raise ValueError("empty_message")
    return message


class _UnixStreamServer(socketserver.TCPServer):
    address_family = _AF_UNIX or socket.AF_INET


class _ThreadedUnixServer(socketserver.ThreadingMixIn, _UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False


class DocumentEnqueueSocketServer:
    """Fixed-schema local IPC server; filesystem permissions are the authorization boundary."""

    def __init__(self, socket_path: str, enqueuer: DurableDocumentEnqueuePort) -> None:
        self.socket_path = Path(socket_path)
        self.enqueuer = enqueuer
        self._server: _ThreadedUnixServer | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        if _AF_UNIX is None:
            raise RuntimeError("Unix sockets are not available on this platform")
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.socket_path.parent, 0o700)
        if os.name == "posix" and self.socket_path.parent.stat().st_uid != os.getuid():
            raise RuntimeError("document enqueue directory owner is unsafe")
        if self.socket_path.exists():
            if not self.socket_path.is_socket():
                raise RuntimeError("document enqueue socket path is occupied by a non-socket")
            if os.name == "posix" and self.socket_path.stat().st_uid != os.getuid():
                raise RuntimeError("refusing to replace a socket owned by another user")
            self.socket_path.unlink()
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                try:
                    if hasattr(socket, "SO_PEERCRED"):
                        credentials = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                        _, peer_uid, _ = struct.unpack("3i", credentials)
                        if peer_uid != os.getuid():
                            raise PermissionError("document enqueue peer owner is not authorized")
                    request = json.loads(_read_message(self.request).decode("utf-8"))
                    if not isinstance(request, dict):
                        raise ValueError("invalid_request")
                    operation = request.get("operation")
                    if request.get("version") != 1:
                        raise ValueError("unsupported_request")
                    if operation == "enqueue_document_archive":
                        if set(request) != {
                            "version",
                            "operation",
                            "document_id",
                            "intake_id",
                            "sha256",
                        }:
                            raise ValueError("invalid_request_schema")
                        job_id = owner.enqueuer.enqueue_document(
                            document_id=_validated_uuid(request.get("document_id")),
                            intake_id=_validated_uuid(request.get("intake_id")),
                            sha256=_validated_sha256(request.get("sha256")),
                        )
                    elif operation == "enqueue_document_process":
                        if set(request) != {
                            "version",
                            "operation",
                            "document_id",
                            "source_version_id",
                            "run_id",
                        }:
                            raise ValueError("invalid_request_schema")
                        job_id = owner.enqueuer.enqueue_processing(
                            document_id=_validated_uuid(request.get("document_id")),
                            source_version_id=_validated_uuid(request.get("source_version_id")),
                            run_id=_validated_uuid(request.get("run_id")),
                        )
                    else:
                        raise ValueError("unsupported_request")
                    response = {"ok": True, "job_id": _validated_uuid(job_id)}
                except Exception as exc:
                    response = {"ok": False, "error": type(exc).__name__}
                self.request.sendall(
                    json.dumps(response, separators=(",", ":"), sort_keys=True).encode("ascii") + b"\n"
                )

        self._server = _ThreadedUnixServer(str(self.socket_path), Handler)
        os.chmod(self.socket_path, 0o600)
        self._thread = Thread(target=self._server.serve_forever, name="document-enqueue-ipc", daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        if self.socket_path.exists() and self.socket_path.is_socket():
            self.socket_path.unlink()
