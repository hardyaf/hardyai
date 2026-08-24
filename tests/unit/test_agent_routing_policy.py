from __future__ import annotations

from app.core.agent_routing import AgentRoutingPolicy
from app.core.request_pipeline import ExecutionPath, RequestClassification
from app.core.types import Intent, SessionOwner


class _RegistryStub:
    def __init__(self, *, micro_allowed: bool) -> None:
        self._micro_allowed = micro_allowed

    def is_micro_allowed_for_intent(self, *, skill: object, intent: str) -> bool:
        return self._micro_allowed


def test_agent_routing_policy_keeps_micro_owner_for_allowed_fast_intent():
    policy = AgentRoutingPolicy(skill_registry=_RegistryStub(micro_allowed=True))
    decision = policy.decide(
        intent=Intent.LIST_ADD_ITEM,
        recommended_owner=SessionOwner.MICRO,
        ambiguity_flags=[],
        missing_fields=[],
        force_main_channel=False,
        skill={"skill_id": "skill.lists.core"},
    )

    assert decision.owner == SessionOwner.MICRO
    assert decision.micro_contract_escalation is False
    assert decision.pipeline.request_classification == RequestClassification.ACTIONABLE
    assert decision.pipeline.execution_path == ExecutionPath.SKILL


def test_agent_routing_policy_escalates_when_micro_contract_disallows_intent():
    policy = AgentRoutingPolicy(skill_registry=_RegistryStub(micro_allowed=False))
    decision = policy.decide(
        intent=Intent.HOME_SET_SWITCH,
        recommended_owner=SessionOwner.MICRO,
        ambiguity_flags=[],
        missing_fields=[],
        force_main_channel=False,
        skill={"skill_id": "skill.home.lights"},
    )

    assert decision.owner == SessionOwner.MAIN
    assert decision.micro_contract_escalation is True
    assert "micro_contract_escalation" in decision.reasons
