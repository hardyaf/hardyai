from __future__ import annotations

from app.skills.authorized_executor import AuthorizedSkillExecutor, RuntimeCapabilityProjector


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
