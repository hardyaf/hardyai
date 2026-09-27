from __future__ import annotations

import pytest

from app.core.ollama_observability import (
    AdaptiveTokenBudgetExhaustedError,
    OllamaCallObserver,
)
from app.skills.domains.email_agent.model_budget import background_email_token_budget_policy


@pytest.mark.parametrize("lane", ["email_summary", "email_classifier"])
def test_background_email_model_exhaustion_never_escalates(lane: str) -> None:
    requested: list[int] = []
    observer = OllamaCallObserver(
        lane=lane,
        model="local-model",
        num_ctx=4096,
        num_predict=256,
        adaptive_policy=background_email_token_budget_policy(),
    )

    with pytest.raises(AdaptiveTokenBudgetExhaustedError):
        observer.generate(
            prompt="bounded background enrichment",
            temperature=0.0,
            invoke=lambda options: (
                requested.append(options["num_predict"])
                or {
                    "response": "",
                    "eval_count": options["num_predict"],
                    "done_reason": "length",
                }
            ),
            is_valid_response=lambda _payload: False,
        )

    assert requested == [256]
    status = observer.status()
    assert status["adaptive_token_budget"] == {
        "enabled": False,
        "max_attempts": 1,
        "growth_factor": 2.0,
        "max_predict_multiplier": 1,
    }
    assert status["last_sequence_metrics"]["failed_loop"] is True
