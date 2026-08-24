from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.main_jarvis import MainJarvis
from app.core.micro_jarvis import MicroJarvis
from app.core.router import JarvisRouter
from app.core.session_store import SessionStore
from app.core.state_machine import RuntimePowerController
from app.db.sqlite_store import SQLiteStore
from app.schemas.api import AskRequest
from app.services.event_log import EventLogService
from app.skills.registry_service import SkillRegistryService
from app.tools.calendar_service import CalendarService
from app.tools.home_service import HomeService
from app.tools.lists_service import ListsService


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_router_escalates_when_micro_contract_disables_intent():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-router-gate-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "router.db"
        store = SQLiteStore(database_path=str(db_path))
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()
        registry.sync_skills_from_markdown()

        skills = {str(skill["skill_id"]): skill for skill in registry.list_skills(active_only=False)}
        lights = skills["skill.home.lights"]
        store.upsert_skill(
            skill_id=str(lights["skill_id"]),
            skill_name=str(lights["skill_name"]),
            skill_user=str(lights["skill_user"]),
            skill_agents=[str(item) for item in lights.get("skill_agents", [])],
            intents=[str(item) for item in lights.get("intents", [])],
            markdown_path=str(lights["markdown_path"]),
            execution_ref=str(lights.get("execution_ref") or ""),
            created_by=str(lights["created_by"]),
            storage_type=str(lights["storage_type"]),
            storage_ref=str(lights.get("storage_ref") or ""),
            micro_enabled=False,
            micro_functions=[],
            micro_failure_handoff=lights.get("micro_failure_handoff", {}),
            main_handoff_context=lights.get("main_handoff_context", {}),
            learnable_ready=True,
            critical_level=int(lights["critical_level"]),
            active=True,
            cron_enabled=False,
            cron_expr=None,
            updated_at=_utc_now(),
        )

        router = JarvisRouter(
            micro_jarvis=MicroJarvis(),
            main_jarvis=MainJarvis(),
            session_store=SessionStore(),
            runtime_power=RuntimePowerController(),
            event_log=EventLogService(),
            memory_service=None,
            lists_service=ListsService(default_list_names=["groceries", "to-do"], sqlite_store=store),
            calendar_service=CalendarService(),
            home_service=HomeService(
                default_switch_names=["office test light", "kitchen light", "living room lamp"],
                sqlite_store=store,
            ),
            skill_registry=registry,
        )
        response = router.route(
            AskRequest(
                text="turn kitchen light on",
                user_id="jordan",
            )
        )

        assert response["route"] == "main_jarvis"
        flags = response.get("classification", {}).get("ambiguity_flags", [])
        assert "micro_contract_escalation" in flags
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_missing_registry_record_cannot_fall_back_to_domain_handler():
    class DenyingRegistry:
        @staticmethod
        def resolve_agent_context(*, text, fallback_user_id, fallback_agent_id):
            return {
                "agent_id": fallback_agent_id,
                "display_name": "Jarvis",
                "wake_alias": None,
                "normalized_text": text,
                "resolved_user_id": fallback_user_id,
                "personality_doc_path": None,
            }

        @staticmethod
        def resolve_skill(*, intent, user_id, agent_id):
            del intent, user_id, agent_id
            return None

        @staticmethod
        def runtime_capability_catalog(*, user_id, agent_id):
            del user_id, agent_id
            return []

        @staticmethod
        def is_micro_allowed_for_intent(*, skill, intent):
            del skill, intent
            return False

        @staticmethod
        def record_skill_run(**kwargs):
            del kwargs

    home = HomeService(default_switch_names=["kitchen light"])
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries"]),
        calendar_service=CalendarService(),
        home_service=home,
        skill_registry=DenyingRegistry(),
    )

    response = router.route(AskRequest(text="turn kitchen light on", user_id="jordan"))

    assert response["result"]["status"] == "policy_denied"
    assert response["result"]["denial_reason"] == "skill_unavailable_or_unauthorized"
    assert home.list_switches()[0]["state"] == "off"
