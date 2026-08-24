from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from app.core.agent_loop_types import (
    AgentLoopActionType,
    AgentLoopLimits,
    AgentLoopState,
    AgentLoopTraceEntry,
    ExecutionOutcome,
    PlannerDecision,
)
from app.core.content_policy import MainAgentContentPolicyGate
from app.core.context_budget import ContextBudget
from app.core.evaluator import MainAgentEvaluator
from app.core.executor import MainAgentExecutor
from app.core.planner import MainAgentPlanner


class MainAgentLoop:
    """Controlled planner->execute->evaluate loop for main agent plans."""

    def __init__(
        self,
        *,
        planner: MainAgentPlanner,
        evaluator: MainAgentEvaluator,
        context_budget: ContextBudget,
        limits: AgentLoopLimits,
        event_hook: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._planner = planner
        self._evaluator = evaluator
        self._context_budget = context_budget
        self._limits = limits
        self._event_hook = event_hook

    def run(
        self,
        *,
        goal_text: str,
        plan: dict[str, Any],
        agent_id: str,
        execution_context: dict[str, Any] | None,
        executor: MainAgentExecutor,
        content_policy_gate: MainAgentContentPolicyGate | None = None,
    ) -> dict[str, Any]:
        run_id = str(uuid4())
        resolved_execution_context = dict(execution_context or {})
        token_session_summaries = resolved_execution_context.get("token_session_turn_summaries")
        if not isinstance(token_session_summaries, list):
            token_session_summaries = []
        session_turn_summaries = [str(item).strip() for item in token_session_summaries if str(item).strip()]
        supplemental_sections = [
            str(plan.get("plan_type") or ""),
            str(plan.get("scope") or ""),
            str(plan.get("action") or ""),
            *session_turn_summaries,
        ]
        budget_snapshot = self._context_budget.snapshot(
            goal_text=goal_text,
            context=resolved_execution_context,
            supplemental_sections=supplemental_sections,
        )
        compaction = {
            "applied": False,
            "strategy": "none",
            "input_section_count": len(supplemental_sections),
            "retained_section_count": len(supplemental_sections),
            "dropped_section_count": 0,
            "session_summary": "",
        }
        if budget_snapshot.trimmed:
            compact_sections = self._compact_sections(supplemental_sections, keep_tail=4, max_chars_per_section=180)
            budget_snapshot = self._context_budget.snapshot(
                goal_text=goal_text,
                context=resolved_execution_context,
                supplemental_sections=compact_sections,
            )
            compaction = {
                "applied": True,
                "strategy": "rolling_session_tail_compaction_v1",
                "input_section_count": len(supplemental_sections),
                "retained_section_count": len(compact_sections),
                "dropped_section_count": max(0, len(supplemental_sections) - len(compact_sections)),
                "session_summary": " | ".join(compact_sections[-3:]) if compact_sections else "",
            }

        execution_plan = self._planner.materialize_plan(plan=plan)
        commands = execution_plan.get("commands")
        requested_count = len(commands) if isinstance(commands, list) else 0
        loop_state = AgentLoopState.READY
        trace_entries: list[AgentLoopTraceEntry] = []
        results: list[dict[str, Any]] = []
        policy_decisions: list[dict[str, Any]] = []
        success_count = 0
        consecutive_failures = 0
        terminal_status = "error"
        terminal_message = "Loop failed before execution."

        for step_number in range(1, self._limits.max_steps + 1):
            loop_state = AgentLoopState.THINKING
            planner_decision = self._planner.next_decision(step_number=step_number, plan=execution_plan)
            state_before = loop_state
            policy_verdict_payload: dict[str, Any] | None = None
            if (
                content_policy_gate is not None
                and planner_decision.action_type == AgentLoopActionType.EXECUTE_COMMAND
                and planner_decision.command_text.strip()
            ):
                verdict = content_policy_gate.evaluate(
                    goal_text=goal_text,
                    command_text=planner_decision.command_text,
                    context=resolved_execution_context,
                )
                policy_verdict_payload = verdict.to_dict()
                policy_decisions.append(
                    {
                        "step_number": step_number,
                        "command_text": planner_decision.command_text,
                        **policy_verdict_payload,
                    }
                )
                if not verdict.allowed:
                    outcome = ExecutionOutcome(
                        status="blocked_by_policy",
                        success=False,
                        summary="Command blocked by content policy.",
                        result={
                            "status": "blocked",
                            "message": "This command was blocked by content policy.",
                            "policy": policy_verdict_payload,
                        },
                    )
                else:
                    outcome = executor.execute(planner_decision, agent_id=agent_id)
            else:
                outcome = executor.execute(planner_decision, agent_id=agent_id)

            if planner_decision.command_text:
                result_status = "ok" if outcome.success else "error"
                if outcome.status in {"waiting_for_approval", "waiting_for_user", "blocked_by_policy"}:
                    result_status = "waiting"
                result_payload = outcome.result or {"status": outcome.status, "message": outcome.summary}
                if policy_verdict_payload is not None and isinstance(result_payload, dict):
                    result_payload = dict(result_payload)
                    result_payload.setdefault("policy", policy_verdict_payload)
                results.append(
                    {
                        "index": step_number - 1,
                        "command_text": planner_decision.command_text,
                        "target": planner_decision.target,
                        "planning": {
                            "subgoal": planner_decision.subgoal,
                            "preconditions": list(planner_decision.preconditions),
                            "expected_outcome": planner_decision.expected_outcome,
                            "uncertainty": planner_decision.uncertainty,
                            "fallback_action": planner_decision.fallback_action,
                            "rationale": planner_decision.rationale,
                            "candidate_actions": [
                                candidate.to_dict() for candidate in planner_decision.candidate_actions
                            ],
                            "selected_candidate_score": planner_decision.selected_candidate_score,
                            "precondition_evaluation": planner_decision.metadata.get("precondition_evaluation"),
                            "unmet_preconditions": planner_decision.metadata.get("unmet_preconditions"),
                        },
                        "classification": outcome.classification,
                        "result": result_payload,
                        "status": result_status,
                    }
                )

            if planner_decision.action_type == AgentLoopActionType.EXECUTE_COMMAND:
                loop_state = AgentLoopState.ACTING
            if outcome.success:
                success_count += 1
                consecutive_failures = 0
            elif planner_decision.action_type == AgentLoopActionType.EXECUTE_COMMAND:
                consecutive_failures += 1

            has_more_commands = isinstance(commands, list) and step_number < len(commands)
            evaluation = self._evaluator.evaluate(
                decision=planner_decision,
                outcome=outcome,
                has_more_commands=has_more_commands,
                consecutive_failures=consecutive_failures,
                limits=self._limits,
            )
            evaluation_next_state = evaluation.next_state
            continue_loop = evaluation.continue_loop

            if results:
                results[-1]["evaluation"] = {
                    "failure_class": evaluation.failure_class,
                    "next_action_hint": evaluation.next_action_hint,
                    "terminal_status": evaluation.terminal_status,
                    "message": evaluation.message,
                }

            trace_entry = AgentLoopTraceEntry(
                step_number=step_number,
                state_before=state_before,
                chosen_action=planner_decision.action_type.value,
                rationale=planner_decision.rationale,
                subgoal=planner_decision.subgoal or None,
                preconditions=list(planner_decision.preconditions),
                expected_outcome=planner_decision.expected_outcome or None,
                uncertainty=planner_decision.uncertainty,
                fallback_action=planner_decision.fallback_action,
                candidate_actions=[candidate.to_dict() for candidate in planner_decision.candidate_actions],
                selected_candidate_score=planner_decision.selected_candidate_score,
                command_text=planner_decision.command_text or None,
                target=planner_decision.target,
                tool_or_skill=outcome.tool_name or outcome.intent,
                result_status=outcome.status,
                result_summary=outcome.summary,
                failure_class=evaluation.failure_class,
                next_action_hint=evaluation.next_action_hint,
                state_after=evaluation_next_state,
            )
            trace_entries.append(trace_entry)
            self._record_step_event(run_id=run_id, entry=trace_entry, decision=planner_decision, outcome=outcome)

            loop_state = evaluation_next_state
            if not continue_loop:
                terminal_status = evaluation.terminal_status or "error"
                terminal_message = str(evaluation.message or outcome.summary or "").strip() or "Loop stopped."
                break
        else:
            loop_state = AgentLoopState.FAILED
            terminal_status = "error"
            terminal_message = "Loop stopped after reaching max step limit."
            trace_entries.append(
                AgentLoopTraceEntry(
                    step_number=self._limits.max_steps,
                    state_before=AgentLoopState.THINKING,
                    chosen_action="guardrail.max_steps",
                    rationale="max_steps_reached",
                    subgoal="Stop loop when configured max step limit is reached.",
                    preconditions=["step_limit_reached"],
                    expected_outcome="Loop exits safely instead of running indefinitely.",
                    uncertainty=0.0,
                    fallback_action="stop",
                    candidate_actions=[],
                    selected_candidate_score=None,
                    command_text=None,
                    target=None,
                    tool_or_skill=None,
                    result_status="error",
                    result_summary=terminal_message,
                    failure_class="unknown",
                    next_action_hint="stop_after_step_cap",
                    state_after=AgentLoopState.FAILED,
                )
            )

        overall_status = self._overall_status(
            loop_state=loop_state,
            terminal_status=terminal_status,
            requested_count=requested_count,
            success_count=success_count,
            attempted_count=len(results),
        )
        failed_count = max(0, requested_count - success_count)

        return {
            "status": overall_status,
            "plan_type": execution_plan.get("plan_type"),
            "scope": execution_plan.get("scope"),
            "action": execution_plan.get("action"),
            "requested_count": requested_count,
            "attempted_count": len(results),
            "success_count": success_count,
            "failed_count": failed_count,
            "results": results,
            "loop_state": loop_state.value,
            "agent_loop": {
                "run_id": run_id,
                "state": loop_state.value,
                "terminal_status": terminal_status,
                "terminal_message": terminal_message,
                "max_steps": self._limits.max_steps,
                "max_failures": self._limits.max_failures,
                "context_budget": {
                    **budget_snapshot.to_dict(),
                    "compaction": compaction,
                },
                "policy": {
                    "applied": content_policy_gate is not None,
                    "decisions": policy_decisions,
                    "latest": policy_decisions[-1] if policy_decisions else None,
                },
                "plan_materialization": execution_plan.get("_planner_materialization"),
                "trace": [entry.to_dict() for entry in trace_entries],
                "trace_summary": [
                    (
                        f"Step {entry.step_number}: {entry.chosen_action} [{entry.subgoal or 'no_subgoal'}] -> "
                        f"{entry.result_status} ({entry.state_after.value})"
                    )
                    for entry in trace_entries
                ],
            },
        }

    @staticmethod
    def _overall_status(
        *,
        loop_state: AgentLoopState,
        terminal_status: str,
        requested_count: int,
        success_count: int,
        attempted_count: int,
    ) -> str:
        if loop_state == AgentLoopState.COMPLETED:
            if requested_count > 0 and success_count == requested_count:
                return "ok"
            if success_count > 0:
                return "partial"
            return "error"
        if loop_state in {AgentLoopState.WAITING_FOR_APPROVAL, AgentLoopState.WAITING_FOR_USER}:
            return "needs_input"
        if terminal_status == "ok":
            return "ok"
        if terminal_status == "blocked":
            return "needs_input"
        if success_count > 0 and attempted_count > success_count:
            return "partial"
        return "error"

    @staticmethod
    def _compact_sections(sections: list[str], *, keep_tail: int, max_chars_per_section: int) -> list[str]:
        if keep_tail <= 0:
            return []
        cleaned = [str(item).strip() for item in sections if str(item).strip()]
        if not cleaned:
            return []
        tail = cleaned[-keep_tail:]
        compacted: list[str] = []
        for section in tail:
            if len(section) <= max_chars_per_section:
                compacted.append(section)
            else:
                compacted.append(f"{section[: max_chars_per_section - 3]}...")
        return compacted

    def _record_step_event(
        self,
        *,
        run_id: str,
        entry: AgentLoopTraceEntry,
        decision: PlannerDecision,
        outcome: Any,
    ) -> None:
        if self._event_hook is None:
            return
        self._event_hook(
            "main.agent_loop.step",
            {
                "run_id": run_id,
                "step_number": entry.step_number,
                "state_before": entry.state_before.value,
                "state_after": entry.state_after.value,
                "chosen_action": entry.chosen_action,
                "rationale": entry.rationale,
                "subgoal": entry.subgoal,
                "preconditions": list(entry.preconditions),
                "expected_outcome": entry.expected_outcome,
                "uncertainty": entry.uncertainty,
                "fallback_action": entry.fallback_action,
                "candidate_actions": [dict(item) for item in entry.candidate_actions],
                "selected_candidate_score": entry.selected_candidate_score,
                "command_text": entry.command_text,
                "target": entry.target,
                "tool_or_skill": entry.tool_or_skill,
                "result_status": entry.result_status,
                "result_summary": entry.result_summary,
                "failure_class": entry.failure_class,
                "next_action_hint": entry.next_action_hint,
                "planner_metadata": decision.metadata,
                "classification": getattr(outcome, "classification", {}) or {},
            },
        )
