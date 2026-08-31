from __future__ import annotations

from typing import Any

from app.core.main_jarvis import MainJarvis
from app.core.micro_jarvis import MicroJarvis
from tests.router_support import PermissiveTestSkillRegistry, RegistryBackedTestRouter as JarvisRouter
from app.core.session_store import SessionStore
from app.core.state_machine import RuntimePowerController
from app.schemas.api import AskRequest
from app.services.event_log import EventLogService
from app.tools.calendar_service import CalendarService
from app.tools.home_service import HomeService
from app.tools.lists_service import ListsService


class _RecordingMicroBackend:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def classify(self, text: str, context=None):
        self.calls.append({"text": text, "context": dict(context or {})})
        return self.payload


class _RecordingRepairBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def repair_action(self, text: str, context=None):
        self.calls.append({"text": text, "context": dict(context or {})})
        return {
            "status": "not_actionable",
            "confidence": 0.0,
            "reasoning": "model_no_supported_action",
            "entities": {},
            "missing_fields": [],
            "message": "I could not map that request to a supported action yet.",
            "source": "backend",
        }


class _RecordingConversationBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def respond(self, text: str, context=None):
        self.calls.append({"text": text, "context": dict(context or {})})
        return "Understood. We can try the forwarding setup again tomorrow."


class _CapabilityRegistry:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self._skills = {
            "skill.lists.core": {
                "skill_id": "skill.lists.core",
                "skill_name": "Lists",
                "intents": ["lists.add_item", "lists.get_items", "lists.create_list"],
                "execution_ref": "app.skills.domains.lists.handler:run",
                "micro_enabled": True,
                "micro_intents": ["lists.add_item", "lists.get_items", "lists.nonexistent"],
            },
            "skill.email.agent": {
                "skill_id": "skill.email.agent",
                "skill_name": "Shared Email Agent",
                "intents": ["email.list_recent", "email.summarize", "email.discuss"],
                "execution_ref": "app.skills.domains.email_agent.handler:run",
                "micro_enabled": False,
                "micro_intents": [],
            },
        }

    def resolve_agent_context(self, *, text: str, fallback_user_id: str, fallback_agent_id: str):
        return {
            "agent_id": fallback_agent_id,
            "display_name": fallback_agent_id,
            "wake_alias": None,
            "normalized_text": text,
            "resolved_user_id": fallback_user_id,
            "personality_doc_path": None,
        }

    def resolve_skill(self, *, intent: str, user_id: str, agent_id: str):
        del user_id, agent_id
        for skill in self._skills.values():
            if intent in skill["intents"]:
                return dict(skill)
        return None

    def runtime_capability_catalog(self, *, user_id: str, agent_id: str):
        del user_id, agent_id
        return [
            {
                key: value
                for key, value in skill.items()
                if key != "execution_ref"
            }
            for skill in self._skills.values()
        ]

    def record_skill_run(self, **kwargs):
        self.runs.append(dict(kwargs))


class _ScopedEmailService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def _authorized(context: dict[str, Any]) -> bool:
        scopes = {
            str(item or "").strip().casefold()
            for item in context.get("skill_scopes") or []
        }
        return "skill.email.agent" in scopes

    def capability_access(self, *, context: dict[str, Any]):
        contracts = [
            {
                "intent": "email.list_recent",
                "purpose": "List or summarize a collection of recent messages.",
                "operation": "read",
                "entity_fields": ["query", "query", "bad field!"],
                "execution_ref": "must-not-leak",
            },
            {
                "intent": "email.sync",
                "purpose": "Must be filtered because it is scheduler-owned.",
                "operation": "write",
                "entity_fields": [],
            },
        ]
        if self._authorized(context):
            return {
                "configured": True,
                "authorized_here": True,
                "availability": "available",
                "access_note": "The shared email agent is available in this private channel.",
                "intent_contracts": contracts,
            }
        return {
            "configured": True,
            "authorized_here": False,
            "availability": "restricted",
            "access_note": "The shared email agent is available only in an authorized private email channel.",
            "intent_contracts": contracts,
        }

    def execute(self, *, intent: str, entities: dict[str, Any], context: dict[str, Any]):
        self.calls.append({"intent": intent, "entities": dict(entities), "context": dict(context)})
        if not self._authorized(context):
            return {
                "status": "policy_denied",
                "message": "The shared email agent is available only in an authorized private email channel.",
            }
        return {
            "status": "ok",
            "message": "E1 - Authorized email summary",
            "result_count": 1,
        }


def _build_router(
    *,
    micro_backend: _RecordingMicroBackend,
    repair_backend: Any = None,
    conversation_backend: Any = None,
    skill_registry: Any = None,
    email_agent_service: Any = None,
    main_tool_execution_mode: str = "off",
    main_tool_enabled_domains: tuple[str, ...] = (),
    main_tool_enabled_operations: tuple[str, ...] = (),
    legacy_micro_routing_enabled: bool = True,
) -> tuple[JarvisRouter, EventLogService]:
    event_log = EventLogService()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(
            backend=micro_backend,
            heuristic_fallback_enabled=False,
        ),
        main_jarvis=MainJarvis(
            repair_backend=repair_backend,
            conversation_backend=conversation_backend,
        ),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=event_log,
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["kitchen light"]),
        skill_registry=skill_registry or PermissiveTestSkillRegistry(),
        email_agent_service=email_agent_service,
        main_tool_model=conversation_backend,
        main_tool_execution_mode=main_tool_execution_mode,
        main_tool_enabled_domains=main_tool_enabled_domains,
        main_tool_enabled_operations=main_tool_enabled_operations,
        legacy_micro_routing_enabled=legacy_micro_routing_enabled,
    )
    return router, event_log


def _discord_request(*, text: str, explicit: bool, session_id: str) -> AskRequest:
    return AskRequest(
        text=text,
        session_id=session_id,
        user_id="discord-jordan",
        source="discord",
        context={
            "micro_command_explicit": explicit,
            "auto_channel_session": True,
            "session_channel": "discord.guild.123.channel.456",
            "channel_session_scope": "per_user",
            "agent_id": "jarvis",
            "agent_display_name": "Jarvis",
        },
    )


def test_unprefixed_discord_statement_bypasses_micro_and_reaches_main():
    micro_backend = _RecordingMicroBackend(
        payload={
            "intent": "email.discuss",
            "confidence": 0.99,
            "entities": {"reference": "that"},
            "ambiguity_flags": [],
            "reasoning": "would_be_false_email_match",
        }
    )
    repair_backend = _RecordingRepairBackend()
    conversation_backend = _RecordingConversationBackend()
    router, event_log = _build_router(
        micro_backend=micro_backend,
        repair_backend=repair_backend,
        conversation_backend=conversation_backend,
    )

    response = router.route(
        _discord_request(
            text="This was my mistake, I had email forwarding set up wrong, we will try again tomorrow",
            explicit=False,
            session_id="discord-main-only",
        )
    )

    assert micro_backend.calls == []
    assert response["route"] == "main_jarvis"
    assert response["owner"] == "main_jarvis"
    assert response["intent"] == "unknown"
    assert response["result"]["status"] == "conversation"
    assert response["classification"]["reasoning"] == "discord_unprefixed_main_handoff"
    assert "micro_bypassed_unprefixed_discord" in response["classification"]["ambiguity_flags"]
    assert repair_backend.calls == []
    assert len(conversation_backend.calls) == 1
    main_context = conversation_backend.calls[0]["context"]
    assert main_context["micro_intent"] == "unknown"
    assert main_context["micro_confidence"] == 0.0
    assert main_context["micro_entities"] == {}
    assert main_context["micro_ambiguity_flags"] == ["micro_bypassed_unprefixed_discord"]
    assert main_context["required_missing_fields"] == []
    assert main_context["agent_id"] == "jarvis"
    assert main_context["agent_display_name"] == "jarvis"
    assert isinstance(main_context["session_summary"], dict)
    event_types = [item["event_type"] for item in event_log.recent(limit=50)]
    assert "pipeline.micro.bypassed" in event_types
    assert "pipeline.main_repair.bypassed" in event_types


def test_explicit_bang_discord_command_enters_micro():
    micro_backend = _RecordingMicroBackend(
        payload={
            "intent": "calendar.view",
            "confidence": 0.96,
            "entities": {"date_hint": "today"},
            "ambiguity_flags": [],
            "reasoning": "explicit_calendar_command",
        }
    )
    router, event_log = _build_router(micro_backend=micro_backend)

    response = router.route(
        _discord_request(
            text="what is on my calendar today",
            explicit=True,
            session_id="discord-explicit-micro",
        )
    )

    assert len(micro_backend.calls) == 1
    assert response["intent"] == "calendar.view"
    assert response["owner"] == "micro_jarvis"
    assert response["route"] == "micro_tool"
    assert response["result"]["status"] == "ok"
    event_types = [item["event_type"] for item in event_log.recent(limit=50)]
    assert "pipeline.micro.bypassed" not in event_types


def test_unresolved_bang_command_carries_full_micro_handoff_to_main():
    micro_backend = _RecordingMicroBackend(
        payload={
            "intent": "unknown",
            "confidence": 0.31,
            "entities": {"candidate_text": "frobnicate that"},
            "ambiguity_flags": ["unknown_intent", "deictic_reference"],
            "reasoning": "explicit_command_ambiguous",
        }
    )
    repair_backend = _RecordingRepairBackend()
    conversation_backend = _RecordingConversationBackend()
    router, _ = _build_router(
        micro_backend=micro_backend,
        repair_backend=repair_backend,
        conversation_backend=conversation_backend,
    )

    router.route(
        _discord_request(
            text="frobnicate that",
            explicit=True,
            session_id="discord-explicit-handoff",
        )
    )

    assert len(micro_backend.calls) == 1
    assert len(repair_backend.calls) == 1
    handoff = repair_backend.calls[0]["context"]
    assert handoff["micro_intent"] == "unknown"
    assert handoff["micro_confidence"] == 0.31
    assert handoff["micro_entities"] == {"candidate_text": "frobnicate that"}
    assert handoff["micro_ambiguity_flags"] == ["unknown_intent", "deictic_reference"]
    assert handoff["required_missing_fields"] == []
    assert handoff["agent_id"] == "jarvis"
    assert handoff["agent_display_name"] == "jarvis"
    assert isinstance(handoff["session_summary"], dict)
    assert isinstance(handoff["recent_turns"], list)


def test_unprefixed_child_action_repaired_by_main_is_still_policy_denied():
    class _HomeCapabilityRegistry(_CapabilityRegistry):
        def __init__(self) -> None:
            super().__init__()
            self._skills["skill.home.core"] = {
                "skill_id": "skill.home.core",
                "skill_name": "Home",
                "intents": ["home.set_switch"],
                "execution_ref": "app.skills.domains.lights.handler:run",
                "micro_enabled": True,
                "micro_intents": ["home.set_switch"],
            }

    class _ResolvedActionConversationBackend:
        def decide_turn(self, text: str, context=None):
            return {
                "mode": "execute_action",
                "intent": "home.set_switch",
                "confidence": 0.96,
                "reasoning": "main_detected_home_action",
                "entities": {"switch_name": "kitchen light", "action": "on"},
                "missing_fields": [],
                "message": "",
                "question": None,
                "source": "backend",
            }

        def respond(self, text: str, context=None):
            raise AssertionError("the typed action must be policy checked")

    micro_backend = _RecordingMicroBackend()
    router, _ = _build_router(
        micro_backend=micro_backend,
        conversation_backend=_ResolvedActionConversationBackend(),
        skill_registry=_HomeCapabilityRegistry(),
    )
    request = _discord_request(
        text="turn the kitchen light on",
        explicit=False,
        session_id="discord-child-main-repair",
    )
    request.context.update(
        {
            "is_child": True,
            "policy_profile": "child_conversation_only",
        }
    )

    response = router.route(request)

    assert micro_backend.calls == []
    assert response["route"] == "identity_policy"
    assert response["result"]["status"] == "policy_denied"


def test_unprefixed_answer_to_bang_clarification_uses_main_without_micro_probe():
    class _CalendarClarificationRepairBackend:
        def repair_action(self, text: str, context=None):
            context = dict(context or {})
            if context.get("pending_intent") == "calendar.add_event":
                return {
                    "status": "resolved_action",
                    "intent": "calendar.add_event",
                    "confidence": 0.94,
                    "reasoning": "pending_when_resolved",
                    "entities": {"when_hint": "tomorrow at noon"},
                    "missing_fields": [],
                    "source": "backend",
                }
            return {
                "status": "needs_clarification",
                "intent": "calendar.add_event",
                "confidence": 0.9,
                "reasoning": "calendar_when_missing",
                "entities": {"event_title": "dentist appointment"},
                "missing_fields": ["when_hint"],
                "question": "When should I schedule it?",
                "source": "backend",
            }

    micro_backend = _RecordingMicroBackend(
        payload={
            "intent": "calendar.add_event",
            "confidence": 0.91,
            "entities": {"event_title": "dentist appointment"},
            "ambiguity_flags": ["missing_when"],
            "reasoning": "explicit_calendar_command",
        }
    )
    router, _ = _build_router(
        micro_backend=micro_backend,
        repair_backend=_CalendarClarificationRepairBackend(),
    )

    first = router.route(
        _discord_request(
            text="schedule a dentist appointment",
            explicit=True,
            session_id="discord-prefixed-clarification",
        )
    )
    second = router.route(
        _discord_request(
            text="tomorrow at noon",
            explicit=False,
            session_id="discord-prefixed-clarification",
        )
    )

    assert first["result"]["status"] == "needs_clarification"
    assert second["result"]["status"] == "ok"
    assert second["result"]["event"]["when_hint"] == "tomorrow at noon"
    assert len(micro_backend.calls) == 1


def test_unprefixed_email_summary_routes_through_main_in_authorized_private_channel():
    class _EmailConversationBackend:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def decide_turn(self, text: str, context=None):
            self.calls.append({"text": text, "context": dict(context or {})})
            return {
                "mode": "execute_action",
                "intent": "email.list_recent",
                "confidence": 0.95,
                "reasoning": "collection_email_summary",
                "entities": {"query": text},
                "missing_fields": [],
                "message": "",
                "question": None,
                "source": "backend",
            }

        def respond(self, text: str, context=None):
            raise AssertionError("the typed action must execute")

    micro_backend = _RecordingMicroBackend()
    registry = _CapabilityRegistry()
    email_service = _ScopedEmailService()
    conversation_backend = _EmailConversationBackend()
    router, _ = _build_router(
        micro_backend=micro_backend,
        conversation_backend=conversation_backend,
        skill_registry=registry,
        email_agent_service=email_service,
    )
    request = _discord_request(
        text="summarize today's emails please",
        explicit=False,
        session_id="discord-authorized-email",
    )
    request.context.update(
        {
            "skill_scopes": ["skill.email.agent"],
            "identity_bound": True,
            "discord_channel_id": "222222222222222222",
            "discord_guild_id": "111111111111111111",
            "external_user_id": "555555555555555555",
        }
    )

    response = router.route(request)

    assert micro_backend.calls == []
    assert response["route"] == "main_jarvis_commitment"
    assert response["intent"] == "email.list_recent"
    assert response["result"]["status"] == "ok"
    assert email_service.calls[0]["entities"]["query"] == "summarize today's emails please"
    main_catalog = conversation_backend.calls[0]["context"]["runtime_capability_catalog"]
    email_capability = next(item for item in main_catalog if item["skill_id"] == "skill.email.agent")
    assert email_capability["authorized_here"] is True


def test_main_receives_scoped_catalog_for_its_own_and_micro_capability_questions():
    micro_backend = _RecordingMicroBackend()
    repair_backend = _RecordingRepairBackend()
    conversation_backend = _RecordingConversationBackend()
    registry = _CapabilityRegistry()
    email_service = _ScopedEmailService()
    router, _ = _build_router(
        micro_backend=micro_backend,
        repair_backend=repair_backend,
        conversation_backend=conversation_backend,
        skill_registry=registry,
        email_agent_service=email_service,
    )

    response = router.route(
        _discord_request(
            text="what can Micro do, and can you read my email here?",
            explicit=False,
            session_id="discord-capability-question",
        )
    )

    assert response["route"] == "main_jarvis"
    assert micro_backend.calls == []
    conversation_catalog = conversation_backend.calls[0]["context"]["runtime_capability_catalog"]
    by_id = {str(item["skill_id"]): item for item in conversation_catalog}
    assert by_id["skill.lists.core"]["micro_enabled"] is True
    assert by_id["skill.lists.core"]["micro_intents"] == [
        "lists.add_item",
        "lists.get_items",
    ]
    assert by_id["skill.lists.core"]["main_intents"] == [
        "lists.add_item",
        "lists.get_items",
        "lists.create_list",
    ]
    assert by_id["skill.email.agent"]["configured"] is True
    assert by_id["skill.email.agent"]["authorized_here"] is False
    assert "authorized private email channel" in by_id["skill.email.agent"]["access_note"]
    assert by_id["skill.email.agent"]["intent_contracts"] == [
        {
            "intent": "email.list_recent",
            "purpose": "List or summarize a collection of recent messages.",
            "operation": "read",
            "entity_fields": ["query", "badfield"],
        }
    ]
    assert "execution_ref" not in by_id["skill.email.agent"]["intent_contracts"][0]


def test_main_repaired_email_request_cannot_bypass_private_channel_scope():
    class _EmailRepairBackend:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def repair_action(self, text: str, context=None):
            self.calls.append({"text": text, "context": dict(context or {})})
            return {
                "status": "resolved_action",
                "intent": "email.list_recent",
                "confidence": 0.96,
                "reasoning": "collection_email_summary",
                "entities": {"query": text},
                "missing_fields": [],
                "source": "backend",
            }

    repair_backend = _EmailRepairBackend()
    email_service = _ScopedEmailService()
    router, _ = _build_router(
        micro_backend=_RecordingMicroBackend(
            payload={
                "intent": "unknown",
                "confidence": 0.2,
                "entities": {},
                "ambiguity_flags": ["unknown_intent"],
                "reasoning": "explicit_command_unresolved",
            }
        ),
        repair_backend=repair_backend,
        skill_registry=_CapabilityRegistry(),
        email_agent_service=email_service,
    )

    response = router.route(
        _discord_request(
            text="summarize today's emails",
            explicit=True,
            session_id="discord-unauthorized-email",
        )
    )

    assert response["route"] == "main_jarvis_repair"
    assert response["result"]["status"] == "policy_denied"
    assert "authorized private email channel" in response["result"]["message"]
    catalog = repair_backend.calls[0]["context"]["runtime_capability_catalog"]
    email_capability = next(item for item in catalog if item["skill_id"] == "skill.email.agent")
    assert email_capability["authorized_here"] is False


def test_main_turn_commitment_executes_unprefixed_email_request_in_private_channel():
    class _CommittedConversationBackend:
        def decide_turn(self, text: str, context=None):
            return {
                "mode": "execute_action",
                "intent": "email.list_recent",
                "confidence": 0.97,
                "reasoning": "the user asked for inbox data and supplied a scope",
                "entities": {"query": "all unread"},
                "missing_fields": [],
                "message": "",
                "question": None,
                "source": "backend",
            }

        def respond(self, text: str, context=None):
            raise AssertionError("the typed decision must be executed")

    email_service = _ScopedEmailService()
    router, event_log = _build_router(
        micro_backend=_RecordingMicroBackend(),
        conversation_backend=_CommittedConversationBackend(),
        skill_registry=_CapabilityRegistry(),
        email_agent_service=email_service,
    )
    request = _discord_request(
        text="summarize all unread email",
        explicit=False,
        session_id="discord-main-commitment-email",
    )
    request.context.update(
        {
            "skill_scopes": ["skill.email.agent"],
            "identity_bound": True,
            "discord_channel_id": "222222222222222222",
        }
    )

    response = router.route(request)

    assert response["route"] == "main_jarvis_commitment"
    assert response["intent"] == "email.list_recent"
    assert response["result"]["status"] == "ok"
    assert response["result"]["committed_by"] == "main_turn_decision"
    assert email_service.calls[0]["entities"]["query"] == "all unread"
    assert "skill.email.agent" in email_service.calls[0]["context"]["skill_scopes"]
    event_types = [item["event_type"] for item in event_log.recent(limit=50)]
    assert "main.action.commitment.executed" in event_types


def test_main_turn_clarification_stays_bound_and_followup_executes_email_action():
    class _ClarifyingConversationBackend:
        def decide_turn(self, text: str, context=None):
            return {
                "mode": "clarify_action",
                "intent": "email.list_recent",
                "confidence": 0.95,
                "reasoning": "the email summary scope is a user preference",
                "entities": {},
                "missing_fields": ["query"],
                "message": "I can summarize those.",
                "question": "Which messages should I include?",
                "source": "backend",
            }

        def respond(self, text: str, context=None):
            raise AssertionError("the first turn must open a typed clarification")

    class _ClarificationRepairBackend:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def repair_action(self, text: str, context=None):
            call_context = dict(context or {})
            self.calls.append({"text": text, "context": call_context})
            if call_context.get("pending_intent") != "email.list_recent":
                return {
                    "status": "not_actionable",
                    "confidence": 0.0,
                    "reasoning": "let_the_turn_decision_choose_the_lane",
                    "entities": {},
                    "missing_fields": [],
                    "message": "No repair action selected.",
                    "source": "backend",
                }
            return {
                "status": "resolved_action",
                "intent": "email.list_recent",
                "confidence": 0.98,
                "reasoning": "the follow-up supplies the requested email scope",
                "entities": {"query": text},
                "missing_fields": [],
                "source": "backend",
            }

    email_service = _ScopedEmailService()
    repair_backend = _ClarificationRepairBackend()
    router, event_log = _build_router(
        micro_backend=_RecordingMicroBackend(),
        repair_backend=repair_backend,
        conversation_backend=_ClarifyingConversationBackend(),
        skill_registry=_CapabilityRegistry(),
        email_agent_service=email_service,
    )

    def authorized_request(text: str) -> AskRequest:
        request = _discord_request(
            text=text,
            explicit=False,
            session_id="discord-main-bound-email",
        )
        request.context.update(
            {
                "skill_scopes": ["skill.email.agent"],
                "identity_bound": True,
                "discord_channel_id": "222222222222222222",
            }
        )
        return request

    first = router.route(authorized_request("can you summarize my emails"))
    second = router.route(authorized_request("all unread"))

    assert first["route"] == "main_jarvis_commitment"
    assert first["result"]["status"] == "needs_clarification"
    assert first["result"]["missing_fields"] == ["query"]
    assert second["route"] == "main_jarvis_repair"
    assert second["intent"] == "email.list_recent"
    assert second["result"]["status"] == "ok"
    assert email_service.calls[0]["entities"]["query"] == "all unread"
    assert "skill.email.agent" in email_service.calls[0]["context"]["skill_scopes"]
    clarification_catalog = repair_backend.calls[0]["context"]["runtime_capability_catalog"]
    assert next(item for item in clarification_catalog if item["skill_id"] == "skill.email.agent")[
        "authorized_here"
    ] is True
    event_types = [item["event_type"] for item in event_log.recent(limit=100)]
    assert "main.action.commitment.clarification_opened" in event_types
    assert "main.repair.clarification.executed" in event_types


def test_main_turn_commitment_is_denied_outside_private_email_scope():
    class _CommittedConversationBackend:
        def decide_turn(self, text: str, context=None):
            return {
                "mode": "execute_action",
                "intent": "email.list_recent",
                "confidence": 0.97,
                "reasoning": "email summary requested",
                "entities": {"query": text},
                "missing_fields": [],
                "message": "",
                "question": None,
            }

        def respond(self, text: str, context=None):
            raise AssertionError("the typed action should be denied before execution")

    email_service = _ScopedEmailService()
    router, _ = _build_router(
        micro_backend=_RecordingMicroBackend(),
        conversation_backend=_CommittedConversationBackend(),
        skill_registry=_CapabilityRegistry(),
        email_agent_service=email_service,
    )

    response = router.route(
        _discord_request(
            text="summarize my emails",
            explicit=False,
            session_id="discord-main-commitment-denied",
        )
    )

    assert response["route"] == "main_jarvis_commitment"
    assert response["result"]["status"] == "policy_denied"
    assert "authorized private email channel" in response["result"]["message"]
    assert email_service.calls == []


class _ActiveNoMatchBackend:
    def __init__(self) -> None:
        self.commitment_calls = 0
        self.selection_calls = 0

    def decide_turn(self, text: str, context=None):
        del text
        assert (context or {}).get("main_tool_execution_mode") == "active"
        self.commitment_calls += 1
        return {
            "mode": "execute_action",
            "confidence": 0.96,
            "reason_code": "plausible_action",
        }

    def select_skills(self, text, discovery_cards, context=None):
        del text, discovery_cards, context
        self.selection_calls += 1
        return {
            "mode": "no_match",
            "selected_skill_ids": [],
            "reason_code": "no_relevant_skill",
        }

    def next_tool_step(self, *args, **kwargs):
        raise AssertionError("no effective tool may reach a step")

    def respond(self, text: str, context=None):
        raise AssertionError("active mode cannot enter legacy Main response planning")


def test_active_mode_prefixed_and_unprefixed_requests_bypass_micro_and_legacy():
    for explicit in (False, True):
        micro_backend = _RecordingMicroBackend(
            payload={
                "intent": "calendar.view",
                "confidence": 0.99,
                "entities": {"window": "daily"},
                "ambiguity_flags": [],
                "reasoning": "must_not_run",
            }
        )
        backend = _ActiveNoMatchBackend()
        router, events = _build_router(
            micro_backend=micro_backend,
            conversation_backend=backend,
            main_tool_execution_mode="active",
        )

        response = router.route(
            _discord_request(
                text="show my calendar today",
                explicit=explicit,
                session_id=f"active-no-legacy-{explicit}",
            )
        )

        assert response["route"] == "main_tool_loop"
        assert response["result"]["status"] == "unavailable"
        assert response["result"]["stop_reason"] == "no_relevant_skill"
        assert micro_backend.calls == []
        assert backend.commitment_calls == 1
        assert backend.selection_calls == 0
        event_types = [item["event_type"] for item in events.recent(limit=100)]
        assert "tool.executed" not in event_types
        assert "main.plan.generated" not in event_types


def test_active_mode_web_request_cannot_be_preempted_by_legacy_list_repair():
    class _ForbiddenLegacyRepair:
        def repair_action(self, text: str, context=None):
            del text, context
            raise AssertionError("active mode must not invoke legacy list repair")

    micro_backend = _RecordingMicroBackend()
    backend = _ActiveNoMatchBackend()
    router, events = _build_router(
        micro_backend=micro_backend,
        repair_backend=_ForbiddenLegacyRepair(),
        conversation_backend=backend,
        main_tool_execution_mode="active",
    )

    response = router.route(
        AskRequest(
            text="Add screws and wall anchors to the hardware run list.",
            session_id="active-web-no-legacy-list-repair",
            user_id="operator",
            source="web",
            context={"agent_id": "jarvis"},
        )
    )

    assert response["route"] == "main_tool_loop"
    assert response["result"]["status"] == "unavailable"
    assert response["result"]["stop_reason"] == "no_relevant_skill"
    assert micro_backend.calls == []
    assert backend.commitment_calls == 1
    bypass = [
        item
        for item in events.recent(limit=100)
        if item["event_type"] == "pipeline.main_repair.bypassed"
    ]
    assert bypass[-1]["payload"]["reason"] == "active_mode_main_tool_commitment"


class _ShadowAndLegacyBackend:
    def __init__(self) -> None:
        self.shadow_commitments = 0
        self.legacy_commitments = 0

    def decide_turn(self, text: str, context=None):
        del text
        if (context or {}).get("main_tool_execution_mode") == "shadow":
            self.shadow_commitments += 1
            return {
                "mode": "execute_action",
                "confidence": 0.9,
                "reason_code": "plausible_action",
            }
        self.legacy_commitments += 1
        return {
            "mode": "conversation",
            "intent": None,
            "confidence": 0.9,
            "reasoning": "legacy_response_remains_authoritative",
            "entities": {},
            "missing_fields": [],
            "message": "Legacy answer unchanged.",
            "question": None,
        }

    def select_skills(self, text, discovery_cards, context=None):
        del text, discovery_cards, context
        return {
            "mode": "no_match",
            "selected_skill_ids": [],
            "reason_code": "no_relevant_skill",
        }

    def next_tool_step(self, *args, **kwargs):
        raise AssertionError("empty shadow allowlists cannot reach a step")

    def respond(self, text: str, context=None):
        raise AssertionError("the legacy commitment supplies the response")


def test_shadow_observes_content_free_status_without_changing_legacy_response():
    backend = _ShadowAndLegacyBackend()
    micro = _RecordingMicroBackend()
    router, events = _build_router(
        micro_backend=micro,
        conversation_backend=backend,
        main_tool_execution_mode="shadow",
    )

    response = router.route(
        _discord_request(
            text="please do an unavailable action",
            explicit=False,
            session_id="shadow-legacy-authoritative",
        )
    )

    assert response["route"] == "main_jarvis"
    assert response["result"]["message"] == "Legacy answer unchanged."
    assert backend.shadow_commitments == 1
    assert backend.legacy_commitments == 1
    assert micro.calls == []
    shadow_events = [
        item for item in events.recent(limit=100)
        if ".shadow." in item["event_type"]
    ]
    assert shadow_events
    assert "please do an unavailable action" not in str(shadow_events)
    assert all(int(item["payload"].get("committed_effect_count") or 0) == 0 for item in shadow_events)
