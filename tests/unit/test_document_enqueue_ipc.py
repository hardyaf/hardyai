from __future__ import annotations

import json
import socket
from uuid import uuid4

import pytest

from app.jobs.enqueue_ipc import (
    DocumentEnqueueSocketServer,
    UnixDocumentEnqueueClient,
    _AF_UNIX,
    _read_message,
)


class RecordingEnqueuer:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def enqueue_document(self, *, document_id: str, intake_id: str, sha256: str) -> str:
        self.calls.append(
            {"document_id": document_id, "intake_id": intake_id, "sha256": sha256}
        )
        return str(uuid4())


@pytest.mark.skipif(_AF_UNIX is None, reason="Unix sockets unavailable")
def test_document_enqueue_ipc_round_trip_and_exact_schema(tmp_path) -> None:
    socket_path = tmp_path / "enqueue.sock"
    enqueuer = RecordingEnqueuer()
    server = DocumentEnqueueSocketServer(str(socket_path), enqueuer)
    server.start()
    try:
        document_id = str(uuid4())
        intake_id = str(uuid4())
        client = UnixDocumentEnqueueClient(str(socket_path))
        assert client.enqueue_document(
            document_id=document_id,
            intake_id=intake_id,
            sha256="a" * 64,
        )
        assert enqueuer.calls == [
            {"document_id": document_id, "intake_id": intake_id, "sha256": "a" * 64}
        ]

        request = {
            "version": 1,
            "operation": "enqueue_document_archive",
            "document_id": str(uuid4()),
            "intake_id": str(uuid4()),
            "sha256": "b" * 64,
            "title": "must-not-cross-boundary",
        }
        with socket.socket(_AF_UNIX, socket.SOCK_STREAM) as raw:
            raw.connect(str(socket_path))
            raw.sendall(json.dumps(request).encode("ascii") + b"\n")
            raw.shutdown(socket.SHUT_WR)
            response = json.loads(_read_message(raw))
        assert response == {"error": "ValueError", "ok": False}
        assert len(enqueuer.calls) == 1
    finally:
        server.close()
