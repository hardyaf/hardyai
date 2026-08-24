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


def test_router_captures_recent_turns_in_single_post_turn_pipeline_point():
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
        home_service=HomeService(),
    )

    response = router.route(
        AskRequest(
            text="add milk to groceries",
            session_id="recent-turns-1",
            user_id="jordan",
            source="web",
        )
    )
    assert response["result"]["status"] == "ok"
    assert response["delivery"]["session"]["status"] == "committed"
    assert response["delivery"]["memory"]["status"] == "not_applicable"

    session = session_store.get_or_create(
        session_id="recent-turns-1",
        user_id="jordan",
        source="web",
    )
    turns = session.context_reference.get("recent_turns")
    assert isinstance(turns, list)
    assert len(turns) >= 2

    user_turn = turns[-2]
    assistant_turn = turns[-1]
    assert user_turn["role"] == "user"
    assert assistant_turn["role"] == "assistant"
    assert user_turn["intent"] == "lists.add_item"
    assert assistant_turn["intent"] == "lists.add_item"
    assert user_turn["references"]["route"] == "micro_tool"
    assert assistant_turn["references"]["route"] == "micro_tool"
    assert assistant_turn["references"]["status"] == "ok"
    assert isinstance(user_turn["normalized_text"], str) and user_turn["normalized_text"]
