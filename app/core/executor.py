from __future__ import annotations

from typing import Any, Callable

from app.core.agent_loop_types import AgentLoopActionType, ExecutionOutcome, PlannerDecision
from app.core.micro_jarvis import MicroDecision, MicroJarvis
from app.core.types import FAST_COMMAND_INTENTS, Intent, SessionOwner, SessionState


class MainAgentExecutor:
    """Executes one planner-selected action at a time."""

    def __init__(
        self,
        *,
        micro_jarvis: MicroJarvis,
        run_fast_command: Callable[[MicroDecision, PlannerDecision], dict[str, Any]],
    ) -> None:
        self._micro_jarvis = micro_jarvis
        self._run_fast_command = run_fast_command

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
            classification = self._micro_jarvis.interpret(
                text=decision.command_text,
                context={
                    "session_state": SessionState.CONVERSATIONAL.value,
                    "session_owner": SessionOwner.MAIN.value,
                    "execution_origin": "main_agent_loop",
                    "agent_id": agent_id,
                },
            )
        classification_payload = classification.to_dict()
        if classification.intent not in FAST_COMMAND_INTENTS:
            message = "Main plan command did not resolve to a fast command."
            return ExecutionOutcome(
                status="error",
                success=False,
                summary=message,
                result={"status": "error", "message": message},
                classification=classification_payload,
                intent=classification.intent.value,
            )

        result = self._run_fast_command(classification, decision)
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
    def _typed_plan_classification(decision: PlannerDecision) -> MicroDecision | None:
        intent_value = str(decision.metadata.get("intent") or "").strip()
        entities = decision.metadata.get("entities")
        if not intent_value or not isinstance(entities, dict):
            return None
        try:
            intent = Intent(intent_value)
        except ValueError:
            return None
        if intent not in FAST_COMMAND_INTENTS:
            return None
        confidence_raw = decision.metadata.get("confidence")
        confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) else 0.95
        return MicroDecision(
            intent=intent,
            confidence=max(0.0, min(confidence, 1.0)),
            entities={str(key): value for key, value in entities.items()},
            ambiguity_flags=[],
            recommended_owner=SessionOwner.MAIN,
            reasoning="main_plan_explicit_command_contract",
        )
