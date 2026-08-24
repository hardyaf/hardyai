from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.request_pipeline import JarvisRequestPipeline, PipelineDecision
from app.core.state_machine import choose_owner_for_intent
from app.core.types import FAST_COMMAND_INTENTS, Intent, SessionOwner

if TYPE_CHECKING:
    from app.skills.registry_service import SkillRegistryService


@dataclass(frozen=True)
class AgentRoutingDecision:
    owner: SessionOwner
    pipeline: PipelineDecision
    reasons: list[str]
    channel_forced_main: bool
    micro_contract_escalation: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "owner": self.owner.value,
            "pipeline": self.pipeline.to_dict(),
            "reasons": list(self.reasons),
            "channel_forced_main": self.channel_forced_main,
            "micro_contract_escalation": self.micro_contract_escalation,
        }


class AgentRoutingPolicy:
    def __init__(
        self,
        *,
        pipeline: JarvisRequestPipeline | None = None,
        skill_registry: "SkillRegistryService | None" = None,
    ) -> None:
        self._pipeline = pipeline or JarvisRequestPipeline()
        self._skill_registry = skill_registry

    def decide(
        self,
        *,
        intent: Intent,
        recommended_owner: SessionOwner,
        ambiguity_flags: list[str],
        missing_fields: list[str],
        force_main_channel: bool,
        skill: dict[str, object] | None,
    ) -> AgentRoutingDecision:
        owner = choose_owner_for_intent(
            intent=intent,
            recommended_owner=recommended_owner,
        )
        reasons: list[str] = []
        channel_forced_main = False
        micro_contract_escalation = False

        if (
            force_main_channel
            and intent not in {Intent.SYSTEM_WAKE, Intent.SYSTEM_SLEEP}
            and owner != SessionOwner.MAIN
        ):
            owner = SessionOwner.MAIN
            channel_forced_main = True
            reasons.append("channel_force_main_owner")

        if (
            owner == SessionOwner.MICRO
            and intent in FAST_COMMAND_INTENTS
            and self._skill_registry is not None
            and not self._skill_registry.is_micro_allowed_for_intent(
                skill=skill,
                intent=intent.value,
            )
        ):
            owner = SessionOwner.MAIN
            micro_contract_escalation = True
            reasons.append("micro_contract_escalation")

        pipeline = self._pipeline.classify(
            intent=intent,
            owner=owner,
            missing_fields=missing_fields,
            ambiguity_flags=ambiguity_flags,
        )
        return AgentRoutingDecision(
            owner=owner,
            pipeline=pipeline,
            reasons=reasons,
            channel_forced_main=channel_forced_main,
            micro_contract_escalation=micro_contract_escalation,
        )
