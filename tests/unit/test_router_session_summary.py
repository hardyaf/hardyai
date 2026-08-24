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


def test_router_updates_session_summary_after_turn():
    session_store = SessionStore()
    event_log = EventLogService()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(),
        session_store=session_store,
        runtime_power=RuntimePowerController(),
        event_log=event_log,
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["kitchen light"]),
        session_summary_update_every_turns=100,
        session_summary_budget_char_threshold=9000,
    )

    response = router.route(
        AskRequest(
            text="add milk to groceries",
            session_id="router-summary-1",
            user_id="jordan",
            source="web",
        )
    )
    assert response["result"]["status"] == "ok"

    session = session_store.get_or_create(
        session_id="router-summary-1",
        user_id="jordan",
        source="web",
    )
    summary = session.context_reference.get("session_summary")
    assert isinstance(summary, dict)
    assert isinstance(summary.get("summary_text"), str) and str(summary.get("summary_text")).strip()

    resolved = summary.get("resolved_decisions")
    assert isinstance(resolved, list)
    assert "lists.add_item:ok" in resolved

    events = event_log.recent(limit=200)
    summary_updates = [event for event in events if event.get("event_type") == "context.session_summary.updated"]
    assert summary_updates
    assert any(str(event.get("payload", {}).get("trigger") or "") == "task_completed" for event in summary_updates)
