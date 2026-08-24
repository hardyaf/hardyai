from typing import Any

from app.core.main_jarvis import MainJarvis
from app.core.micro_jarvis import MicroJarvis
from tests.router_support import RegistryBackedTestRouter as JarvisRouter
from app.core.session_store import SessionStore
from app.core.state_machine import RuntimePowerController
from app.schemas.api import AskRequest
from app.services.event_log import EventLogService
from app.tools.calendar_service import CalendarService
from app.tools.home_service import HomeService
from app.tools.lists_service import ListsService


class CapturingMicroBackend:
    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []

    def classify(self, text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        self.contexts.append(dict(context or {}))
        return {
            "intent": "lists.create_list",
            "confidence": 0.93,
            "entities": {"list_name": "weekend"},
            "ambiguity_flags": [],
            "reasoning": "test_backend",
        }


class CapturingMainJarvis(MainJarvis):
    def __init__(self) -> None:
        super().__init__()
        self.respond_context: dict[str, Any] | None = None

    def respond(self, text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        self.respond_context = dict(context or {})
        return {
            "status": "conversation",
            "message": "I can help with that.",
        }


def test_router_builds_working_context_packet_for_micro_and_main_paths():
    micro_backend = CapturingMicroBackend()
    main_jarvis = CapturingMainJarvis()
    event_log = EventLogService()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=micro_backend),
        main_jarvis=main_jarvis,
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=event_log,
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["kitchen light"]),
    )

    response = router.route(
        AskRequest(
            text="create weekend list",
            session_id="working-context-router-1",
            user_id="jordan",
            source="web",
        )
    )
    assert response["route"] == "main_jarvis"

    assert micro_backend.contexts
    micro_context = micro_backend.contexts[-1]
    working_context = micro_context.get("working_context")
    assert isinstance(working_context, dict)
    assert isinstance(working_context.get("session_summary"), dict)
    assert isinstance(working_context.get("recent_turns"), list)
    assert isinstance(working_context.get("entity_hints"), list)
    assert isinstance(working_context.get("budget_metadata"), dict)

    assert isinstance(main_jarvis.respond_context, dict)
    main_working_context = main_jarvis.respond_context.get("working_context")
    assert isinstance(main_working_context, dict)
    assert isinstance(main_working_context.get("session_summary"), dict)
    assert isinstance(main_working_context.get("recent_turns"), list)
    assert isinstance(main_working_context.get("budget_metadata"), dict)

    events = event_log.recent(limit=200)
    packet_events = [event for event in events if event.get("event_type") == "context.packet.built"]
    assert packet_events
