from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.accelerator.repository import AcceleratorLeaseRepository
from app.accelerator.service import (
    LANE_PRIORITIES,
    AcceleratorAdmissionQueue,
    AcceleratorLeaseGuard,
)
from app.accelerator.types import AcceleratorAdmissionError
from app.integrations.local_service import validate_local_http_service_url


def _integer_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


_DATABASE_PATH = os.getenv("ACCELERATOR_DATABASE_PATH", "data/accelerator/leases.db")
_KEY_PATH = Path(
    os.getenv("ACCELERATOR_ADMISSION_API_KEY_PATH", "/run/secrets/accelerator_admission_api_key")
)
_OLLAMA_URL = validate_local_http_service_url(
    os.getenv("OLLAMA_BACKEND_URL", "http://ollama:11434"),
    label="Ollama backend URL",
)
_VLM_URL = validate_local_http_service_url(
    os.getenv("PADDLEOCR_VL_BACKEND_URL", "http://paddleocr-vl-serve:8050"),
    label="PaddleOCR-VL backend URL",
)
_VLM_ENABLED = str(os.getenv("DOCUMENTS_PADDLEOCR_VL_ENABLED", "false")).strip().casefold() in {
    "1",
    "true",
    "yes",
    "on",
}
_ALLOWED_MODELS = frozenset(
    item.strip()
    for item in str(os.getenv("ACCELERATOR_OLLAMA_MODELS", "qwen2.5:7b,gpt-oss:20b")).split(",")
    if item.strip()
)
_EVICTABLE_MODELS = frozenset(
    item.strip()
    for item in str(os.getenv("ACCELERATOR_OLLAMA_EVICTABLE_MODELS", "qwen2.5:7b")).split(",")
    if item.strip()
)
_PROTECTED_MODELS = frozenset(
    item.strip()
    for item in str(os.getenv("ACCELERATOR_OLLAMA_PROTECTED_MODELS", "gpt-oss:20b")).split(",")
    if item.strip()
)
if (
    not _EVICTABLE_MODELS
    or not _PROTECTED_MODELS
    or not _EVICTABLE_MODELS.issubset(_ALLOWED_MODELS)
    or not _PROTECTED_MODELS.issubset(_ALLOWED_MODELS)
    or _EVICTABLE_MODELS & _PROTECTED_MODELS
):
    raise RuntimeError("accelerator_model_eviction_policy_invalid")
_MAX_REQUEST_BYTES = _integer_env("ACCELERATOR_MAX_REQUEST_BYTES", 2 * 1024 * 1024, 4096, 8 * 1024 * 1024)
_MAX_VLM_REQUEST_BYTES = _integer_env(
    "ACCELERATOR_MAX_VLM_REQUEST_BYTES",
    72 * 1024 * 1024,
    4096,
    144 * 1024 * 1024,
)
_MAX_RESPONSE_BYTES = _integer_env(
    "ACCELERATOR_MAX_RESPONSE_BYTES", 16 * 1024 * 1024, 4096, 64 * 1024 * 1024
)
_WAIT_SECONDS = _float_env("ACCELERATOR_WAIT_SECONDS", 120.0, 1.0, 900.0)
_UPSTREAM_SECONDS = _float_env("ACCELERATOR_UPSTREAM_TIMEOUT_SECONDS", 180.0, 5.0, 900.0)

_repository = AcceleratorLeaseRepository(_DATABASE_PATH)
_admission = AcceleratorAdmissionQueue(
    _repository,
    lease_seconds=_float_env("ACCELERATOR_LEASE_SECONDS", 30.0, 5.0, 900.0),
    heartbeat_seconds=_float_env("ACCELERATOR_HEARTBEAT_SECONDS", 5.0, 0.5, 60.0),
)
_client = httpx.AsyncClient(
    timeout=httpx.Timeout(_UPSTREAM_SECONDS),
    follow_redirects=False,
    trust_env=False,
)

app = FastAPI(
    title="HardyAI Accelerator Admission",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def _read_key() -> str:
    if _KEY_PATH.is_symlink():
        raise RuntimeError("accelerator_key_path_symlink")
    value = _KEY_PATH.resolve().read_text(encoding="utf-8").strip()
    if not value or len(value) > 512 or any(character.isspace() for character in value):
        raise RuntimeError("accelerator_key_invalid")
    return value


def _require_key(
    x_hardyai_accelerator_key: str | None = Header(default=None),
) -> None:
    try:
        configured = _read_key()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="accelerator_key_unavailable") from exc
    supplied = str(x_hardyai_accelerator_key or "").strip()
    if not supplied or not hmac.compare_digest(configured, supplied):
        raise HTTPException(status_code=401, detail="accelerator_auth_required")


def _lane(value: str | None) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized not in LANE_PRIORITIES:
        raise HTTPException(status_code=400, detail="accelerator_lane_not_allowed")
    return normalized


async def _json_body(request: Request, *, maximum: int = _MAX_REQUEST_BYTES) -> dict[str, Any]:
    raw_length = str(request.headers.get("content-length") or "").strip()
    if not raw_length.isdigit():
        raise HTTPException(status_code=411, detail="content_length_required")
    if int(raw_length) <= 0 or int(raw_length) > maximum:
        raise HTTPException(status_code=413, detail="accelerator_request_too_large")
    body = await request.body()
    if len(body) != int(raw_length) or len(body) > maximum:
        raise HTTPException(status_code=400, detail="accelerator_request_size_mismatch")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="accelerator_json_invalid") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="accelerator_json_object_required")
    return value


def _ollama_payload(value: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "model",
        "prompt",
        "stream",
        "format",
        "options",
        "keep_alive",
        "system",
        "template",
        "raw",
        "context",
        "suffix",
    }
    if set(value) - allowed_keys or "images" in value:
        raise HTTPException(status_code=400, detail="accelerator_ollama_fields_rejected")
    model = str(value.get("model") or "").strip()
    prompt = value.get("prompt")
    if model not in _ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail="accelerator_model_not_allowed")
    if not isinstance(prompt, str) or len(prompt) > 500_000:
        raise HTTPException(status_code=400, detail="accelerator_prompt_invalid")
    if value.get("stream") is not False:
        raise HTTPException(status_code=400, detail="accelerator_streaming_disabled")
    options = value.get("options")
    if options is not None and not isinstance(options, dict):
        raise HTTPException(status_code=400, detail="accelerator_options_invalid")
    return {key: item for key, item in value.items() if key in allowed_keys}


async def _bounded_upstream(
    *,
    method: str,
    url: str,
    json_payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, str]:
    async with _client.stream(method, url, json=json_payload, headers=headers) as response:
        chunks: list[bytes] = []
        observed = 0
        async for chunk in response.aiter_bytes():
            observed += len(chunk)
            if observed > _MAX_RESPONSE_BYTES:
                raise AcceleratorAdmissionError("accelerator_response_too_large")
            chunks.append(chunk)
        content_type = str(response.headers.get("content-type") or "application/json").split(";", 1)[0]
        return response.status_code, b"".join(chunks), content_type


async def _guarded_upstream(
    guard: AcceleratorLeaseGuard,
    *,
    method: str,
    url: str,
    json_payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, str]:
    upstream = asyncio.create_task(
        _bounded_upstream(
            method=method,
            url=url,
            json_payload=json_payload,
            headers=headers,
        )
    )
    lease_lost = asyncio.create_task(guard.lost.wait())
    done, _ = await asyncio.wait({upstream, lease_lost}, return_when=asyncio.FIRST_COMPLETED)
    try:
        if lease_lost in done and guard.lost.is_set():
            upstream.cancel()
            raise AcceleratorAdmissionError("accelerator_lease_lost")
        return await upstream
    finally:
        lease_lost.cancel()
        try:
            await lease_lost
        except BaseException:
            pass


def _safe_upstream_response(status: int, body: bytes, content_type: str) -> Response:
    if not 200 <= status < 300:
        return JSONResponse(
            status_code=502 if status >= 500 else status,
            content={"error": f"accelerator_upstream_http_{int(status)}"},
        )
    return Response(content=body, status_code=status, media_type=content_type)


async def _unload_ollama(guard: AcceleratorLeaseGuard) -> None:
    for model in sorted(_EVICTABLE_MODELS):
        status, _, _ = await _guarded_upstream(
            guard,
            method="POST",
            url=f"{_OLLAMA_URL}/api/generate",
            json_payload={"model": model, "prompt": "", "stream": False, "keep_alive": 0},
        )
        if status >= 500:
            raise AcceleratorAdmissionError("accelerator_ollama_unload_failed")


@app.get("/ready")
async def ready(_: None = Depends(_require_key)) -> dict[str, Any]:
    try:
        ollama = await _bounded_upstream(method="GET", url=f"{_OLLAMA_URL}/api/tags")
        vlm_ready = True
        if _VLM_ENABLED:
            key = _read_key()
            vlm = await _bounded_upstream(
                method="GET",
                url=f"{_VLM_URL}/ready",
                headers={"X-HardyAI-Accelerator-Key": key},
            )
            vlm_ready = 200 <= vlm[0] < 300
        status = "ready" if 200 <= ollama[0] < 300 and vlm_ready else "degraded"
    except Exception:
        status = "degraded"
        vlm_ready = False if _VLM_ENABLED else True
    snapshot = _repository.snapshot()
    return {
        "status": status,
        "resource": snapshot["resource_id"],
        "leased": bool(snapshot["lease_lane"]),
        "queue_depth": snapshot["queued"],
        "vlm_enabled": _VLM_ENABLED,
        "vlm_ready": vlm_ready,
        "evictable_model_count": len(_EVICTABLE_MODELS),
        "protected_model_count": len(_PROTECTED_MODELS),
    }


@app.get("/api/tags")
async def ollama_tags(
    _: None = Depends(_require_key),
) -> Response:
    status, body, content_type = await _bounded_upstream(method="GET", url=f"{_OLLAMA_URL}/api/tags")
    return _safe_upstream_response(status, body, content_type)


@app.get("/api/ps")
async def ollama_ps(
    _: None = Depends(_require_key),
) -> Response:
    status, body, content_type = await _bounded_upstream(method="GET", url=f"{_OLLAMA_URL}/api/ps")
    return _safe_upstream_response(status, body, content_type)


@app.post("/api/generate")
async def ollama_generate(
    request: Request,
    x_hardyai_accelerator_lane: str | None = Header(default=None),
    _: None = Depends(_require_key),
) -> Response:
    lane = _lane(x_hardyai_accelerator_lane)
    if lane == "document_vlm":
        raise HTTPException(status_code=400, detail="accelerator_lane_endpoint_mismatch")
    payload = _ollama_payload(await _json_body(request))
    try:
        async with _admission.lease(lane=lane, wait_seconds=_WAIT_SECONDS) as guard:
            status, body, content_type = await _guarded_upstream(
                guard,
                method="POST",
                url=f"{_OLLAMA_URL}/api/generate",
                json_payload=payload,
            )
    except AcceleratorAdmissionError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc
    return _safe_upstream_response(status, body, content_type)


@app.get("/v1/document-vlm/ready")
async def document_vlm_ready(_: None = Depends(_require_key)) -> Response:
    if not _VLM_ENABLED:
        raise HTTPException(status_code=503, detail="document_vlm_disabled")
    key = _read_key()
    status, body, content_type = await _bounded_upstream(
        method="GET",
        url=f"{_VLM_URL}/ready",
        headers={"X-HardyAI-Accelerator-Key": key},
    )
    return _safe_upstream_response(status, body, content_type)


@app.post("/v1/document-vlm")
async def document_vlm(
    request: Request,
    x_hardyai_accelerator_lane: str | None = Header(default=None),
    _: None = Depends(_require_key),
) -> Response:
    if not _VLM_ENABLED:
        raise HTTPException(status_code=503, detail="document_vlm_disabled")
    if _lane(x_hardyai_accelerator_lane) != "document_vlm":
        raise HTTPException(status_code=400, detail="accelerator_lane_endpoint_mismatch")
    payload = await _json_body(request, maximum=_MAX_VLM_REQUEST_BYTES)
    key = _read_key()
    try:
        async with _admission.lease(lane="document_vlm", wait_seconds=_WAIT_SECONDS) as guard:
            await _unload_ollama(guard)
            status, body, content_type = await _guarded_upstream(
                guard,
                method="POST",
                url=f"{_VLM_URL}/parse-image",
                json_payload=payload,
                headers={"X-HardyAI-Accelerator-Key": key},
            )
    except AcceleratorAdmissionError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc
    return _safe_upstream_response(status, body, content_type)


@app.on_event("shutdown")
async def shutdown() -> None:
    await _client.aclose()
    _repository.close()
