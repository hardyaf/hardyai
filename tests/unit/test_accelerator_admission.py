from __future__ import annotations

import asyncio
import json

from fastapi import HTTPException
from starlette.requests import Request

from app.accelerator.repository import AcceleratorLeaseRepository
from app.accelerator.service import LANE_PRIORITIES


def test_durable_accelerator_queue_prioritizes_live_work_and_fences_old_owners(tmp_path) -> None:
    repository = AcceleratorLeaseRepository(str(tmp_path / "accelerator.db"))
    document_waiter = repository.enqueue(
        lane="document_vlm",
        priority=LANE_PRIORITIES["document_vlm"],
        wait_seconds=30,
    )
    main_waiter = repository.enqueue(
        lane="main_conversation",
        priority=LANE_PRIORITIES["main_conversation"],
        wait_seconds=30,
    )

    assert repository.try_acquire(waiter_id=document_waiter, lease_seconds=30) is None
    main_lease = repository.try_acquire(waiter_id=main_waiter, lease_seconds=30)
    assert main_lease is not None
    assert main_lease.lane == "main_conversation"
    assert repository.heartbeat(lease=main_lease, lease_seconds=30) is True
    assert repository.release(lease=main_lease) is True

    document_lease = repository.try_acquire(waiter_id=document_waiter, lease_seconds=30)
    assert document_lease is not None
    assert document_lease.fencing_token > main_lease.fencing_token
    assert repository.heartbeat(lease=main_lease, lease_seconds=30) is False
    assert repository.release(lease=main_lease) is False
    assert repository.release(lease=document_lease) is True
    assert repository.snapshot()["queued"] == 0
    repository.close()


def test_accelerator_client_fails_closed_when_admission_key_is_required(monkeypatch) -> None:
    from app.accelerator.client import accelerator_request_headers

    monkeypatch.setenv("ACCELERATOR_ADMISSION_REQUIRED", "true")
    monkeypatch.delenv("ACCELERATOR_ADMISSION_API_KEY_PATH", raising=False)

    try:
        accelerator_request_headers("micro")
    except RuntimeError as exc:
        assert str(exc) == "accelerator_admission_key_path_missing"
    else:
        raise AssertionError("required accelerator admission unexpectedly allowed a bypass")


def test_accelerator_client_reads_key_and_sets_typed_lane(tmp_path, monkeypatch) -> None:
    from app.accelerator.client import accelerator_request_headers

    key_path = tmp_path / "accelerator.key"
    key_path.write_text("bounded-test-key", encoding="utf-8")
    monkeypatch.setenv("ACCELERATOR_ADMISSION_REQUIRED", "true")
    monkeypatch.setenv("ACCELERATOR_ADMISSION_API_KEY_PATH", str(key_path))

    assert accelerator_request_headers("document_vlm") == {
        "X-HardyAI-Accelerator-Lane": "document_vlm",
        "X-HardyAI-Accelerator-Key": "bounded-test-key",
    }


def _typed_chat_payload(model: str) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "Choose one typed step."}],
        "stream": False,
        "options": {"num_ctx": 4096, "num_predict": 256, "temperature": 0.0},
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "submit_model_step",
                    "description": "Record one proposed step without executing it.",
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["mode"],
                        "properties": {"mode": {"type": "string"}},
                    },
                },
            }
        ],
        "think": False,
    }


def test_accelerator_chat_payload_allows_only_typed_step_wrapper() -> None:
    from app.api import accelerator_admission_app as admission_app

    model = sorted(admission_app._ALLOWED_MODELS)[0]
    payload = _typed_chat_payload(model)

    assert admission_app._ollama_chat_payload(payload) == payload

    payload["tools"][0]["function"]["name"] = "lists.add_items"
    try:
        admission_app._ollama_chat_payload(payload)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "accelerator_chat_tool_invalid"
    else:
        raise AssertionError("business tool unexpectedly crossed the chat admission boundary")


def test_accelerator_chat_route_uses_typed_lane_lease_and_chat_upstream(monkeypatch) -> None:
    from app.api import accelerator_admission_app as admission_app

    model = sorted(admission_app._ALLOWED_MODELS)[0]
    body = json.dumps(_typed_chat_payload(model)).encode("utf-8")
    lease_calls: list[tuple[str, float]] = []
    upstream_calls: list[dict] = []

    class _Lease:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class _Admission:
        def lease(self, *, lane: str, wait_seconds: float):
            lease_calls.append((lane, wait_seconds))
            return _Lease()

    async def _upstream(guard, **kwargs):
        del guard
        upstream_calls.append(kwargs)
        return 200, b'{"message":{"tool_calls":[]}}', "application/json"

    delivered = False

    async def _receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/chat",
            "headers": [(b"content-length", str(len(body)).encode("ascii"))],
        },
        receive=_receive,
    )
    monkeypatch.setattr(admission_app, "_admission", _Admission())
    monkeypatch.setattr(admission_app, "_guarded_upstream", _upstream)

    response = asyncio.run(
        admission_app.ollama_chat(
            request,
            x_hardyai_accelerator_lane="main_conversation",
            _=None,
        )
    )

    assert response.status_code == 200
    assert lease_calls == [("main_conversation", admission_app._WAIT_SECONDS)]
    assert upstream_calls[0]["method"] == "POST"
    assert upstream_calls[0]["url"].endswith("/api/chat")
    assert upstream_calls[0]["json_payload"]["tools"][0]["function"]["name"] == "submit_model_step"
