from __future__ import annotations

from typing import Any

from app.skills.execution_dispatcher import SkillExecutionDispatcher
from app.skills.tool_contracts import ToolCallEnvelope, ToolDescriptor


class _FakeHomeService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def set_switch(
        self,
        *,
        switch_name: str,
        action: str,
        source_interface: str | None,
        requested_by_user_id: str | None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "switch_name": switch_name,
                "action": action,
                "source_interface": source_interface,
                "requested_by_user_id": requested_by_user_id,
            }
        )
        return {
            "status": "ok",
            "switch_name": switch_name,
            "state": action,
        }


def test_execution_dispatcher_runs_safe_execution_ref():
    fake_home = _FakeHomeService()
    dispatcher = SkillExecutionDispatcher(
        lists_service=object(),
        calendar_service=object(),
        home_service=fake_home,
    )
    skill = {"execution_ref": "app.skills.domains.lights.handler:run"}

    result = dispatcher.execute(
        skill=skill,
        intent="home.set_switch",
        entities={"switch_name": "desk", "action": "on"},
        context={"source_interface": "web", "requested_by_user_id": "jordan"},
    )

    assert isinstance(result, dict)
    assert result["status"] == "ok"
    assert fake_home.calls
    assert fake_home.calls[0]["switch_name"] == "desk"
    assert fake_home.calls[0]["action"] == "on"
    assert fake_home.calls[0]["source_interface"] == "web"
    assert fake_home.calls[0]["requested_by_user_id"] == "jordan"


def test_execution_dispatcher_rejects_unsafe_execution_ref():
    dispatcher = SkillExecutionDispatcher(
        lists_service=object(),
        calendar_service=object(),
        home_service=object(),
    )
    skill = {"execution_ref": "os.system:run"}

    result = dispatcher.execute(
        skill=skill,
        intent="home.set_switch",
        entities={"switch_name": "desk", "action": "on"},
        context={},
    )

    assert result is None


def test_execution_dispatcher_runs_email_only_through_domain_handler():
    class FakeEmailAgent:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, **kwargs):
            self.calls.append(kwargs)
            return {"status": "ok", "message": "E1 - fixture"}

    email = FakeEmailAgent()
    dispatcher = SkillExecutionDispatcher(
        lists_service=object(),
        calendar_service=object(),
        home_service=object(),
        email_agent_service=email,
    )

    result = dispatcher.execute(
        skill={"execution_ref": "app.skills.domains.email_agent.handler:run"},
        intent="email.list_recent",
        entities={"query": "recent email"},
        context={"source_interface": "discord", "identity_bound": True},
    )

    assert result == {
        "status": "ok",
        "message": "E1 - fixture",
        "_persistence_policy": "sensitive_domain",
    }
    assert email.calls[0]["intent"] == "email.list_recent"
    assert email.calls[0]["context"]["identity_bound"] is True


def _typed_descriptor(skill_id: str, tool_id: str) -> ToolDescriptor:
    return ToolDescriptor.from_mapping(
        {
            "tool_id": tool_id,
            "skill_id": skill_id,
            "contract_version": 1,
            "purpose": "Execute one synthetic consumer contract.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["value"],
                "properties": {
                    "value": {"type": "string", "minLength": 1, "maxLength": 40}
                },
            },
            "observation_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["status"],
                "properties": {"status": {"type": "string", "enum": ["ok"]}},
            },
            "effect": "read",
            "approval_rule": "none",
            "approval_conditions": [],
            "sensitivity": "normal",
            "persistence": "standard",
            "idempotency": "not_applicable",
            "effect_cardinality": "single",
            "transferable_observation_fields": [],
            "runtime_dependencies": [],
            "timeout_seconds": 5,
            "max_result_items": 1,
            "max_observation_chars": 200,
            "legacy_intents": [],
            "interactive": True,
        }
    )


def _typed_envelope(descriptor: ToolDescriptor, request_id: str) -> ToolCallEnvelope:
    return ToolCallEnvelope.create(
        root_request_id=request_id,
        call_ordinal=1,
        session_id="session-1",
        principal_kind="user",
        principal_subject="operator",
        user_id="operator",
        agent_id="jarvis",
        source_interface="test",
        channel_scope="test",
        skill_id=descriptor.skill_id,
        descriptor=descriptor,
        authorization_snapshot_ref="authz-fixture",
        validated_arguments=descriptor.validate_arguments({"value": "one"}),
    )


def test_typed_dispatcher_supports_a_second_consumer_without_registry_branches() -> None:
    class SyntheticDomain:
        def __init__(self, name: str) -> None:
            self.name = name
            self.calls = []

        def execute_tool(self, *, envelope, services):
            self.calls.append((envelope, services))
            return {"status": "ok", "consumer": self.name, "tool_id": envelope.tool_id}

    first = SyntheticDomain("first")
    second = SyntheticDomain("second")
    dispatcher = SkillExecutionDispatcher(
        domain_handlers={
            "skill.fixture.first": first,
            "skill.fixture.second": second,
        }
    )
    first_descriptor = _typed_descriptor("skill.fixture.first", "first.read")
    second_descriptor = _typed_descriptor("skill.fixture.second", "second.read")

    first_result = dispatcher.execute_tool(_typed_envelope(first_descriptor, "request-1"))
    second_result = dispatcher.execute_tool(_typed_envelope(second_descriptor, "request-2"))
    validated = first_descriptor.validate_arguments({"value": "unchanged"})
    canonical = dispatcher.canonicalize_tool_arguments(
        skill={"skill_id": "skill.fixture.first"},
        descriptor=first_descriptor,
        arguments=validated,
        context={"requested_by_user_id": "operator"},
    )

    assert first_result == {"status": "ok", "consumer": "first", "tool_id": "first.read"}
    assert second_result == {"status": "ok", "consumer": "second", "tool_id": "second.read"}
    assert canonical == {"value": "unchanged"}
    assert len(first.calls) == len(second.calls) == 1
