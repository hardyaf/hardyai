from __future__ import annotations

from app.core.agent_loop_types import (
    AgentLoopActionType,
    AgentLoopLimits,
    ExecutionOutcome,
    PlannerDecision,
)
from app.core.evaluator import MainAgentEvaluator


def _decision(action_type: AgentLoopActionType = AgentLoopActionType.EXECUTE_COMMAND) -> PlannerDecision:
    return PlannerDecision(
        step_number=1,
        action_type=action_type,
        rationale="test",
        command_text="test command",
    )


def test_evaluator_classifies_policy_block_and_waits_for_user():
    evaluator = MainAgentEvaluator()
    outcome = ExecutionOutcome(
        status="blocked_by_policy",
        success=False,
        summary="Command blocked by content policy.",
        result={"status": "blocked", "message": "blocked"},
    )
    result = evaluator.evaluate(
        decision=_decision(),
        outcome=outcome,
        has_more_commands=True,
        consecutive_failures=1,
        limits=AgentLoopLimits(max_steps=4, max_failures=2),
    )
    assert result.failure_class == "policy_block"
    assert result.next_state.value == "WAITING_FOR_USER"
    assert result.terminal_status == "blocked"


def test_evaluator_classifies_missing_data_and_waits_for_user():
    evaluator = MainAgentEvaluator()
    outcome = ExecutionOutcome(
        status="needs_clarification",
        success=False,
        summary="Missing required fields.",
        result={"status": "needs_clarification", "message": "missing when_hint"},
    )
    result = evaluator.evaluate(
        decision=_decision(),
        outcome=outcome,
        has_more_commands=True,
        consecutive_failures=1,
        limits=AgentLoopLimits(max_steps=4, max_failures=2),
    )
    assert result.failure_class == "missing_data"
    assert result.next_state.value == "WAITING_FOR_USER"
    assert result.terminal_status == "needs_user"


def test_evaluator_classifies_not_found_and_requests_refinement():
    evaluator = MainAgentEvaluator()
    outcome = ExecutionOutcome(
        status="unknown_list",
        success=False,
        summary="List not found.",
        result={"status": "unknown_list", "message": "list does not exist"},
    )
    result = evaluator.evaluate(
        decision=_decision(),
        outcome=outcome,
        has_more_commands=True,
        consecutive_failures=1,
        limits=AgentLoopLimits(max_steps=4, max_failures=2),
    )
    assert result.failure_class == "not_found"
    assert result.next_state.value == "WAITING_FOR_USER"
    assert result.next_action_hint == "request_target_refinement"


def test_evaluator_classifies_transient_and_continues_when_more_commands():
    evaluator = MainAgentEvaluator()
    outcome = ExecutionOutcome(
        status="error",
        success=False,
        summary="timeout from service",
        result={"status": "error", "message": "temporary timeout"},
    )
    result = evaluator.evaluate(
        decision=_decision(),
        outcome=outcome,
        has_more_commands=True,
        consecutive_failures=1,
        limits=AgentLoopLimits(max_steps=4, max_failures=3),
    )
    assert result.failure_class == "transient"
    assert result.next_state.value == "THINKING"
    assert result.continue_loop is True


def test_evaluator_classifies_unknown_and_stops_at_retry_limit():
    evaluator = MainAgentEvaluator()
    outcome = ExecutionOutcome(
        status="error",
        success=False,
        summary="unexpected failure",
        result={"status": "error", "message": "boom"},
    )
    result = evaluator.evaluate(
        decision=_decision(),
        outcome=outcome,
        has_more_commands=True,
        consecutive_failures=2,
        limits=AgentLoopLimits(max_steps=4, max_failures=2),
    )
    assert result.failure_class == "unknown"
    assert result.next_state.value == "FAILED"
    assert result.continue_loop is False
