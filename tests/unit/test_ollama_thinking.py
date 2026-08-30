from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.accelerator_admission_app import _ollama_payload
from app.core.ollama_observability import (
    apply_ollama_think_mode,
    normalize_ollama_think_mode,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("off", False),
        ("LOW", "low"),
        ("medium", "medium"),
        ("high", "high"),
        ("max", "max"),
    ],
)
def test_ollama_thinking_mode_is_typed_and_bounded(raw, expected) -> None:
    assert normalize_ollama_think_mode(raw) == expected


def test_ollama_thinking_mode_rejects_provider_specific_free_text() -> None:
    with pytest.raises(ValueError, match="ollama_think_mode_invalid"):
        normalize_ollama_think_mode("unbounded-secret-mode")


def test_apply_ollama_thinking_omits_none_and_preserves_false() -> None:
    without_think = apply_ollama_think_mode({"model": "test"}, None)
    disabled = apply_ollama_think_mode({"model": "test"}, False)

    assert "think" not in without_think
    assert disabled["think"] is False


def test_accelerator_admission_accepts_candidate_thinking_control() -> None:
    payload = _ollama_payload(
        {
            "model": "qwen3.8:27b",
            "prompt": "synthetic acceptance probe",
            "stream": False,
            "think": "low",
        }
    )

    assert payload["think"] == "low"


def test_accelerator_admission_rejects_invalid_thinking_control() -> None:
    with pytest.raises(HTTPException) as caught:
        _ollama_payload(
            {
                "model": "qwen3.8:27b",
                "prompt": "synthetic acceptance probe",
                "stream": False,
                "think": {"level": "low"},
            }
        )

    assert caught.value.status_code == 400
    assert caught.value.detail == "accelerator_think_invalid"
