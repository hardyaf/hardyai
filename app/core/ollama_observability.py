from __future__ import annotations

from typing import Any, Callable


OllamaMetricsCallback = Callable[[dict[str, Any]], None]


class OllamaCallObserver:
    """Keep non-sensitive Ollama request sizing and timing data inspectable."""

    def __init__(
        self,
        *,
        lane: str,
        model: str,
        num_ctx: int,
        num_predict: int,
        metrics_callback: OllamaMetricsCallback | None = None,
    ) -> None:
        self.lane = str(lane or "ollama").strip() or "ollama"
        self.model = str(model or "").strip()
        self.num_ctx = max(512, int(num_ctx))
        self.num_predict = max(1, int(num_predict))
        self._metrics_callback = metrics_callback
        self._last_call_metrics: dict[str, Any] | None = None

    def options(self, *, temperature: float) -> dict[str, Any]:
        return {
            "temperature": float(temperature),
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
        }

    def record(
        self,
        *,
        prompt: str,
        response_payload: dict[str, Any] | None = None,
        outcome: str,
        error_type: str | None = None,
    ) -> dict[str, Any]:
        payload = response_payload if isinstance(response_payload, dict) else {}
        prompt_chars = len(str(prompt or ""))
        estimated_prompt_tokens = (prompt_chars + 3) // 4
        prompt_eval_count = self._optional_int(payload.get("prompt_eval_count"))
        eval_count = self._optional_int(payload.get("eval_count"))
        metrics: dict[str, Any] = {
            "lane": self.lane,
            "model": self.model,
            "outcome": str(outcome or "unknown"),
            "requested_num_ctx": self.num_ctx,
            "requested_num_predict": self.num_predict,
            "prompt_chars": prompt_chars,
            "estimated_prompt_tokens": estimated_prompt_tokens,
            "estimated_input_exceeds_context": estimated_prompt_tokens > self.num_ctx,
            "prompt_eval_count": prompt_eval_count,
            "eval_count": eval_count,
            "context_utilization_ratio": (
                round(prompt_eval_count / self.num_ctx, 4)
                if prompt_eval_count is not None and self.num_ctx > 0
                else None
            ),
            "done_reason": str(payload.get("done_reason") or "").strip() or None,
            "total_duration_ms": self._nanoseconds_to_milliseconds(payload.get("total_duration")),
            "load_duration_ms": self._nanoseconds_to_milliseconds(payload.get("load_duration")),
            "prompt_eval_duration_ms": self._nanoseconds_to_milliseconds(
                payload.get("prompt_eval_duration")
            ),
            "eval_duration_ms": self._nanoseconds_to_milliseconds(payload.get("eval_duration")),
            "error_type": str(error_type or "").strip() or None,
        }
        self._last_call_metrics = metrics
        if self._metrics_callback is not None:
            try:
                self._metrics_callback(dict(metrics))
            except Exception:
                # Telemetry must never break the model response path.
                pass
        return dict(metrics)

    def status(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "model": self.model,
            "requested_num_ctx": self.num_ctx,
            "requested_num_predict": self.num_predict,
            "last_call_metrics": dict(self._last_call_metrics) if self._last_call_metrics else None,
        }

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value)
        return None

    @classmethod
    def _nanoseconds_to_milliseconds(cls, value: Any) -> float | None:
        numeric = cls._optional_int(value)
        if numeric is None:
            return None
        return round(numeric / 1_000_000.0, 3)
