from __future__ import annotations

from typing import Any

import httpx

from app.accelerator.client import accelerator_request_headers


class NativeTaskModelClient:
    """Native Ollama chat adapter that preserves assistant/tool messages verbatim."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        num_ctx: int,
        num_predict: int,
        think: bool | str = "medium",
        keep_alive_seconds: float | None = None,
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._model = str(model or "").strip()
        self._timeout_seconds = max(5.0, float(timeout_seconds))
        self._num_ctx = max(512, int(num_ctx))
        self._num_predict = max(64, int(num_predict))
        self._think = think
        self._keep_alive_seconds = keep_alive_seconds

    @property
    def model(self) -> str:
        return self._model

    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "think": self._think,
            "options": {
                "num_ctx": self._num_ctx,
                "num_predict": self._num_predict,
            },
        }
        if self._keep_alive_seconds is not None:
            payload["keep_alive"] = f"{max(0.0, float(self._keep_alive_seconds)):g}s"
        with httpx.Client(timeout=self._timeout_seconds, trust_env=False) as client:
            response = client.post(
                f"{self._base_url}/api/chat",
                headers=accelerator_request_headers("main_conversation"),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        message = data.get("message")
        if not isinstance(message, dict) or str(message.get("role") or "") != "assistant":
            raise RuntimeError("task_model_assistant_message_invalid")
        content = message.get("content", "")
        if not isinstance(content, str):
            raise RuntimeError("task_model_assistant_content_invalid")
        tool_calls = message.get("tool_calls")
        if tool_calls is not None and not isinstance(tool_calls, list):
            raise RuntimeError("task_model_tool_calls_invalid")
        return {
            "message": message,
            "model": str(data.get("model") or self._model),
            "done_reason": str(data.get("done_reason") or ""),
            "prompt_eval_count": int(data.get("prompt_eval_count") or 0),
            "eval_count": int(data.get("eval_count") or 0),
            "total_duration": int(data.get("total_duration") or 0),
        }
