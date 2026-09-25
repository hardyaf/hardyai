"""Small capability client available inside the unprivileged task runner."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any


def _request(payload: dict[str, Any]) -> dict[str, Any]:
    payload["token"] = os.environ["JARVIS_TASK_BROKER_TOKEN"]
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 1_048_576:
        raise ValueError("task broker request is too large")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(30.0)
    try:
        client.connect(os.environ["JARVIS_TASK_BROKER_SOCKET"])
        client.sendall(encoded + b"\n")
        chunks = bytearray()
        while len(chunks) <= 1_048_576:
            chunk = client.recv(65_536)
            if not chunk:
                break
            chunks.extend(chunk)
            if b"\n" in chunk:
                break
    finally:
        client.close()
    result = json.loads(bytes(chunks).split(b"\n", 1)[0].decode("utf-8"))
    if not isinstance(result, dict):
        raise RuntimeError("task broker response was invalid")
    return result


def describe(tool_id: str) -> dict[str, Any]:
    return _request({"action": "describe", "tool_id": tool_id})


def call(
    tool_id: str,
    arguments: dict[str, Any],
    *,
    contract_version: int = 1,
    logical_operation_id: str | None = None,
) -> dict[str, Any]:
    return _request(
        {
            "action": "call",
            "tool_id": tool_id,
            "contract_version": contract_version,
            "arguments": arguments,
            "logical_operation_id": logical_operation_id,
        }
    )


def published_path(name: str) -> Path:
    if not name or name != Path(name).name or name in {".", ".."}:
        raise ValueError("artifact name must be a single safe filename")
    root = Path(os.environ["JARVIS_TASK_PUBLISHED"]).resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise ValueError("artifact path escapes the published directory")
    return path
