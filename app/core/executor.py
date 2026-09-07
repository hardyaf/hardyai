from __future__ import annotations

from typing import Any, Callable

from app.core.agent_loop_types import AgentLoopActionType, ExecutionOutcome, PlannerDecision
from app.core.types import LEGACY_ACTION_INTENTS, Intent, RoutingDecision, SessionOwner


class MainAgentExecutor:
    """Executes one planner-selected action at a time."""

    def __init__(
        self,
        *,
        run_compatibility_action: Callable[[RoutingDecision, PlannerDecision], dict[str, Any]],
    ) -> None:
        self._run_compatibility_action = run_compatibility_action

    def execute(self, decision: PlannerDecision, *, agent_id: str) -> ExecutionOutcome:
        if decision.action_type == AgentLoopActionType.REQUEST_APPROVAL:
            return ExecutionOutcome(
                status="waiting_for_approval",
                success=False,
                summary="Action requires approval before execution.",
            )
        if decision.action_type == AgentLoopActionType.REQUEST_USER_INPUT:
            return ExecutionOutcome(
                status="waiting_for_user",
                success=False,
                summary="Action requires additional user input.",
            )
        if decision.action_type == AgentLoopActionType.COMPLETE:
            return ExecutionOutcome(status="completed", success=True, summary="No further actions needed.")
        if decision.action_type == AgentLoopActionType.FAIL:
            reason = str(decision.metadata.get("reason") or "Planner reported a loop failure.")
            return ExecutionOutcome(status="error", success=False, summary=reason)

        classification = self._typed_plan_classification(decision)
        if classification is None:
            message = "Main plan command is missing its typed compatibility contract."
            return ExecutionOutcome(
                status="error",
                success=False,
                summary=message,
                result={"status": "error", "message": message},
            )
        classification_payload = classification.to_dict()
        if classification.intent not in LEGACY_ACTION_INTENTS:
            message = "Main plan command did not resolve to an allowed compatibility action."
            return ExecutionOutcome(
                status="error",
                success=False,
                summary=message,
                result={"status": "error", "message": message},
                classification=classification_payload,
                intent=classification.intent.value,
            )

        result = self._run_compatibility_action(classification, decision)
        status = str(result.get("status") or "error")
        success = status == "ok"
        summary = str(result.get("message") or "").strip() or f"{classification.intent.value} -> {status}"
        return ExecutionOutcome(
            status=status,
            success=success,
            summary=summary,
            result=result,
            classification=classification_payload,
            intent=classification.intent.value,
            tool_name=classification.intent.value,
        )

    @staticmethod
    def _typed_plan_classification(decision: PlannerDecision) -> RoutingDecision | None:
        intent_value = str(decision.metadata.get("intent") or "").strip()
        entities = decision.metadata.get("entities")
        if not intent_value or not isinstance(entities, dict):
            return None
        try:
            intent = Intent(intent_value)
        except ValueError:
            return None
        if intent not in LEGACY_ACTION_INTENTS:
            return None
        confidence_raw = decision.metadata.get("confidence")
        confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) else 0.95
        return RoutingDecision(
            intent=intent,
            confidence=max(0.0, min(confidence, 1.0)),
            entities={str(key): value for key, value in entities.items()},
            ambiguity_flags=[],
            recommended_owner=SessionOwner.MAIN,
            reasoning="main_plan_explicit_command_contract",
        )
