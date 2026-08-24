from typing import Any

from app.core.main_jarvis import MainJarvis
from app.core.micro_jarvis import MicroJarvis
from tests.router_support import RegistryBackedTestRouter as JarvisRouter
from app.core.session_store import SessionStore
from app.core.state_machine import RuntimePowerController
from app.core.types import SessionOwner
from app.schemas.api import AskRequest
from app.services.event_log import EventLogService
from app.tools.calendar_service import CalendarService
from app.tools.home_service import HomeService
from app.tools.lists_service import ListsService


class _MicroBackend:
    def classify(self, text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        del text
        del context
        return {
            "intent": "lists.get_items",
            "confidence": 0.92,
            "entities": {"list_name": "it"},
            "ambiguity_flags": ["deictic_list_reference"],
            "reasoning": "stub_backend",
        }


class _StubContract:
    contract_id = "stub_lists"

    def __init__(self) -> None:
        self.resolve_calls = 0
        self.emit_calls = 0
        self.continue_calls = 0

    def supports_intent(self, *, intent: str) -> bool:
        return str(intent or "").strip().lower() == "lists.get_items"

    def emit_context_updates(self, *, intent: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        self.emit_calls += 1
        del intent
        if str(result.get("status") or "").strip().lower() not in {"ok", "partial"}:
            return []
        return [
            {
                "domain": "lists",
                "entity_type": "list",
                "display_name": "contract-list",
                "aliases": ["contract list"],
                "salience": 0.95,
            }
        ]

    def resolve_followup(
        self,
        *,
        decision: Any,
        registry: Any,
        resolver: Any,
        required_fields_for_intent: Any,
        has_blocking_ambiguity: Any,
    ) -> Any:
        del registry
        del resolver
        self.resolve_calls += 1
        decision.entities["list_name"] = "groceries"
        decision.ambiguity_flags = [
            str(flag)
            for flag in decision.ambiguity_flags
            if str(flag).strip().lower() != "deictic_list_reference"
        ]
        if "stub_context_contract" not in decision.ambiguity_flags:
            decision.ambiguity_flags.append("stub_context_contract")
        decision.confidence = max(float(decision.confidence), 0.9)
        missing = required_fields_for_intent(decision.intent, decision.entities)
        if not missing and not has_blocking_ambiguity(decision):
            decision.recommended_owner = SessionOwner.MICRO
        return decision

    def continue_pending_interaction(
        self,
        *,
        intent: str,
        text: str,
        missing_fields: list[str],
        current_entities: dict[str, Any],
    ) -> dict[str, Any]:
        self.continue_calls += 1
        del intent
        del text
        del missing_fields
        del current_entities
        return {}

    def refine_missing_fields(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        missing_fields: list[str],
        resolver: Any,
    ) -> list[str]:
        del intent
        del entities
        del resolver
        return [str(item).strip() for item in missing_fields if str(item).strip()]

    def shape_tool_followup(
        self,
        *,
        intent: str,
        status: str,
        tool_result: dict[str, Any],
        entities: dict[str, Any],
        missing_fields: list[str],
        question: str | None,
        registry: Any,
    ) -> dict[str, Any]:
        del intent
        del status
        del tool_result
        del registry
        return {
            "entities": dict(entities),
            "missing_fields": list(missing_fields),
            "question": question,
        }

    def legacy_main_handoff_hints(
        self,
        *,
        registry: Any,
        context_reference: dict[str, Any],
        runtime_context: dict[str, Any] | None = None,
        intent: str | None = None,
        route: str | None = None,
    ) -> dict[str, Any]:
        del registry
        del context_reference
        del runtime_context
        del intent
        del route
        return {}


def test_router_uses_skill_context_contracts_for_followup_resolution_and_context_emission():
    session_store = SessionStore()
    stub_contract = _StubContract()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=_MicroBackend()),
        main_jarvis=MainJarvis(),
        session_store=session_store,
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["kitchen light"]),
    )
    router._skill_context_contracts = [stub_contract]

    response = router.route(
        AskRequest(
            text="show it",
            session_id="router-contract-1",
            user_id="jordan",
            source="web",
        )
    )
    assert response["route"] == "micro_tool"
    assert response["result"]["status"] == "ok"
    assert response["result"]["list_name"] == "groceries"
    assert stub_contract.resolve_calls >= 1
    assert stub_contract.emit_calls >= 1

    session = session_store.get_or_create(
        session_id="router-contract-1",
        user_id="jordan",
        source="web",
    )
    entity_registry = session.context_reference.get("entity_registry")
    assert isinstance(entity_registry, dict)
    entities = entity_registry.get("entities")
    assert isinstance(entities, list)
    assert any(
        isinstance(item, dict) and str(item.get("display_name") or "").strip().lower() == "contract-list"
        for item in entities
    )


def test_router_enriches_rotated_session_with_safe_email_anchor_before_micro_routing():
    class FakeEmailService:
        def __init__(self) -> None:
            self.executed = []

        def working_context_hint(self, *, context):
            assert context["source_interface"] == "discord"
            assert context["requested_by_user_id"] == "jordan"
            return {
                "skill_id": "skill.email.agent",
                "context_kind": "email_reference_set",
                "last_email_reference_set_id": "durable-ref-1",
                "last_email_result_count": 2,
            }

        def execute(self, *, intent, entities, context):
            self.executed.append((intent, entities, context))
            return {
                "status": "ok",
                "message": "Two new emails.",
                "email_context_entities": [],
            }

    email_service = FakeEmailService()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["kitchen light"]),
        email_agent_service=email_service,
    )

    response = router.route(
        AskRequest(
            text="Can you summarize new ones now",
            session_id="rotated-email-session",
            user_id="jordan",
            source="discord",
                context={
                    "identity_bound": True,
                    "external_user_id": "42",
                    "discord_channel_id": "222222222222222222",
                    "agent_id": "jarvis",
                    "micro_command_explicit": True,
                },
        )
    )

    assert response["intent"] == "email.list_recent"
    assert response["route"] == "main_skill"
    assert email_service.executed[0][0] == "email.list_recent"


def test_router_uses_contract_hook_for_pending_interaction_continue():
    class _PendingContract(_StubContract):
        contract_id = "pending_lists"

        def supports_intent(self, *, intent: str) -> bool:
            return str(intent or "").strip().lower() == "lists.get_items"

        def resolve_followup(
            self,
            *,
            decision: Any,
            registry: Any,
            resolver: Any,
            required_fields_for_intent: Any,
            has_blocking_ambiguity: Any,
        ) -> Any:
            del registry
            del resolver
            del required_fields_for_intent
            del has_blocking_ambiguity
            self.resolve_calls += 1
            return decision

        def continue_pending_interaction(
            self,
            *,
            intent: str,
            text: str,
            missing_fields: list[str],
            current_entities: dict[str, Any],
        ) -> dict[str, Any]:
            self.continue_calls += 1
            del text
            del current_entities
            if str(intent or "").strip().lower() != "lists.get_items":
                return {}
            if "list_name" in missing_fields:
                return {"list_name": "groceries"}
            return {}

        def shape_tool_followup(
            self,
            *,
            intent: str,
            status: str,
            tool_result: dict[str, Any],
            entities: dict[str, Any],
            missing_fields: list[str],
            question: str | None,
            registry: Any,
        ) -> dict[str, Any]:
            del intent
            del tool_result
            del registry
            next_missing = [str(item).strip() for item in missing_fields if str(item).strip()]
            if str(status or "").strip().lower() == "unknown_list" and "list_name" not in next_missing:
                next_missing.append("list_name")
            return {
                "entities": dict(entities),
                "missing_fields": next_missing,
                "question": question,
            }

    session_store = SessionStore()
    pending_contract = _PendingContract()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(),
        session_store=session_store,
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["kitchen light"]),
    )
    router._skill_context_contracts = [pending_contract]

    first = router.route(
        AskRequest(
            text="what is on blue list",
            session_id="router-contract-pending-1",
            user_id="jordan",
            source="web",
        )
    )
    assert first["state"] == "AWAITING_CONFIRMATION"
    assert first["result"]["status"] == "unknown_list"

    second = router.route(
        AskRequest(
            text="yes",
            session_id="router-contract-pending-1",
            user_id="jordan",
            source="web",
        )
    )
    assert pending_contract.continue_calls >= 1
    assert second["route"] == "main_jarvis_repair"
    assert second["result"]["status"] == "ok"
    assert second["result"]["list_name"] == "groceries"


def test_router_uses_contract_hook_for_missing_field_refinement():
    class _RefineContract(_StubContract):
        contract_id = "refine_lists"

        def __init__(self) -> None:
            super().__init__()
            self.refine_calls = 0

        def resolve_followup(
            self,
            *,
            decision: Any,
            registry: Any,
            resolver: Any,
            required_fields_for_intent: Any,
            has_blocking_ambiguity: Any,
        ) -> Any:
            del registry
            del resolver
            del required_fields_for_intent
            del has_blocking_ambiguity
            self.resolve_calls += 1
            return decision

        def refine_missing_fields(
            self,
            *,
            intent: str,
            entities: dict[str, Any],
            missing_fields: list[str],
            resolver: Any,
        ) -> list[str]:
            self.refine_calls += 1
            del intent
            del resolver
            next_missing = [str(item).strip() for item in missing_fields if str(item).strip()]
            list_name = str(entities.get("list_name") or "").strip().lower()
            if list_name in {"it", "that list"} and "list_name" not in next_missing:
                next_missing.append("list_name")
            return next_missing

    session_store = SessionStore()
    refine_contract = _RefineContract()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=_MicroBackend()),
        main_jarvis=MainJarvis(),
        session_store=session_store,
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["kitchen light"]),
    )
    router._skill_context_contracts = [refine_contract]

    response = router.route(
        AskRequest(
            text="show it",
            session_id="router-contract-refine-1",
            user_id="jordan",
            source="web",
        )
    )
    assert refine_contract.refine_calls >= 1
    assert response["route"] != "micro_tool"


def test_router_legacy_main_handoff_context_comes_from_contract_hooks():
    class _HandoffContract(_StubContract):
        contract_id = "handoff"

        def supports_intent(self, *, intent: str) -> bool:
            del intent
            return False

        def legacy_main_handoff_hints(
            self,
            *,
            registry: Any,
            context_reference: dict[str, Any],
            runtime_context: dict[str, Any] | None = None,
            intent: str | None = None,
            route: str | None = None,
        ) -> dict[str, Any]:
            del registry
            del context_reference
            del runtime_context
            del intent
            del route
            return {"last_list_name": "contract-groceries"}

    session_store = SessionStore()
    handoff_contract = _HandoffContract()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(),
        session_store=session_store,
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["kitchen light"]),
    )
    router._skill_context_contracts = [handoff_contract]
    session = session_store.get_or_create(
        session_id="router-contract-handoff-1",
        user_id="jordan",
        source="web",
    )
    hints = router._legacy_main_handoff_context(session=session)
    assert hints.get("last_list_name") == "contract-groceries"


def test_router_legacy_main_handoff_context_includes_runtime_available_switches_from_contract():
    class _SwitchHandoffContract(_StubContract):
        contract_id = "handoff_switches"

        def supports_intent(self, *, intent: str) -> bool:
            del intent
            return False

        def legacy_main_handoff_hints(
            self,
            *,
            registry: Any,
            context_reference: dict[str, Any],
            runtime_context: dict[str, Any] | None = None,
            intent: str | None = None,
            route: str | None = None,
        ) -> dict[str, Any]:
            del registry
            del context_reference
            del intent
            del route
            runtime = runtime_context if isinstance(runtime_context, dict) else {}
            switches = runtime.get("available_switches")
            if isinstance(switches, list) and switches:
                return {"available_switches": switches}
            return {}

    session_store = SessionStore()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(),
        session_store=session_store,
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["kitchen light"]),
    )
    router._skill_context_contracts = [_SwitchHandoffContract()]
    session = session_store.get_or_create(
        session_id="router-contract-handoff-2",
        user_id="jordan",
        source="web",
    )
    hints = router._legacy_main_handoff_context(session=session)
    switches = hints.get("available_switches")
    assert isinstance(switches, list)
    assert switches
