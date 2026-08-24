from __future__ import annotations

from app.core.agent_loop import MainAgentLoop
from app.core.agent_loop_types import (
    AgentLoopActionType,
    AgentLoopLimits,
    ExecutionOutcome,
    PlannerDecision,
)
from app.core.content_policy import MainAgentContentPolicyGate
from app.core.context_budget import ContextBudget
from app.core.evaluator import MainAgentEvaluator
from app.core.executor import MainAgentExecutor
from app.core.planner import MainAgentPlanner


class _StubExecutor:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    def execute(self, decision, *, agent_id: str):  # type: ignore[no-untyped-def]
        if decision.action_type == AgentLoopActionType.EXECUTE_COMMAND:
            if self._fail:
                return ExecutionOutcome(
                    status="error",
                    success=False,
                    summary=f"failed {decision.command_text}",
                    result={"status": "error", "message": "boom"},
                    classification={"intent": "lists.add_item"},
                    intent="lists.add_item",
                    tool_name="lists.add_item",
                )
            return ExecutionOutcome(
                status="ok",
                success=True,
                summary=f"ran {decision.command_text}",
                result={"status": "ok", "message": "done"},
                classification={"intent": "lists.add_item"},
                intent="lists.add_item",
                tool_name="lists.add_item",
            )
        return ExecutionOutcome(status="completed", success=True, summary="done")


def test_main_agent_executor_preserves_typed_plan_entities_without_reclassification():
    class _NeverClassify:
        def interpret(self, text, context=None):  # type: ignore[no-untyped-def]
            raise AssertionError("typed plan commands must not be reclassified")

    captured = []
    executor = MainAgentExecutor(
        micro_jarvis=_NeverClassify(),  # type: ignore[arg-type]
        run_fast_command=lambda classification, decision: (
            captured.append((classification, decision))
            or {"status": "ok", "message": "done"}
        ),
    )
    decision = PlannerDecision(
        step_number=2,
        action_type=AgentLoopActionType.EXECUTE_COMMAND,
        rationale="test",
        command_text="add alpha token to Deployment Compound Smoke",
        metadata={
            "intent": "lists.add_item",
            "entities": {
                "list_name": "Deployment Compound Smoke",
                "item_text": "alpha token",
            },
            "confidence": 0.94,
        },
    )

    outcome = executor.execute(decision, agent_id="jarvis")

    assert outcome.success is True
    assert captured[0][0].intent.value == "lists.add_item"
    assert captured[0][0].entities["list_name"] == "Deployment Compound Smoke"


def test_main_agent_loop_completes_two_step_plan_with_trace():
    loop = MainAgentLoop(
        planner=MainAgentPlanner(auto_approve_actions=True),
        evaluator=MainAgentEvaluator(),
        context_budget=ContextBudget(max_chars=1200),
        limits=AgentLoopLimits(max_steps=4, max_failures=2),
    )

    execution = loop.run(
        goal_text="create and then add",
        plan={
            "plan_type": "list.create_and_add",
            "commands": [
                {"command_text": "create weekend list", "target": "weekend"},
                {"command_text": "add bananas to weekend", "target": "weekend"},
            ],
        },
        agent_id="jarvis",
        execution_context={"source_interface": "web"},
        executor=_StubExecutor(fail=False),  # type: ignore[arg-type]
    )

    assert execution["status"] == "ok"
    assert execution["loop_state"] == "COMPLETED"
    assert execution["requested_count"] == 2
    assert execution["success_count"] == 2
    assert len(execution["agent_loop"]["trace"]) >= 2
    trace_item = execution["agent_loop"]["trace"][0]
    assert isinstance(trace_item.get("subgoal"), str)
    assert isinstance(trace_item.get("preconditions"), list)
    assert isinstance(trace_item.get("expected_outcome"), str)
    assert isinstance(trace_item.get("uncertainty"), float)
    assert trace_item.get("fallback_action") is not None
    first_result = execution["results"][0]
    assert isinstance(first_result.get("planning"), dict)
    assert first_result["planning"]["subgoal"]
    budget = execution["agent_loop"]["context_budget"]
    assert budget["used_tokens_estimate"] >= 1
    assert budget["max_tokens_estimate"] >= budget["used_tokens_estimate"]


def test_main_agent_loop_stops_after_repeated_failures():
    loop = MainAgentLoop(
        planner=MainAgentPlanner(auto_approve_actions=True),
        evaluator=MainAgentEvaluator(),
        context_budget=ContextBudget(max_chars=1200),
        limits=AgentLoopLimits(max_steps=4, max_failures=1),
    )

    execution = loop.run(
        goal_text="do several failing actions",
        plan={
            "plan_type": "failing.plan",
            "commands": [
                {"command_text": "fail one"},
                {"command_text": "fail two"},
                {"command_text": "fail three"},
            ],
        },
        agent_id="jarvis",
        execution_context={"source_interface": "web"},
        executor=_StubExecutor(fail=True),  # type: ignore[arg-type]
    )

    assert execution["status"] == "error"
    assert execution["loop_state"] == "FAILED"
    assert execution["success_count"] == 0
    assert execution["failed_count"] >= 1
    assert execution["agent_loop"]["terminal_status"] == "error"
    trace_item = execution["agent_loop"]["trace"][0]
    assert trace_item["failure_class"] in {"unknown", "transient", "missing_data", "not_found", "policy_block"}
    assert "next_action_hint" in trace_item


def test_main_agent_planner_auto_approves_when_enabled():
    plan = {
        "commands": [
            {
                "command_text": "turn office test light off",
                "requires_approval": True,
            }
        ]
    }

    auto_planner = MainAgentPlanner(auto_approve_actions=True)
    auto_decision = auto_planner.next_decision(step_number=1, plan=plan)
    assert auto_decision.action_type == AgentLoopActionType.EXECUTE_COMMAND
    assert auto_decision.metadata["approval_mode"] == "auto_approved"
    assert auto_decision.subgoal
    assert auto_decision.preconditions
    assert auto_decision.expected_outcome
    assert isinstance(auto_decision.uncertainty, float)
    assert auto_decision.fallback_action == "request_user_input"

    gated_planner = MainAgentPlanner(auto_approve_actions=False)
    gated_decision = gated_planner.next_decision(step_number=1, plan=plan)
    assert gated_decision.action_type == AgentLoopActionType.REQUEST_APPROVAL
    assert gated_decision.expected_outcome.startswith("User approval")


def test_main_agent_loop_blocks_policy_violating_command_for_child_context():
    loop = MainAgentLoop(
        planner=MainAgentPlanner(auto_approve_actions=True),
        evaluator=MainAgentEvaluator(),
        context_budget=ContextBudget(max_chars=1200),
        limits=AgentLoopLimits(max_steps=4, max_failures=2),
    )

    execution = loop.run(
        goal_text="create a weapon list",
        plan={"commands": [{"command_text": "create weapon list", "target": "weapon"}]},
        agent_id="jarvis",
        execution_context={"kid_mode": True},
        executor=_StubExecutor(fail=False),  # type: ignore[arg-type]
        content_policy_gate=MainAgentContentPolicyGate(
            enabled=True,
            enforce_for_children_only=True,
            blocked_patterns=[r"\bweapon\b"],
        ),
    )

    assert execution["status"] == "needs_input"
    assert execution["loop_state"] == "WAITING_FOR_USER"
    latest_policy = execution["agent_loop"]["policy"]["latest"]
    assert latest_policy["allowed"] is False
    assert latest_policy["status"] == "blocked"
    assert execution["results"][0]["result"]["policy"]["status"] == "blocked"


def test_main_agent_loop_compacts_session_context_when_budget_is_small():
    loop = MainAgentLoop(
        planner=MainAgentPlanner(auto_approve_actions=True),
        evaluator=MainAgentEvaluator(),
        context_budget=ContextBudget(max_chars=180),
        limits=AgentLoopLimits(max_steps=4, max_failures=2),
    )
    long_history = [
        "turn summary " + ("alpha " * 20),
        "turn summary " + ("beta " * 20),
        "turn summary " + ("gamma " * 20),
        "turn summary " + ("delta " * 20),
        "turn summary " + ("epsilon " * 20),
    ]

    execution = loop.run(
        goal_text="create and add task",
        plan={"commands": [{"command_text": "create weekend list"}]},
        agent_id="jarvis",
        execution_context={"token_session_turn_summaries": long_history},
        executor=_StubExecutor(fail=False),  # type: ignore[arg-type]
    )

    budget = execution["agent_loop"]["context_budget"]
    assert budget["compaction"]["applied"] is True
    assert budget["compaction"]["dropped_section_count"] >= 1


def test_main_agent_planner_uses_reasoning_overrides_from_command_metadata():
    planner = MainAgentPlanner(auto_approve_actions=True)
    plan = {
        "commands": [
            {
                "command_text": "add bananas to groceries",
                "subgoal": "Add fruit to the existing groceries list.",
                "preconditions": ["groceries_list_exists", "item_text_present"],
                "expected_outcome": "Groceries includes bananas.",
                "uncertainty": 0.19,
                "fallback_action": "request_user_input",
            }
        ]
    }

    decision = planner.next_decision(step_number=1, plan=plan)
    assert decision.action_type == AgentLoopActionType.EXECUTE_COMMAND
    assert decision.subgoal == "Add fruit to the existing groceries list."
    assert decision.preconditions == ["groceries_list_exists", "item_text_present"]
    assert decision.expected_outcome == "Groceries includes bananas."
    assert decision.uncertainty == 0.19
    assert decision.fallback_action == "request_user_input"


def test_main_agent_planner_emits_scored_candidate_actions():
    planner = MainAgentPlanner(auto_approve_actions=True)
    plan = {
        "commands": [
            {
                "command_text": "add milk to groceries",
                "confidence": 0.92,
            }
        ]
    }

    decision = planner.next_decision(step_number=1, plan=plan)
    assert decision.action_type == AgentLoopActionType.EXECUTE_COMMAND
    assert 2 <= len(decision.candidate_actions) <= 4
    assert decision.selected_candidate_score is not None
    assert isinstance(decision.metadata.get("candidate_actions"), list)
    assert decision.metadata.get("selected_candidate_action") == AgentLoopActionType.EXECUTE_COMMAND.value


def test_main_agent_planner_prefers_user_input_when_required_or_missing_fields():
    planner = MainAgentPlanner(auto_approve_actions=True)
    plan = {
        "commands": [
            {
                "command_text": "add milk",
                "requires_user_input": True,
                "missing_fields": ["list_name"],
                "confidence": 0.62,
            }
        ]
    }

    decision = planner.next_decision(step_number=1, plan=plan)
    assert decision.action_type == AgentLoopActionType.REQUEST_USER_INPUT
    scores = {candidate.action_type.value: candidate.score for candidate in decision.candidate_actions}
    assert scores["request_user_input"] >= scores["execute_command"]


def test_main_agent_planner_tie_break_prefers_execute_command():
    planner = MainAgentPlanner(auto_approve_actions=True)
    plan = {
        "commands": [
            {
                "command_text": "add milk to groceries",
                "candidate_score_overrides": {
                    "execute_command": 0.6,
                    "request_user_input": 0.6,
                },
            }
        ]
    }

    decision = planner.next_decision(step_number=1, plan=plan)
    assert decision.action_type == AgentLoopActionType.EXECUTE_COMMAND


def test_main_agent_planner_routes_to_user_input_when_preconditions_unmet():
    planner = MainAgentPlanner(auto_approve_actions=True)
    plan = {
        "commands": [
            {
                "command_text": "add apples to it",
                "target": None,
                "missing_fields": [],
            }
        ]
    }

    decision = planner.next_decision(step_number=1, plan=plan)
    assert decision.action_type == AgentLoopActionType.REQUEST_USER_INPUT
    assert decision.rationale == "preconditions_unmet_request_user_input"
    unmet = decision.metadata.get("unmet_preconditions")
    assert isinstance(unmet, list)
    assert "resolved_reference" in unmet
    planning_checks = decision.metadata.get("precondition_evaluation")
    assert isinstance(planning_checks, dict)
    assert planning_checks["status"]["resolved_reference"] is False


def test_main_agent_planner_executes_when_preconditions_are_met():
    planner = MainAgentPlanner(auto_approve_actions=True)
    plan = {
        "commands": [
            {
                "command_text": "add apples to groceries",
                "target": "groceries",
                "precondition_checks": {
                    "target_context_available": True,
                    "resolved_reference": True,
                },
                "confidence": 0.88,
            }
        ]
    }

    decision = planner.next_decision(step_number=1, plan=plan)
    assert decision.action_type == AgentLoopActionType.EXECUTE_COMMAND
    unmet = decision.metadata.get("unmet_preconditions")
    assert isinstance(unmet, list)
    assert not unmet


def test_main_agent_planner_materialize_plan_injects_verification_for_low_confidence_write():
    planner = MainAgentPlanner(auto_approve_actions=True)
    plan = {
        "commands": [
            {
                "command_text": "add milk to groceries",
                "target": "groceries",
                "confidence": 0.4,
            }
        ]
    }

    materialized = planner.materialize_plan(plan=plan)
    commands = materialized["commands"]
    assert len(commands) == 2
    assert commands[0]["command_text"] == "add milk to groceries"
    assert commands[1]["command_text"] == "show me groceries"
    assert commands[1]["is_verification"] is True
    assert commands[1]["skip_auto_verify"] is True
    assert commands[1]["verification_reason"] == "low_confidence_write"
    materialization = materialized["_planner_materialization"]
    assert materialization["original_command_count"] == 1
    assert materialization["materialized_command_count"] == 2
    assert materialization["injected_verification_count"] == 1
    injected = materialization["injected_verifications"]
    assert isinstance(injected, list)
    assert len(injected) == 1
    assert injected[0]["original_command_text"] == "add milk to groceries"
    assert injected[0]["verification_command_text"] == "show me groceries"
    assert injected[0]["verification_reason"] == "low_confidence_write"


def test_main_agent_planner_materialize_plan_skips_verification_for_high_confidence_write():
    planner = MainAgentPlanner(auto_approve_actions=True)
    plan = {
        "commands": [
            {
                "command_text": "add milk to groceries",
                "target": "groceries",
                "confidence": 0.95,
            }
        ]
    }

    materialized = planner.materialize_plan(plan=plan)
    commands = materialized["commands"]
    assert len(commands) == 1
    assert commands[0]["command_text"] == "add milk to groceries"
    materialization = materialized["_planner_materialization"]
    assert materialization["injected_verification_count"] == 0
    assert materialization["injected_verifications"] == []


def test_main_agent_loop_runs_materialized_verification_step_when_injected():
    loop = MainAgentLoop(
        planner=MainAgentPlanner(auto_approve_actions=True),
        evaluator=MainAgentEvaluator(),
        context_budget=ContextBudget(max_chars=1200),
        limits=AgentLoopLimits(max_steps=4, max_failures=2),
    )

    execution = loop.run(
        goal_text="add milk and verify",
        plan={
            "commands": [
                {
                    "command_text": "add milk to groceries",
                    "target": "groceries",
                    "confidence": 0.4,
                }
            ]
        },
        agent_id="jarvis",
        execution_context={"source_interface": "web"},
        executor=_StubExecutor(fail=False),  # type: ignore[arg-type]
    )

    assert execution["status"] == "ok"
    assert execution["requested_count"] == 2
    assert execution["attempted_count"] == 2
    assert execution["success_count"] == 2
    assert execution["results"][0]["command_text"] == "add milk to groceries"
    assert execution["results"][1]["command_text"] == "show me groceries"
    materialization = execution["agent_loop"]["plan_materialization"]
    assert materialization is not None
    assert materialization["injected_verification_count"] == 1
    assert materialization["injected_verifications"][0]["verification_command_text"] == "show me groceries"
