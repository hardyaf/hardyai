from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Any, Callable


class TaskCapabilityBroker:
    """One-run local broker that keeps credentials and policy outside generated code."""

    def __init__(
        self,
        *,
        socket_path: Path,
        token: str,
        handler: Callable[[dict[str, Any]], dict[str, Any]],
        max_calls: int = 200,
    ) -> None:
        self.socket_path = socket_path
        self._token = token
        self._handler = handler
        self._max_calls = max(1, min(int(max_calls), 1000))
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._server: socket.socket | None = None
        self._calls = 0

    def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.socket_path.parent, 0o777)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("task_broker_start_timeout")

    def close(self) -> None:
        self._stop.set()
        server = self._server
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> "TaskCapabilityBroker":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _serve(self) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server = server
        server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o666)
        server.listen(4)
        server.settimeout(0.25)
        self._ready.set()
        while not self._stop.is_set():
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(5.0)
                response = self._handle_connection(connection)
                encoded = json.dumps(
                    response, ensure_ascii=True, separators=(",", ":")
                ).encode("utf-8")
                try:
                    connection.sendall(encoded + b"\n")
                except OSError:
                    pass

    def _handle_connection(self, connection: socket.socket) -> dict[str, Any]:
        data = bytearray()
        while len(data) <= 1_048_576:
            chunk = connection.recv(65_536)
            if not chunk:
                break
            data.extend(chunk)
            if b"\n" in chunk:
                break
        if len(data) > 1_048_576:
            return {"status": "error", "error_code": "broker_request_too_large"}
        try:
            request = json.loads(bytes(data).split(b"\n", 1)[0].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"status": "error", "error_code": "broker_request_invalid"}
        if not isinstance(request, dict) or request.get("token") != self._token:
            return {"status": "policy_denied", "error_code": "broker_auth_failed"}
        self._calls += 1
        if self._calls > self._max_calls:
            return {"status": "error", "error_code": "broker_call_limit_exceeded"}
        try:
            return self._handler(request)
        except Exception as exc:
            return {
                "status": "error",
                "error_code": "broker_handler_failed",
                "error_type": type(exc).__name__,
            }
