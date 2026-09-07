from __future__ import annotations

from app.core.agent_routing import AgentRoutingPolicy
from app.core.request_pipeline import ExecutionPath, RequestClassification
from app.core.types import Intent, SessionOwner


def test_agent_routing_policy_maps_historical_owner_to_main():
    policy = AgentRoutingPolicy()
    decision = policy.decide(
        intent=Intent.LIST_ADD_ITEM,
        recommended_owner=SessionOwner.MICRO,
        ambiguity_flags=[],
        missing_fields=[],
        force_main_channel=False,
        skill={"skill_id": "skill.lists.core"},
    )

    assert decision.owner == SessionOwner.MAIN
    assert decision.legacy_contract_escalation is False
    assert decision.pipeline.request_classification == RequestClassification.ACTIONABLE
    assert decision.pipeline.execution_path == ExecutionPath.SKILL


def test_agent_routing_policy_keeps_all_semantic_actions_main_owned():
    policy = AgentRoutingPolicy()
    decision = policy.decide(
        intent=Intent.HOME_SET_SWITCH,
        recommended_owner=SessionOwner.MICRO,
        ambiguity_flags=[],
        missing_fields=[],
        force_main_channel=False,
        skill={"skill_id": "skill.home.lights"},
    )

    assert decision.owner == SessionOwner.MAIN
    assert decision.legacy_contract_escalation is False
    assert decision.reasons == []
