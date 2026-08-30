from __future__ import annotations

import pytest

from app.core.ollama_observability import (
    AdaptiveTokenBudgetExhaustedError,
    AdaptiveTokenBudgetPolicy,
    OllamaCallObserver,
)


def test_ollama_observer_makes_context_and_output_limits_explicit():
    observer = OllamaCallObserver(
        lane="main_conversation",
        model="qwen2.5:7b",
        num_ctx=12288,
        num_predict=1024,
    )

    assert observer.options(temperature=0.3) == {
        "temperature": 0.3,
        "num_ctx": 12288,
        "num_predict": 1024,
    }


def test_ollama_observer_records_counts_and_timings_without_prompt_text():
    recorded: list[dict] = []
    observer = OllamaCallObserver(
        lane="email_summary",
        model="qwen2.5:7b",
        num_ctx=12288,
        num_predict=1024,
        metrics_callback=recorded.append,
    )

    metrics = observer.record(
        prompt="private email body",
        response_payload={
            "prompt_eval_count": 6000,
            "eval_count": 250,
            "total_duration": 2_500_000_000,
            "prompt_eval_duration": 1_250_000_000,
            "eval_duration": 900_000_000,
            "done_reason": "stop",
        },
        outcome="success",
    )

    assert metrics["prompt_chars"] == len("private email body")
    assert metrics["prompt_eval_count"] == 6000
    assert metrics["eval_count"] == 250
    assert metrics["total_duration_ms"] == 2500.0
    assert metrics["context_utilization_ratio"] == round(6000 / 12288, 4)
    assert "private email body" not in repr(metrics)
    assert recorded == [metrics]
    assert observer.status()["last_call_metrics"] == metrics


def test_adaptive_budget_retries_token_exhaustion_and_keeps_content_out_of_metrics():
    recorded: list[dict] = []
    requested: list[int] = []
    responses = iter(
        [
            {"response": "", "eval_count": 256, "done_reason": "length"},
            {"response": '{"status":"ok"}', "eval_count": 80, "done_reason": "stop"},
        ]
    )
    observer = OllamaCallObserver(
        lane="main_repair",
        model="local-model",
        num_ctx=4096,
        num_predict=256,
        metrics_callback=recorded.append,
    )

    result = observer.generate(
        prompt="sensitive user request",
        temperature=0.0,
        invoke=lambda options: (requested.append(options["num_predict"]) or next(responses)),
        is_valid_response=lambda payload: bool(payload.get("response")),
    )

    assert result["done_reason"] == "stop"
    assert requested == [256, 512]
    assert recorded[0]["escalation_reason"] == "provider_token_limit"
    assert recorded[0]["escalated_to_num_predict"] == 512
    assert recorded[1]["adaptive_retry"] is True
    assert "sensitive user request" not in repr(recorded)


def test_adaptive_budget_only_fails_after_bounded_repeated_exhaustion():
    observer = OllamaCallObserver(
        lane="email_summary",
        model="local-model",
        num_ctx=4096,
        num_predict=100,
        adaptive_policy=AdaptiveTokenBudgetPolicy(max_attempts=3, max_predict_multiplier=8),
    )
    requested: list[int] = []

    with pytest.raises(AdaptiveTokenBudgetExhaustedError):
        observer.generate(
            prompt="prompt",
            temperature=0.0,
            invoke=lambda options: (
                requested.append(options["num_predict"])
                or {"response": "", "eval_count": options["num_predict"], "done_reason": "length"}
            ),
            is_valid_response=lambda _payload: False,
        )

    assert requested == [100, 200, 400]
    assert observer.status()["last_sequence_metrics"]["failed_loop"] is True
    assert observer.status()["last_sequence_metrics"]["outcome"] == "failed_loop"


def test_provider_stop_reason_wins_when_eval_count_exactly_matches_budget():
    observer = OllamaCallObserver(
        lane="micro",
        model="local-model",
        num_ctx=4096,
        num_predict=128,
    )
    calls = 0

    def invoke(_options):
        nonlocal calls
        calls += 1
        return {"response": "ok", "eval_count": 128, "done_reason": "stop"}

    observer.generate(
        prompt="prompt",
        temperature=0.0,
        invoke=invoke,
        is_valid_response=lambda payload: payload.get("response") == "ok",
    )

    assert calls == 1
