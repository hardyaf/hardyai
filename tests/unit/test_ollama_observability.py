from __future__ import annotations

from app.core.ollama_observability import OllamaCallObserver


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
