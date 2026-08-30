from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TypeAlias
from uuid import uuid4


OllamaMetricsCallback = Callable[[dict[str, Any]], None]
OllamaGenerateCallback = Callable[[dict[str, Any]], dict[str, Any]]
OllamaResponseValidator = Callable[[dict[str, Any]], bool]
OllamaThinkMode: TypeAlias = bool | str | None

OLLAMA_THINK_EFFORTS = frozenset({"low", "medium", "high", "max"})


def normalize_ollama_think_mode(
    value: Any,
    *,
    default: OllamaThinkMode = None,
) -> OllamaThinkMode:
    """Normalize Ollama's thinking control and reject unbounded provider values."""

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    if normalized in OLLAMA_THINK_EFFORTS:
        return normalized
    raise ValueError("ollama_think_mode_invalid")


def apply_ollama_think_mode(
    payload: dict[str, Any],
    think: OllamaThinkMode,
) -> dict[str, Any]:
    """Apply an optional top-level thinking control without retaining reasoning output."""

    normalized = normalize_ollama_think_mode(think)
    if normalized is not None:
        payload["think"] = normalized
    return payload


class AdaptiveTokenBudgetExhaustedError(RuntimeError):
    """Every bounded retry ended at the provider's output-token limit."""


@dataclass(frozen=True, slots=True)
class AdaptiveTokenBudgetPolicy:
    """Bounded output growth when a local model exhausts its response budget."""

    enabled: bool = True
    max_attempts: int = 4
    growth_factor: float = 2.0
    max_predict_multiplier: int = 8

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_attempts", max(1, min(int(self.max_attempts), 8)))
        object.__setattr__(self, "growth_factor", max(1.25, min(float(self.growth_factor), 4.0)))
        object.__setattr__(
            self,
            "max_predict_multiplier",
            max(1, min(int(self.max_predict_multiplier), 32)),
        )

    def next_budget(self, *, base: int, current: int) -> int | None:
        if not self.enabled:
            return None
        maximum = max(int(base), int(base) * self.max_predict_multiplier)
        if current >= maximum:
            return None
        grown = max(current + 1, int(round(current * self.growth_factor)))
        return min(grown, maximum)


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
        adaptive_policy: AdaptiveTokenBudgetPolicy | None = None,
    ) -> None:
        self.lane = str(lane or "ollama").strip() or "ollama"
        self.model = str(model or "").strip()
        self.num_ctx = max(512, int(num_ctx))
        self.num_predict = max(1, int(num_predict))
        self.adaptive_policy = adaptive_policy or AdaptiveTokenBudgetPolicy()
        self._metrics_callback = metrics_callback
        self._last_call_metrics: dict[str, Any] | None = None
        self._last_sequence_metrics: dict[str, Any] | None = None

    def options(self, *, temperature: float, num_predict: int | None = None) -> dict[str, Any]:
        return {
            "temperature": float(temperature),
            "num_ctx": self.num_ctx,
            "num_predict": max(1, int(num_predict or self.num_predict)),
        }

    def generate(
        self,
        *,
        prompt: str,
        temperature: float,
        invoke: OllamaGenerateCallback,
        is_valid_response: OllamaResponseValidator,
    ) -> dict[str, Any]:
        """Invoke Ollama and grow output budget only on demonstrated exhaustion."""

        budget = self.num_predict
        attempts = 0
        escalations: list[dict[str, Any]] = []
        call_id = str(uuid4())
        while True:
            attempts += 1
            try:
                payload = invoke(self.options(temperature=temperature, num_predict=budget))
            except Exception as exc:
                self.record(
                    prompt=prompt,
                    outcome="error",
                    error_type=type(exc).__name__,
                    requested_num_predict=budget,
                    attempt=attempts,
                    adaptive_retry=attempts > 1,
                    call_id=call_id,
                )
                self._last_sequence_metrics = {
                    "attempt_count": attempts,
                    "initial_num_predict": self.num_predict,
                    "final_num_predict": budget,
                    "escalations": list(escalations),
                    "outcome": "error",
                }
                raise

            try:
                valid = bool(is_valid_response(payload))
            except Exception:
                valid = False
            exhaustion_reason = self._exhaustion_reason(
                payload=payload,
                requested_num_predict=budget,
                valid=valid,
            )
            next_budget = None
            if attempts < self.adaptive_policy.max_attempts and exhaustion_reason is not None:
                next_budget = self.adaptive_policy.next_budget(base=self.num_predict, current=budget)
            will_retry = next_budget is not None
            failed_loop = exhaustion_reason is not None and next_budget is None
            self.record(
                prompt=prompt,
                response_payload=payload,
                outcome=(
                    "failed_loop"
                    if failed_loop
                    else ("success" if valid and not will_retry else "incomplete")
                ),
                requested_num_predict=budget,
                attempt=attempts,
                adaptive_retry=attempts > 1,
                escalation_reason=exhaustion_reason if will_retry else None,
                escalated_to_num_predict=next_budget,
                token_budget_exhausted=exhaustion_reason is not None,
                call_id=call_id,
                failed_loop=failed_loop,
            )
            if not will_retry:
                self._last_sequence_metrics = {
                    "attempt_count": attempts,
                    "initial_num_predict": self.num_predict,
                    "final_num_predict": budget,
                    "escalations": list(escalations),
                    "outcome": "failed_loop" if failed_loop else ("success" if valid else "invalid"),
                    "failed_loop": failed_loop,
                }
                if failed_loop:
                    raise AdaptiveTokenBudgetExhaustedError(
                        f"{self.lane} exhausted {attempts} bounded output-token attempts"
                    )
                return payload
            escalations.append(
                {
                    "attempt": attempts,
                    "reason": exhaustion_reason,
                    "from_num_predict": budget,
                    "to_num_predict": next_budget,
                }
            )
            budget = int(next_budget)

    def record(
        self,
        *,
        prompt: str,
        response_payload: dict[str, Any] | None = None,
        outcome: str,
        error_type: str | None = None,
        requested_num_predict: int | None = None,
        attempt: int = 1,
        adaptive_retry: bool = False,
        escalation_reason: str | None = None,
        escalated_to_num_predict: int | None = None,
        token_budget_exhausted: bool = False,
        call_id: str | None = None,
        failed_loop: bool = False,
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
            "requested_num_predict": max(1, int(requested_num_predict or self.num_predict)),
            "initial_num_predict": self.num_predict,
            "attempt": max(1, int(attempt)),
            "adaptive_retry": bool(adaptive_retry),
            "call_id": str(call_id or "").strip() or None,
            "token_budget_exhausted": bool(token_budget_exhausted),
            "failed_loop": bool(failed_loop),
            "escalation_reason": str(escalation_reason or "").strip() or None,
            "escalated_to_num_predict": (
                max(1, int(escalated_to_num_predict))
                if escalated_to_num_predict is not None
                else None
            ),
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
            "adaptive_token_budget": {
                "enabled": self.adaptive_policy.enabled,
                "max_attempts": self.adaptive_policy.max_attempts,
                "growth_factor": self.adaptive_policy.growth_factor,
                "max_predict_multiplier": self.adaptive_policy.max_predict_multiplier,
            },
            "last_call_metrics": dict(self._last_call_metrics) if self._last_call_metrics else None,
            "last_sequence_metrics": (
                dict(self._last_sequence_metrics) if self._last_sequence_metrics else None
            ),
        }

    @classmethod
    def _exhaustion_reason(
        cls,
        *,
        payload: dict[str, Any],
        requested_num_predict: int,
        valid: bool,
    ) -> str | None:
        reason = str(payload.get("done_reason") or "").strip().casefold()
        if reason in {"length", "max_tokens", "max_token", "token_limit"}:
            return "provider_token_limit"
        if reason in {"stop", "eos", "complete", "completed"}:
            return None
        eval_count = cls._optional_int(payload.get("eval_count"))
        if eval_count is not None and eval_count >= requested_num_predict:
            return "observed_token_limit"
        if not valid and eval_count is not None and eval_count >= max(1, int(requested_num_predict * 0.9)):
            return "invalid_output_near_token_limit"
        return None

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
