from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from app.core.main_jarvis import MainJarvis
from app.core.micro_jarvis import MicroJarvis
from tests.router_support import RegistryBackedTestRouter as JarvisRouter
from app.core.session_store import SessionStore
from app.core.state_machine import RuntimePowerController
from app.db.sqlite_store import SQLiteStore
from app.schemas.api import AskRequest
from app.services.event_log import EventLogService
from app.skills.registry_service import SkillRegistryService
from app.tools.calendar_service import CalendarService
from app.tools.home_service import HomeService
from app.tools.lists_service import ListsService


def test_router_resolves_agent_alias_and_records_skill_run():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-router-agent-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "router.db"
        store = SQLiteStore(database_path=str(db_path))
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()

        router = JarvisRouter(
            micro_jarvis=MicroJarvis(),
            main_jarvis=MainJarvis(),
            session_store=SessionStore(persistence=store),
            runtime_power=RuntimePowerController(),
            event_log=EventLogService(persistence=store),
            memory_service=None,
            lists_service=ListsService(default_list_names=["groceries"], sqlite_store=store),
            calendar_service=CalendarService(),
            home_service=HomeService(sqlite_store=store, default_switch_names=["office test light"]),
            skill_registry=registry,
        )

        response = router.route(
            AskRequest(
                text="hey catparty add milk to groceries",
                session_id="agent-session-1",
                user_id="local_user",
                source="web",
            )
        )
        assert response["result"]["status"] == "ok"

        session = store.get_session("agent-session-1")
        assert session is not None
        assert session["user_id"] == "local_user"

        runs = store.recent_skill_runs(limit=5)
        assert runs
        assert runs[0]["skill_id"] == "skill.lists.core"
        assert runs[0]["user_id"] == "local_user"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_router_prefers_execution_dispatcher_for_skill_execution():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-router-dispatch-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "router.db"
        store = SQLiteStore(database_path=str(db_path))
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()

        router = JarvisRouter(
            micro_jarvis=MicroJarvis(),
            main_jarvis=MainJarvis(),
            session_store=SessionStore(persistence=store),
            runtime_power=RuntimePowerController(),
            event_log=EventLogService(persistence=store),
            memory_service=None,
            lists_service=ListsService(default_list_names=["groceries"], sqlite_store=store),
            calendar_service=CalendarService(),
            home_service=HomeService(sqlite_store=store, default_switch_names=["office test light"]),
            skill_registry=registry,
        )

        captured: dict[str, object] = {}

        def _fake_execute(*, skill, intent, entities, context):  # type: ignore[no-untyped-def]
            captured["skill"] = skill
            captured["intent"] = intent
            captured["entities"] = entities
            captured["context"] = context
            return {"status": "ok", "message": "dispatched"}

        router._authorized_skill_executor._dispatcher.execute = _fake_execute  # type: ignore[assignment]

        response = router.route(
            AskRequest(
                text="hey catparty add milk to groceries",
                session_id="agent-session-2",
                user_id="local_user",
                source="web",
            )
        )

        assert response["result"]["status"] == "ok"
        assert response["result"]["message"] == "dispatched"
        assert isinstance(captured.get("skill"), dict)
        assert captured["intent"] == "lists.add_item"
        assert captured["skill"]["skill_id"] == "skill.lists.core"  # type: ignore[index]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
