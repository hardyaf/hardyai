from __future__ import annotations

import hmac
import os
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}")
_WORKSPACE = re.compile(r"owners/[0-9a-f]{20}/tasks/[A-Za-z0-9-]{1,80}")


class RunRequest(BaseModel):
    task_id: str
    run_id: str
    workspace_ref: str
    broker_token: str = Field(min_length=32, max_length=128)
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)


def _secret() -> str:
    path = Path(os.environ.get("TASK_RUNNER_KEY_FILE", "/run/secrets/task_runner_key"))
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _docker_client() -> httpx.Client:
    socket_path = os.environ.get("DOCKER_SOCKET_PATH", "/var/run/docker.sock")
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=socket_path),
        base_url="http://docker",
        timeout=httpx.Timeout(310.0, connect=5.0),
        trust_env=False,
    )


def _container_name(task_id: str, run_id: str) -> str:
    safe_task = re.sub(r"[^a-zA-Z0-9_.-]", "-", task_id)[:36]
    safe_run = re.sub(r"[^a-zA-Z0-9_.-]", "-", run_id)[:36]
    return f"jarvis-task-{safe_task}-{safe_run}"


def _decode_docker_logs(content: bytes) -> tuple[str, str]:
    stdout = bytearray()
    stderr = bytearray()
    offset = 0
    while offset + 8 <= len(content):
        stream = content[offset]
        size = int.from_bytes(content[offset + 4 : offset + 8], "big")
        offset += 8
        if size < 0 or offset + size > len(content):
            break
        target = stderr if stream == 2 else stdout
        target.extend(content[offset : offset + size])
        offset += size
    if offset == 0 and content:
        stdout.extend(content)
    return (
        bytes(stdout[:131_072]).decode("utf-8", errors="replace"),
        bytes(stderr[:131_072]).decode("utf-8", errors="replace"),
    )


app = FastAPI(title="HardyAI trusted task runner launcher", docs_url=None, redoc_url=None)


@app.get("/health")
def health() -> dict[str, str]:
    if not _secret():
        raise HTTPException(status_code=503, detail="task_runner_key_unavailable")
    return {"status": "ok"}


@app.post("/runs")
def run_program(
    request: RunRequest,
    x_task_runner_key: str | None = Header(default=None),
) -> dict[str, Any]:
    configured = _secret()
    if not configured or not hmac.compare_digest(configured, str(x_task_runner_key or "")):
        raise HTTPException(status_code=401, detail="task_runner_auth_failed")
    if _IDENTIFIER.fullmatch(request.task_id) is None or _IDENTIFIER.fullmatch(request.run_id) is None:
        raise HTTPException(status_code=400, detail="task_runner_identifier_invalid")
    if _WORKSPACE.fullmatch(request.workspace_ref) is None:
        raise HTTPException(status_code=400, detail="task_runner_workspace_invalid")

    host_root = Path(os.environ.get("TASK_WORKSPACE_HOST_ROOT", "")).resolve()
    if not host_root.is_absolute() or str(host_root) == "/":
        raise HTTPException(status_code=503, detail="task_runner_host_root_invalid")
    task_root = (host_root / request.workspace_ref).resolve()
    if host_root not in task_root.parents:
        raise HTTPException(status_code=400, detail="task_runner_workspace_escape")
    run_root = (task_root / "runs" / request.run_id).resolve()
    if task_root not in run_root.parents:
        raise HTTPException(status_code=400, detail="task_runner_run_escape")
    required = [run_root / "input", run_root / "work", run_root / "ipc", task_root / "published"]
    if (
        any(not item.is_dir() or item.is_symlink() for item in required)
        or not (required[0] / "source.py").is_file()
        or (required[0] / "source.py").is_symlink()
    ):
        raise HTTPException(status_code=409, detail="task_runner_workspace_unprepared")

    image = os.environ.get("TASK_RUNNER_IMAGE", "").strip()
    if not image:
        raise HTTPException(status_code=503, detail="task_runner_image_unconfigured")
    name = _container_name(request.task_id, request.run_id)
    container_id = ""
    timed_out = False
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    create_body = {
        "Image": image,
        "Cmd": ["python", "/workspace/input/source.py"],
        "User": "65532:65532",
        "WorkingDir": "/workspace/work",
        "Env": [
            "PYTHONUNBUFFERED=1",
            "PYTHONDONTWRITEBYTECODE=1",
            f"JARVIS_TASK_BROKER_TOKEN={request.broker_token}",
            "JARVIS_TASK_BROKER_SOCKET=/workspace/ipc/broker.sock",
            "JARVIS_TASK_PUBLISHED=/workspace/published",
        ],
        "NetworkDisabled": True,
        "HostConfig": {
            "AutoRemove": False,
            "ReadonlyRootfs": True,
            "NetworkMode": "none",
            "Memory": 536_870_912,
            "MemorySwap": 536_870_912,
            "NanoCpus": 1_000_000_000,
            "PidsLimit": 64,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=67108864,mode=1777"},
            "Binds": [
                f"{required[0]}:/workspace/input:ro",
                f"{required[1]}:/workspace/work:rw",
                f"{required[2]}:/workspace/ipc:rw",
                f"{required[3]}:/workspace/published:rw",
            ],
        },
    }
    with _docker_client() as client:
        try:
            # A restarted attempt gets the immutable source/input snapshot and
            # a clean scratch directory. Published artifacts and durable effect
            # receipts remain separate and are never treated as scratch state.
            client.delete(f"/v1.44/containers/{quote(name)}?force=true&v=true")
            for child in required[1].iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            created = client.post(
                f"/v1.44/containers/create?name={quote(name)}", json=create_body
            )
            if created.status_code == 409:
                client.delete(f"/v1.44/containers/{quote(name)}?force=true")
                created = client.post(
                    f"/v1.44/containers/create?name={quote(name)}", json=create_body
                )
            created.raise_for_status()
            container_id = str(created.json().get("Id") or "")
            if not container_id:
                raise RuntimeError("task_runner_container_id_missing")
            started = client.post(f"/v1.44/containers/{container_id}/start")
            started.raise_for_status()
            try:
                waited = client.post(
                    f"/v1.44/containers/{container_id}/wait?condition=not-running",
                    timeout=request.timeout_seconds,
                )
                waited.raise_for_status()
                exit_code = int(waited.json().get("StatusCode") or 0)
            except httpx.TimeoutException:
                timed_out = True
                client.post(f"/v1.44/containers/{container_id}/kill?signal=KILL")
                waited = client.post(
                    f"/v1.44/containers/{container_id}/wait?condition=not-running",
                    timeout=10.0,
                )
                if waited.is_success:
                    exit_code = int(waited.json().get("StatusCode") or 137)
            logs = client.get(
                f"/v1.44/containers/{container_id}/logs?stdout=1&stderr=1",
                timeout=10.0,
            )
            if logs.is_success:
                stdout, log_stderr = _decode_docker_logs(logs.content[:262_144])
                stderr = log_stderr
            if timed_out:
                stderr = (stderr + "\nProgram exceeded its fixed execution timeout.").strip()
        finally:
            if container_id:
                try:
                    client.delete(
                        f"/v1.44/containers/{container_id}?force=true&v=true", timeout=10.0
                    )
                except httpx.HTTPError:
                    pass
    return {
        "status": "ok",
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout": stdout[-131_072:],
        "stderr": stderr[-131_072:],
    }
