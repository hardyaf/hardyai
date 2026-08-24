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


def _build_router() -> JarvisRouter:
    return JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(),
    )


def test_sleep_blocks_non_wake_requests_until_wake_phrase():
    router = _build_router()

    sleep = router.route(AskRequest(text="Jarvis go to sleep", session_id="s1"))
    assert sleep["power_state"] == "ASLEEP"
    assert sleep["result"]["status"] == "sleeping"

    blocked = router.route(AskRequest(text="add milk to groceries", session_id="s1"))
    assert blocked["route"] == "sleep_guard"
    assert blocked["result"]["status"] == "sleeping"

    wake = router.route(AskRequest(text="wake up jarvis", session_id="s1"))
    assert wake["power_state"] == "AWAKE"
    assert wake["result"]["status"] == "awake"

    after_wake = router.route(AskRequest(text="add milk to groceries", session_id="s1"))
    assert after_wake["route"] == "micro_tool"
    assert after_wake["result"]["status"] == "ok"
    assert after_wake["result"]["list_name"] == "groceries"
