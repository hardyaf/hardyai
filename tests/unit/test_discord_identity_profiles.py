from __future__ import annotations

from app.core.main_jarvis import MainJarvis
from app.core.micro_jarvis import MicroJarvis
from tests.router_support import RegistryBackedTestRouter as JarvisRouter
from app.core.session_store import SessionStore
from app.core.state_machine import RuntimePowerController
from app.db.sqlite_store import SQLiteStore
from app.schemas.api import AskRequest
from app.services.event_log import EventLogService
from app.services.identity_service import ExternalIdentityService
from app.skills.registry_service import SkillRegistryService
from app.tickets.repository import TicketRepository
from app.tools.calendar_service import CalendarService
from app.tools.home_service import HomeService
from app.tools.lists_service import ListsService


def test_discord_bindings_isolate_sessions_personas_and_child_actions(tmp_path):
    path = tmp_path / "identity.db"
    store = SQLiteStore(database_path=str(path))
    repository = TicketRepository(database_path=str(path))
    registry = SkillRegistryService(sqlite_store=store)
    registry.seed_defaults()
    identities = ExternalIdentityService(repository=repository, skill_registry=registry)
    identities.upsert(
        source="discord",
        external_user_id="1001",
        external_display_name="Kid One",
        user_id="kid-one",
        agent_id="kid_spark",
        age_band="6-8",
        presentation_profile="child_simple",
        policy_profile="child_conversation_only",
        active=True,
    )
    identities.upsert(
        source="discord",
        external_user_id="1002",
        external_display_name="Kid Two",
        user_id="kid-two",
        agent_id="kid_quest",
        age_band="9-11",
        presentation_profile="child_simple",
        policy_profile="child_conversation_only",
        active=True,
    )
    identities.upsert(
        source="discord",
        external_user_id="444444444444444444",
        external_display_name="Casey",
        user_id="child",
        agent_id="child",
        age_band="6-8",
        presentation_profile="child_simple",
        policy_profile="child_conversation_only",
        active=True,
    )
    home = HomeService(sqlite_store=store, default_switch_names=["office test light"])
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(),
        session_store=SessionStore(persistence=store),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(persistence=store),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries"], sqlite_store=store),
        calendar_service=CalendarService(),
        home_service=home,
        skill_registry=registry,
        identity_service=identities,
    )

    def request(user_id: str, text: str):
        return router.route(
            AskRequest(
                text=text,
                user_id=user_id,
                source="discord",
                    context={
                        "external_user_id": user_id,
                        "auto_channel_session": True,
                        "channel_session_scope": "per_user",
                        "session_channel": "discord.guild.1.channel.2",
                        "micro_command_explicit": True,
                    },
            )
        )

    first = request("1001", "tell me a silly cat fact")
    second = request("1002", "tell me a silly cat fact")
    child = request("444444444444444444", "Hi Jarvis. I'm Casey")
    assert first["agent_id"] == "kid_spark"
    assert second["agent_id"] == "kid_quest"
    assert child["agent_id"] == "child"
    assert child["session_runtime"]["channel"]["channel_key"] == "child:discord.guild.1.channel.2"
    assert first["session_id"] != second["session_id"]
    assert child["session_id"] not in {first["session_id"], second["session_id"]}
    assert first["assistant"]["debug_intent_label"] is None
    assert child["assistant"]["debug_intent_label"] is None

    denied = request("1001", "turn office test light on")
    assert denied["result"]["status"] == "policy_denied"
    switch = next(item for item in home.list_switches() if item["name"] == "office test light")
    assert switch["state"] == "off"

    child_denied = request("444444444444444444", "turn office test light on")
    assert child_denied["result"]["status"] == "policy_denied"
    assert child_denied["assistant"]["text"] == (
        "I can't control things in the house for you. You can ask me a question or talk with me instead."
    )
    assert switch["state"] == "off"

    repository.close()
    store.close()
