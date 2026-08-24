from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from app.core.types import EMAIL_AGENT_INTENTS, FAST_COMMAND_INTENTS, Intent, SessionOwner


NON_BLOCKING_AMBIGUITY_FLAGS = {
    "short",
    "resolved_via_main_repair",
    "list_reference_resolved_from_context",
    "switch_reference_resolved_from_context",
    "main_sticky_followup",
}


class RequestClassification(str, Enum):
    INFORMATIONAL = "informational"
    ACTIONABLE = "actionable"
    ORCHESTRATION_REQUIRED = "orchestration_required"


class ExecutionPath(str, Enum):
    DIRECT_RESPONSE = "direct_response"
    SKILL = "skill"
    AGENT = "agent"


@dataclass(frozen=True)
class PipelineDecision:
    request_classification: RequestClassification
    execution_path: ExecutionPath
    requires_validation: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "request_classification": self.request_classification.value,
            "execution_path": self.execution_path.value,
            "requires_validation": self.requires_validation,
            "reason": self.reason,
        }


class JarvisRequestPipeline:
    def classify(
        self,
        *,
        intent: Intent,
        owner: SessionOwner,
        missing_fields: Iterable[str] | None = None,
        ambiguity_flags: Iterable[str] | None = None,
    ) -> PipelineDecision:
        normalized_missing_fields = [
            str(item).strip().lower()
            for item in (missing_fields or [])
            if str(item).strip()
        ]
        normalized_ambiguity = {
            str(item).strip().lower()
            for item in (ambiguity_flags or [])
            if str(item).strip()
        }
        blocking_ambiguity = normalized_ambiguity - NON_BLOCKING_AMBIGUITY_FLAGS

        if intent in {Intent.SYSTEM_WAKE, Intent.SYSTEM_SLEEP}:
            return PipelineDecision(
                request_classification=RequestClassification.ACTIONABLE,
                execution_path=ExecutionPath.DIRECT_RESPONSE,
                requires_validation=True,
                reason="system_power_control",
            )

        if intent in {Intent.CONVERSATIONAL, Intent.UNKNOWN}:
            return PipelineDecision(
                request_classification=RequestClassification.INFORMATIONAL,
                execution_path=ExecutionPath.DIRECT_RESPONSE,
                requires_validation=True,
                reason="informational_or_unstructured_request",
            )

        if intent in FAST_COMMAND_INTENTS and owner == SessionOwner.MICRO:
            return PipelineDecision(
                request_classification=RequestClassification.ACTIONABLE,
                execution_path=ExecutionPath.SKILL,
                requires_validation=True,
                reason="micro_deterministic_skill_execution",
            )

        if intent in FAST_COMMAND_INTENTS and owner == SessionOwner.MAIN:
            if normalized_missing_fields or blocking_ambiguity:
                return PipelineDecision(
                    request_classification=RequestClassification.ORCHESTRATION_REQUIRED,
                    execution_path=ExecutionPath.AGENT,
                    requires_validation=True,
                    reason="main_needed_for_repair_or_clarification",
                )
            return PipelineDecision(
                request_classification=RequestClassification.ACTIONABLE,
                execution_path=ExecutionPath.SKILL,
                requires_validation=True,
                reason="main_skill_execution",
            )

        if intent in EMAIL_AGENT_INTENTS and owner == SessionOwner.MAIN:
            return PipelineDecision(
                request_classification=RequestClassification.INFORMATIONAL,
                execution_path=ExecutionPath.SKILL,
                requires_validation=True,
                reason="main_sensitive_email_skill_execution",
            )

        return PipelineDecision(
            request_classification=RequestClassification.ORCHESTRATION_REQUIRED,
            execution_path=ExecutionPath.AGENT,
            requires_validation=True,
            reason="default_main_orchestration",
        )
