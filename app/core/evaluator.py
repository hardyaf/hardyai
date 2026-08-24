from __future__ import annotations

from app.core.agent_loop_types import (
    AgentLoopActionType,
    AgentLoopLimits,
    AgentLoopState,
    EvaluatorOutcome,
    ExecutionOutcome,
    PlannerDecision,
)


class MainAgentEvaluator:
    """Determines the next loop state after each action result."""

    def evaluate(
        self,
        *,
        decision: PlannerDecision,
        outcome: ExecutionOutcome,
        has_more_commands: bool,
        consecutive_failures: int,
        limits: AgentLoopLimits,
    ) -> EvaluatorOutcome:
        if decision.action_type == AgentLoopActionType.REQUEST_APPROVAL:
            return EvaluatorOutcome(
                next_state=AgentLoopState.WAITING_FOR_APPROVAL,
                continue_loop=False,
                terminal_status="needs_approval",
                message=outcome.summary,
                next_action_hint="await_user_approval",
            )
        if decision.action_type == AgentLoopActionType.REQUEST_USER_INPUT:
            return EvaluatorOutcome(
                next_state=AgentLoopState.WAITING_FOR_USER,
                continue_loop=False,
                terminal_status="needs_user",
                message=outcome.summary,
                next_action_hint="ask_for_missing_input",
            )
        if decision.action_type == AgentLoopActionType.FAIL:
            return EvaluatorOutcome(
                next_state=AgentLoopState.FAILED,
                continue_loop=False,
                terminal_status="error",
                message=outcome.summary,
                failure_class="unknown",
                next_action_hint="stop_and_report_failure",
            )
        if decision.action_type == AgentLoopActionType.COMPLETE:
            return EvaluatorOutcome(
                next_state=AgentLoopState.COMPLETED,
                continue_loop=False,
                terminal_status="ok",
                message=outcome.summary,
                next_action_hint="return_final_response",
            )

        if not outcome.success:
            failure_class = self._classify_failure(outcome)
            if failure_class == "policy_block":
                return EvaluatorOutcome(
                    next_state=AgentLoopState.WAITING_FOR_USER,
                    continue_loop=False,
                    terminal_status="blocked",
                    message="Action blocked by policy; awaiting user adjustment.",
                    failure_class=failure_class,
                    next_action_hint="ask_user_for_safe_alternative",
                )
            if failure_class == "missing_data":
                return EvaluatorOutcome(
                    next_state=AgentLoopState.WAITING_FOR_USER,
                    continue_loop=False,
                    terminal_status="needs_user",
                    message="Missing required information; request clarification.",
                    failure_class=failure_class,
                    next_action_hint="ask_clarifying_question",
                )
            if failure_class == "not_found":
                return EvaluatorOutcome(
                    next_state=AgentLoopState.WAITING_FOR_USER,
                    continue_loop=False,
                    terminal_status="needs_user",
                    message="Target was not found; ask user to refine target.",
                    failure_class=failure_class,
                    next_action_hint="request_target_refinement",
                )
            if failure_class == "transient":
                if consecutive_failures >= limits.max_failures:
                    return EvaluatorOutcome(
                        next_state=AgentLoopState.FAILED,
                        continue_loop=False,
                        terminal_status="error",
                        message="Loop stopped after repeated transient failures.",
                        failure_class=failure_class,
                        next_action_hint="stop_after_retry_budget_exhausted",
                    )
                if has_more_commands:
                    return EvaluatorOutcome(
                        next_state=AgentLoopState.THINKING,
                        continue_loop=True,
                        terminal_status=None,
                        message="Transient failure; continue with replanning.",
                        failure_class=failure_class,
                        next_action_hint="switch_strategy_and_continue",
                    )
                return EvaluatorOutcome(
                    next_state=AgentLoopState.WAITING_FOR_USER,
                    continue_loop=False,
                    terminal_status="needs_user",
                    message="Temporary issue with no fallback step; request retry from user.",
                    failure_class=failure_class,
                    next_action_hint="ask_user_to_retry_later",
                )

            if consecutive_failures >= limits.max_failures:
                return EvaluatorOutcome(
                    next_state=AgentLoopState.FAILED,
                    continue_loop=False,
                    terminal_status="error",
                    message="Loop stopped after repeated action failures.",
                    failure_class=failure_class,
                    next_action_hint="stop_after_retry_budget_exhausted",
                )
            if has_more_commands:
                return EvaluatorOutcome(
                    next_state=AgentLoopState.THINKING,
                    continue_loop=True,
                    terminal_status=None,
                    message="Continuing after action failure with fallback strategy.",
                    failure_class=failure_class,
                    next_action_hint="advance_to_next_strategy",
                )
            return EvaluatorOutcome(
                next_state=AgentLoopState.FAILED,
                continue_loop=False,
                terminal_status="error",
                message="Loop ended with a failed action.",
                failure_class=failure_class,
                next_action_hint="stop_and_report_failure",
            )

        if has_more_commands:
            return EvaluatorOutcome(
                next_state=AgentLoopState.THINKING,
                continue_loop=True,
                terminal_status=None,
                message="Action succeeded; planning next step.",
                next_action_hint="continue_plan",
            )

        return EvaluatorOutcome(
            next_state=AgentLoopState.COMPLETED,
            continue_loop=False,
            terminal_status="ok",
            message="Plan steps completed.",
            next_action_hint="return_final_response",
        )

    @staticmethod
    def _classify_failure(outcome: ExecutionOutcome) -> str:
        status = str(outcome.status or "").strip().lower()
        message = str(outcome.summary or "").strip().lower()
        result = outcome.result if isinstance(outcome.result, dict) else {}
        result_status = str(result.get("status") or "").strip().lower()
        result_message = str(result.get("message") or "").strip().lower()
        haystack = " ".join(
            token
            for token in [status, result_status, message, result_message]
            if token
        )

        if any(token in haystack for token in ["blocked_by_policy", "policy", "blocked"]):
            return "policy_block"
        if any(
            token in haystack
            for token in [
                "needs_clarification",
                "missing",
                "needs_input",
                "missing_fields",
                "required",
                "invalid_input",
            ]
        ):
            return "missing_data"
        if any(
            token in haystack
            for token in [
                "unknown_list",
                "unknown_switch",
                "not found",
                "not_found",
                "does not exist",
            ]
        ):
            return "not_found"
        if any(
            token in haystack
            for token in [
                "timeout",
                "temporar",
                "rate limit",
                "unavailable",
                "network",
                "retry",
                "service error",
            ]
        ):
            return "transient"
        return "unknown"
