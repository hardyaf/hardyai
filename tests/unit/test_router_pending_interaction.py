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


def _build_router(session_store: SessionStore) -> JarvisRouter:
    return JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(),
        session_store=session_store,
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light"]),
    )


def test_router_uses_typed_pending_interaction_storage_and_cancel_path():
    store = SessionStore()
    router = _build_router(store)

    first = router.route(
        AskRequest(
            text="add milk to blue list",
            session_id="pending-interaction-1",
            user_id="jordan",
            source="web",
        )
    )
    assert first["state"] == "AWAITING_CONFIRMATION"

    session = store.get_or_create(
        session_id="pending-interaction-1",
        user_id="jordan",
        source="web",
    )
    pending_interaction = session.context_reference.get("pending_interaction")
    assert isinstance(pending_interaction, dict)
    assert pending_interaction["intent"] == "lists.add_item"
    assert pending_interaction["kind"] == "missing_field"
    assert pending_interaction["expected_fields"] == ["list_name"]

    second = router.route(
        AskRequest(
            text="nevermind",
            session_id="pending-interaction-1",
            user_id="jordan",
            source="web",
        )
    )
    assert second["result"]["status"] == "cancelled"

    session_after_cancel = store.get_or_create(
        session_id="pending-interaction-1",
        user_id="jordan",
        source="web",
    )
    assert session_after_cancel.context_reference.get("pending_interaction") is None
    annotations = session_after_cancel.context_reference.get("context_annotations")
    assert isinstance(annotations, dict)
    assert annotations.get("last_pending_cancel_reason") == "user_cancelled_pending_flow"


def test_router_emits_pending_interaction_transition_events():
    store = SessionStore()
    event_log = EventLogService()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(),
        session_store=store,
        runtime_power=RuntimePowerController(),
        event_log=event_log,
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light"]),
    )

    first = router.route(
        AskRequest(
            text="add milk to blue list",
            session_id="pending-transition-events-1",
            user_id="jordan",
            source="web",
        )
    )
    assert first["state"] == "AWAITING_CONFIRMATION"

    second = router.route(
        AskRequest(
            text="cancel that",
            session_id="pending-transition-events-1",
            user_id="jordan",
            source="web",
        )
    )
    assert second["result"]["status"] == "cancelled"

    events = event_log.recent(limit=300)
    transitions = [
        event
        for event in events
        if str(event.get("event_type") or "").strip().lower() == "context.pending_interaction.transition"
    ]
    assert transitions
    actions = [str(event.get("payload", {}).get("action") or "").strip().lower() for event in transitions]
    assert "set" in actions
    assert "cancel" in actions
