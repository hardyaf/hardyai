from __future__ import annotations

import json

import pytest

from app.skills.authorized_executor import AuthorizedSkillExecutor, RuntimeCapabilityProjector
from app.skills.execution_dispatcher import SkillExecutionDispatcher
from app.skills.registry_service import SkillRegistryService
from app.skills.tool_contracts import (
    ToolArgumentCanonicalizationError,
    ToolDescriptor,
)


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "ok", "intent": kwargs["intent"]}

    def describe_capability(self, *, skill, context):
        self.calls.append({"skill": skill, "context": context})
        return {
            "configured": True,
            "authorized_here": True,
            "main_intents": ["lists.create_list", "not.real"],
            "intent_contracts": [
                {
                    "intent": "lists.create_list",
                    "purpose": "  Create   a list.  ",
                    "operation": "write",
                    "entity_fields": ["list_name", "../../secret", "list_name"],
                }
            ],
        }


class FakeRegistry:
    skill = {
        "skill_id": "skill.lists.core",
        "intents": ["lists.create_list"],
        "execution_ref": "app.skills.domains.lists.handler:run",
    }

    def resolve_skill(self, *, intent, user_id, agent_id):
        if intent == "lists.create_list" and user_id == "operator" and agent_id == "jarvis":
            return dict(self.skill)
        return None

    def runtime_capability_catalog(self, *, user_id, agent_id):
        return [
            {
                "skill_id": "skill.lists.core",
                "intents": ["lists.create_list", "stale.intent"],
                "micro_intents": ["lists.get_items", "stale.intent"],
            }
        ]


def test_executor_fails_closed_without_a_registry_record():
    dispatcher = FakeDispatcher()
    executor = AuthorizedSkillExecutor(skill_registry=None, dispatcher=dispatcher)

    result = executor.execute(
        intent="home.set_switch",
        entities={"switch_name": "desk", "action": "on"},
        source_interface="web",
        requested_by_user_id="operator",
        agent_id="jarvis",
        request_context={},
        request_id="request-1",
    )

    assert result["status"] == "policy_denied"
    assert result["dispatch_mode"] == "registry_only"
    assert dispatcher.calls == []


def test_executor_builds_only_allowlisted_trusted_context_fields():
    dispatcher = FakeDispatcher()
    executor = AuthorizedSkillExecutor(skill_registry=FakeRegistry(), dispatcher=dispatcher)

    result = executor.execute(
        intent="lists.create_list",
        entities={"list_name": "groceries"},
        source_interface="discord",
        requested_by_user_id="operator",
        agent_id="jarvis",
        request_context={
            "discord_channel_id": "111111111111111111",
            "identity_bound": True,
            "document_attachment_ids": ["doc-1", "", "doc-2", "doc-3", "doc-4", "doc-5"],
            "current_document_attachment_ids": ["doc-4"],
            "execution_ref": "forged",
        },
        request_id="request-2",
    )

    assert result["status"] == "ok"
    context = dispatcher.calls[0]["context"]
    assert context["request_id"] == "request-2"
    assert context["identity_bound"] is True
    assert context["document_attachment_ids"] == ["doc-1", "doc-2", "doc-3"]
    assert context["current_document_attachment_ids"] == ["doc-4"]
    assert "execution_ref" not in context


def test_capability_projection_filters_stale_intents_and_contract_fields():
    dispatcher = FakeDispatcher()
    projector = RuntimeCapabilityProjector(
        skill_registry=FakeRegistry(),
        dispatcher=dispatcher,
        main_action_intents={"lists.create_list"},
        known_intents={"lists.create_list", "lists.get_items"},
    )

    catalog = projector.project(
        user_id="operator",
        agent_id="jarvis",
        source_interface="web",
        request_context={},
    )

    assert catalog[0]["main_intents"] == ["lists.create_list"]
    assert catalog[0]["micro_intents"] == ["lists.get_items"]
    assert catalog[0]["intent_contracts"] == [
        {
            "intent": "lists.create_list",
            "purpose": "Create a list.",
            "operation": "write",
            "entity_fields": ["list_name", "secret"],
        }
    ]


def _typed_descriptor(
    *,
    tool_id: str,
    effect: str = "read",
    runtime_dependencies: list[str] | None = None,
) -> ToolDescriptor:
    return ToolDescriptor.from_mapping(
        {
            "tool_id": tool_id,
            "skill_id": "skill.fixture.core",
            "contract_version": 1,
            "purpose": f"Use the bounded {tool_id} fixture.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["target_ref"],
                "properties": {
                    "target_ref": {"type": "string", "minLength": 1, "maxLength": 80},
                    "targets": {
                        "type": "array",
                        "minItems": 0,
                        "maxItems": 8,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1, "maxLength": 80},
                    },
                },
            },
            "observation_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["status"],
                "properties": {
                    "status": {"type": "string", "enum": ["ok"]},
                },
            },
            "effect": effect,
            "approval_rule": "none",
            "approval_conditions": [],
            "sensitivity": "private",
            "persistence": "redacted",
            "idempotency": "not_applicable" if effect == "read" else "required",
            "effect_cardinality": "single",
            "transferable_observation_fields": [],
            "runtime_dependencies": runtime_dependencies or [],
            "timeout_seconds": 10,
            "max_result_items": 1,
            "max_observation_chars": 1000,
            "legacy_intents": [],
            "interactive": True,
        }
    )


class _TypedCatalog:
    def __init__(self, descriptors: list[ToolDescriptor]) -> None:
        self.skill = {
            "skill_id": "skill.fixture.core",
            "skill_name": "Fixture",
            "skill_user": "all",
            "skill_agents": ["all"],
            "intents": [],
            "execution_ref": "app.skills.domains.lists.handler:run",
            "storage_ref": "provider-secret-storage",
            "main_tools": [item.to_storage_dict() for item in descriptors],
            "main_tools_contract_version": 1,
            "active": True,
        }

    def list_skills(self, *, active_only: bool = True) -> list[dict]:
        return [dict(self.skill)] if active_only else [dict(self.skill)]


class _TypedDomainHandler:
    def __init__(self) -> None:
        self.mode = "resolve"
        self.calls = []

    def canonicalize_tool_arguments(self, *, tool_id, validated_arguments, request_context):
        del tool_id, request_context
        if self.mode in {"stale", "ambiguous", "unauthorized"}:
            raise ToolArgumentCanonicalizationError(f"canonicalizer_{self.mode}")
        if self.mode == "mutate":
            validated_arguments["target_ref"] = "mutated"
            return validated_arguments
        if self.mode == "schema_change":
            return {**validated_arguments, "provider_setting": "forged"}
        if self.mode == "provider_object":
            return {"target_ref": object()}
        if self.mode == "duplicate":
            return {"target_ref": "resource-1", "targets": ["one", "one"]}
        return {
            **validated_arguments,
            "target_ref": "resource-1"
            if validated_arguments["target_ref"] == "alias"
            else validated_arguments["target_ref"],
        }

    def execute_tool(self, *, envelope, services):
        self.calls.append((envelope, services))
        return {
            "status": "ok",
            "operation_id": envelope.operation_id,
            "arguments": dict(envelope.arguments),
        }


class _TypedDispatcher(SkillExecutionDispatcher):
    def __init__(self, handler: _TypedDomainHandler) -> None:
        super().__init__(domain_handlers={"skill.fixture.core": handler})
        self.authorized = True
        self.deny_on_describe_call: int | None = None
        self.describe_calls = 0

    def describe_capability(self, *, skill, context):
        del skill, context
        self.describe_calls += 1
        authorized = self.authorized and self.describe_calls != self.deny_on_describe_call
        return {
            "configured": True,
            "authorized_here": authorized,
            "availability": "available" if authorized else "denied",
            "access_note": (
                "Available in private provider settings /etc/private for 111111111111111111"
            ),
        }


def _typed_executor(
    descriptors: list[ToolDescriptor],
    *,
    mode: str,
    domains: tuple[str, ...],
    operations: tuple[str, ...],
) -> tuple[AuthorizedSkillExecutor, _TypedDispatcher, _TypedDomainHandler]:
    registry = SkillRegistryService(_TypedCatalog(descriptors))
    handler = _TypedDomainHandler()
    dispatcher = _TypedDispatcher(handler)
    executor = AuthorizedSkillExecutor(
        skill_registry=registry,
        dispatcher=dispatcher,
        execution_mode=mode,
        enabled_domains=domains,
        enabled_operations=operations,
        max_selected_skills=3,
    )
    return executor, dispatcher, handler


def _typed_context() -> dict:
    return {
        "requested_by_user_id": "operator",
        "user_id": "operator",
        "agent_id": "jarvis",
        "source_interface": "discord",
        "discord_channel_id": "111111111111111111",
        "session_id": "session-1",
        "principal_kind": "discord_user",
        "principal_subject": "subject-1",
    }


def test_effective_tools_requires_both_exact_rollout_allowlists_and_keeps_reads() -> None:
    read = _typed_descriptor(tool_id="fixture.read")
    write = _typed_descriptor(tool_id="fixture.write", effect="local_write")

    for domains, operations in (
        (("fixture",), ()),
        ((), ("fixture.read",)),
    ):
        executor, _, _ = _typed_executor(
            [read, write],
            mode="shadow",
            domains=domains,
            operations=operations,
        )
        assert executor.effective_tools(["skill.fixture.core"], _typed_context()) == []

    executor, _, _ = _typed_executor(
        [read, write],
        mode="shadow",
        domains=("fixture",),
        operations=("fixture.read",),
    )
    projection = executor.effective_tools(["skill.fixture.core"], _typed_context())
    assert [item["tool_id"] for item in projection] == ["fixture.read"]
    serialized = json.dumps(projection, sort_keys=True)
    for forbidden in (
        "execution_ref",
        "storage_ref",
        "provider-secret-storage",
        "runtime_dependencies",
        "principal_subject",
        "/etc/private",
        "111111111111111111",
    ):
        assert forbidden not in serialized


def test_effective_tools_enforces_selected_skill_cap_and_runtime_dependencies() -> None:
    descriptor = _typed_descriptor(
        tool_id="fixture.read",
        runtime_dependencies=["document_processing"],
    )
    executor, _, _ = _typed_executor(
        [descriptor],
        mode="shadow",
        domains=("fixture",),
        operations=("fixture.read",),
    )
    assert executor.effective_tools(["skill.fixture.core"], _typed_context()) == []
    context = {**_typed_context(), "available_runtime_dependencies": ["document_processing"]}
    assert executor.effective_tools(["skill.fixture.core"], context)[0]["tool_id"] == (
        "fixture.read"
    )
    assert executor.effective_tools(
        ["skill.fixture.core", "skill.two", "skill.three", "skill.four"],
        context,
    ) == []


def test_typed_execution_canonicalizes_before_identity_and_dispatches_generic_domain() -> None:
    descriptor = _typed_descriptor(tool_id="fixture.read")
    executor, _, handler = _typed_executor(
        [descriptor],
        mode="active",
        domains=("fixture",),
        operations=("fixture.read",),
    )
    result = executor.execute_tool(
        tool_id="fixture.read",
        contract_version=1,
        arguments={"target_ref": "alias"},
        source_interface="discord",
        requested_by_user_id="operator",
        agent_id="jarvis",
        request_context=_typed_context(),
        request_id="delivery-1",
        call_ordinal=1,
    )
    assert result["status"] == "ok"
    assert result["arguments"] == {"target_ref": "resource-1"}
    assert result["operation_id"].startswith("toolop_v1_")
    assert len(handler.calls) == 1
    envelope = handler.calls[0][0]
    assert envelope.arguments == {"target_ref": "resource-1"}
    assert "alias" not in envelope.arguments_hash


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("stale", "canonicalizer_stale"),
        ("ambiguous", "canonicalizer_ambiguous"),
        ("unauthorized", "canonicalizer_unauthorized"),
        ("mutate", "canonicalizer_mutated_arguments"),
        ("schema_change", "arguments_unknown_field"),
        ("provider_object", "canonicalizer_result_invalid"),
        ("duplicate", "arguments_array_duplicate"),
    ],
)
def test_canonicalization_failures_stop_before_operation_identity_and_dispatch(
    mode: str,
    reason: str,
) -> None:
    descriptor = _typed_descriptor(tool_id="fixture.read")
    executor, _, handler = _typed_executor(
        [descriptor],
        mode="active",
        domains=("fixture",),
        operations=("fixture.read",),
    )
    handler.mode = mode
    result = executor.execute_tool(
        tool_id="fixture.read",
        contract_version=1,
        arguments={"target_ref": "alias"},
        source_interface="discord",
        requested_by_user_id="operator",
        agent_id="jarvis",
        request_context=_typed_context(),
        request_id="delivery-2",
        call_ordinal=1,
    )
    assert result["status"] == "policy_denied"
    assert result["denial_reason"] == reason
    assert "operation_id" not in result
    assert handler.calls == []


def test_unknown_stale_and_changed_authorization_fail_before_dispatch() -> None:
    descriptor = _typed_descriptor(tool_id="fixture.read")
    executor, dispatcher, handler = _typed_executor(
        [descriptor],
        mode="active",
        domains=("fixture",),
        operations=("fixture.read",),
    )
    unknown = executor.execute_tool(
        tool_id="fixture.unknown",
        contract_version=1,
        arguments={"target_ref": "alias"},
        source_interface="discord",
        requested_by_user_id="operator",
        agent_id="jarvis",
        request_context=_typed_context(),
        request_id="delivery-3",
        call_ordinal=1,
    )
    stale = executor.execute_tool(
        tool_id="fixture.read",
        contract_version=2,
        arguments={"target_ref": "alias"},
        source_interface="discord",
        requested_by_user_id="operator",
        agent_id="jarvis",
        request_context=_typed_context(),
        request_id="delivery-4",
        call_ordinal=1,
    )
    assert unknown["denial_reason"] == "tool_unknown_or_unauthorized"
    assert stale["denial_reason"] == "tool_contract_stale"

    dispatcher.describe_calls = 0
    dispatcher.deny_on_describe_call = 2
    changed = executor.execute_tool(
        tool_id="fixture.read",
        contract_version=1,
        arguments={"target_ref": "alias"},
        source_interface="discord",
        requested_by_user_id="operator",
        agent_id="jarvis",
        request_context=_typed_context(),
        request_id="delivery-5",
        call_ordinal=1,
    )
    assert changed["denial_reason"] == "tool_authorization_changed"
    assert handler.calls == []
