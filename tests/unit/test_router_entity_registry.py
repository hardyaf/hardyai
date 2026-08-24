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


def test_router_records_list_entity_and_resolves_deictic_followup_from_registry():
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
        home_service=HomeService(default_switch_names=["kitchen light", "office lamp"]),
    )

    first = router.route(
        AskRequest(
            text="add milk to groceries",
            session_id="entity-registry-list-1",
            user_id="jordan",
            source="web",
        )
    )
    assert first["result"]["status"] == "ok"

    session = session_store.get_or_create(
        session_id="entity-registry-list-1",
        user_id="jordan",
        source="web",
    )
    entity_registry = session.context_reference.get("entity_registry")
    assert isinstance(entity_registry, dict)
    entities = entity_registry.get("entities")
    assert isinstance(entities, list)
    assert any(
        str(item.get("domain") or "") == "lists"
        and str(item.get("entity_type") or "") == "list"
        and str(item.get("display_name") or "").strip().lower() == "groceries"
        for item in entities
        if isinstance(item, dict)
    )

    second = router.route(
        AskRequest(
            text="add tofu to it",
            session_id="entity-registry-list-1",
            user_id="jordan",
            source="web",
        )
    )
    assert second["result"]["status"] == "ok"
    assert second["result"]["list_name"] == "groceries"
