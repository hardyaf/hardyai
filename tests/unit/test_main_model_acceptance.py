from __future__ import annotations

from pathlib import Path

from scripts.benchmark_main_models import evaluate_case, load_cases


def test_main_acceptance_manifest_is_valid_and_has_safety_case() -> None:
    cases = load_cases(Path("benchmarks/models/main_acceptance_cases.json"))

    assert {case["kind"] for case in cases} == {"conversation", "turn_decision", "repair"}
    assert any(case.get("safety_critical") is True for case in cases)


def test_main_acceptance_evaluator_requires_expected_contract_labels() -> None:
    case = {
        "id": "typed-list",
        "kind": "turn_decision",
        "text": "synthetic",
        "context": {},
        "expect": {"mode": "execute_action", "intent": "lists.add_item"},
        "max_seconds": 5.0,
    }
    valid = {
        "mode": "execute_action",
        "intent": "lists.add_item",
        "confidence": 0.95,
        "reasoning": "ready",
        "entities": {"list_name": "errands", "item_text": "coffee"},
        "missing_fields": [],
        "message": "",
        "question": None,
        "source": "backend",
    }

    accepted = evaluate_case(case, valid, seconds=1.0)
    rejected = evaluate_case(case, {**valid, "intent": "lists.create_list"}, seconds=1.0)

    assert accepted["passed"] is True
    assert rejected["passed"] is False


def test_main_acceptance_evaluator_applies_latency_gate_without_output_content() -> None:
    case = {
        "id": "conversation",
        "kind": "conversation",
        "text": "synthetic",
        "context": {},
        "expect": {"nonempty": True},
        "max_seconds": 2.0,
    }

    result = evaluate_case(case, "valid private answer", seconds=3.0)

    assert result["contract_valid"] is True
    assert result["latency_ok"] is False
    assert "valid private answer" not in str(result)
