import shutil
from pathlib import Path
from uuid import uuid4

from app.core.main_jarvis import MainJarvis
from app.core.micro_jarvis import MicroDecision, MicroJarvis
from tests.router_support import RegistryBackedTestRouter as JarvisRouter
from app.core.session_store import SessionStore
from app.core.state_machine import RuntimePowerController
from app.core.types import Intent, SessionOwner
from app.schemas.api import AskRequest
from app.services.conversation_history_service import ConversationHistoryService
from app.services.event_log import EventLogService
from app.tools.calendar_service import CalendarService
from app.tools.home_service import HomeService
from app.tools.lists_service import ListsService


class _DefaultRepairBackend:
    def repair_action(self, text: str, context=None):
        context = context or {}
        lowered = text.strip().lower()
        pending_intent = str(context.get("pending_intent") or "").strip().lower()
        pending_entities = context.get("pending_entities") or {}
        if not isinstance(pending_entities, dict):
            pending_entities = {}

        if pending_intent == "calendar.add_event":
            if "whatever words" in lowered:
                return {
                    "status": "resolved_action",
                    "intent": "calendar.add_event",
                    "confidence": 0.91,
                    "reasoning": "pending_context_parse",
                    "entities": {
                        "event_name": "lunch",
                        "start_time": "today at 2pm",
                        "invitees": ["Jordan", "Taylor"],
                    },
                    "missing_fields": [],
                    "source": "backend",
                }

            if "tomorrow at noon" in lowered:
                return {
                    "status": "resolved_action",
                    "intent": "calendar.add_event",
                    "confidence": 0.9,
                    "reasoning": "calendar_pending_followup_with_when",
                    "entities": {
                        "event_title": str(pending_entities.get("event_title") or "dentist appointment"),
                        "when_hint": "tomorrow at noon",
                    },
                    "missing_fields": [],
                    "source": "backend",
                }

            if "lunch with babbers at noon" in lowered:
                return {
                    "status": "resolved_action",
                    "intent": "calendar.add_event",
                    "confidence": 0.88,
                    "reasoning": "calendar_pending_followup_with_title",
                    "entities": {
                        "event_title": "lunch with babbers at noon",
                        "when_hint": str(pending_entities.get("when_hint") or "tomorrow"),
                    },
                    "missing_fields": [],
                    "source": "backend",
                }

            if "call it lunch" in lowered:
                return {
                    "status": "resolved_action",
                    "intent": "calendar.add_event",
                    "confidence": 0.9,
                    "reasoning": "calendar_pending_followup_named_title",
                    "entities": {
                        "event_title": "lunch",
                        "when_hint": str(pending_entities.get("when_hint") or "tomorrow at 2pm"),
                    },
                    "missing_fields": [],
                    "source": "backend",
                }

            return None

        if pending_intent == "home.set_switch":
            if "office" in lowered:
                return {
                    "status": "resolved_action",
                    "intent": "home.set_switch",
                    "confidence": 0.87,
                    "reasoning": "switch_pending_resolved_office",
                    "entities": {
                        "switch_name": "office test light",
                        "action": str(pending_entities.get("action") or "on"),
                    },
                    "missing_fields": [],
                    "source": "backend",
                }
            if "all of them" in lowered or "all lights" in lowered:
                return {
                    "status": "resolved_action",
                    "intent": "home.set_switch",
                    "confidence": 0.89,
                    "reasoning": "switch_pending_resolved_all",
                    "entities": {
                        "switch_name": "all lights",
                        "action": str(pending_entities.get("action") or "on"),
                        "scope": "all",
                    },
                    "missing_fields": [],
                    "source": "backend",
                }
            return None

        if "sync" in lowered and "calendar" in lowered:
            return {
                "status": "not_actionable",
                "reasoning": "calendar_sync_not_supported",
                "message": "Calendar sync is not wired yet.",
                "source": "backend",
            }

        if "house heat" in lowered and "68" in lowered:
            return {
                "status": "not_actionable",
                "reasoning": "thermostat_not_supported",
                "message": "Thermostat control is not wired yet.",
                "inferred_intent": "home.set_thermostat",
                "inferred_entities": {"target_temperature_f": 68},
                "source": "backend",
            }

        if "add dentist appointment to my calendar" in lowered:
            when_hint = "tomorrow" if "tomorrow" in lowered else None
            if when_hint:
                return {
                    "status": "resolved_action",
                    "intent": "calendar.add_event",
                    "confidence": 0.9,
                    "reasoning": "calendar_add_resolved_with_when",
                    "entities": {"event_title": "dentist appointment", "when_hint": when_hint},
                    "missing_fields": [],
                    "source": "backend",
                }
            return {
                "status": "needs_clarification",
                "intent": "calendar.add_event",
                "confidence": 0.82,
                "reasoning": "calendar_add_missing_when",
                "entities": {"event_title": "dentist appointment"},
                "missing_fields": ["when_hint"],
                "question": "When should I schedule it?",
                "source": "backend",
            }

        if "schedule on my calendar tomorrow at 2pm" in lowered:
            return {
                "status": "needs_clarification",
                "intent": "calendar.add_event",
                "confidence": 0.82,
                "reasoning": "calendar_add_missing_title",
                "entities": {"when_hint": "tomorrow at 2pm"},
                "missing_fields": ["event_title"],
                "question": "What should I name the calendar event?",
                "source": "backend",
            }

        if "schedule on my calendar tomorrow" in lowered:
            return {
                "status": "needs_clarification",
                "intent": "calendar.add_event",
                "confidence": 0.82,
                "reasoning": "calendar_add_missing_title",
                "entities": {"when_hint": "tomorrow"},
                "missing_fields": ["event_title"],
                "question": "What should I name the calendar event?",
                "source": "backend",
            }

        if "schedule on my calendar" in lowered:
            return {
                "status": "needs_clarification",
                "intent": "calendar.add_event",
                "confidence": 0.74,
                "reasoning": "calendar_add_missing_fields",
                "entities": {},
                "missing_fields": ["event_title", "when_hint"],
                "question": "What should I name the calendar event?",
                "source": "backend",
            }

        if "opioid settlement fund disbursement committee" in lowered and "calendar" in lowered:
            return {
                "status": "resolved_action",
                "intent": "calendar.add_event",
                "confidence": 0.9,
                "reasoning": "calendar_add_resolved_semantic",
                "entities": {
                    "event_title": "opioid settlement fund disbursement committee",
                    "when_hint": "tomorrow",
                },
                "missing_fields": [],
                "source": "backend",
            }

        if lowered.startswith("turn garage floodlight on"):
            return {
                "status": "resolved_action",
                "intent": "home.set_switch",
                "confidence": 0.9,
                "reasoning": "switch_resolved_unknown_target",
                "entities": {"switch_name": "garage floodlight", "action": "on"},
                "missing_fields": [],
                "source": "backend",
            }

        if lowered.startswith("turn house lights on"):
            return {
                "status": "resolved_action",
                "intent": "home.set_switch",
                "confidence": 0.9,
                "reasoning": "switch_resolved_unknown_house_lights",
                "entities": {"switch_name": "house lights", "action": "on"},
                "missing_fields": [],
                "source": "backend",
            }

        if "ride burrito shells to it" in lowered:
            last_list_name = str(context.get("last_list_name") or "").strip()
            if not last_list_name:
                entity_hints = context.get("entity_hints")
                if not isinstance(entity_hints, list):
                    working_context = context.get("working_context")
                    if isinstance(working_context, dict):
                        entity_hints = working_context.get("entity_hints")
                if isinstance(entity_hints, list):
                    for entity in entity_hints:
                        if not isinstance(entity, dict):
                            continue
                        if str(entity.get("domain") or "").strip().lower() != "lists":
                            continue
                        if str(entity.get("entity_type") or "").strip().lower() != "list":
                            continue
                        candidate = str(entity.get("display_name") or "").strip()
                        if candidate:
                            last_list_name = candidate
                            break
            if last_list_name:
                return {
                    "status": "resolved_action",
                    "intent": "lists.add_item",
                    "confidence": 0.8,
                    "reasoning": "list_add_asr_recovery_from_context",
                    "entities": {"list_name": last_list_name, "item_text": "burrito shells"},
                    "missing_fields": [],
                    "source": "backend",
                }
            return None

        return None


def _build_router_with_log() -> tuple[JarvisRouter, EventLogService]:
    event_log = EventLogService()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(repair_backend=_DefaultRepairBackend()),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=event_log,
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )
    return router, event_log


def _build_router_with_store(session_store: SessionStore) -> JarvisRouter:
    return JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(),
        session_store=session_store,
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )


def test_router_channel_session_reuses_session_id_while_active():
    router = _build_router_with_store(SessionStore())
    first = router.route(
        AskRequest(
            text="show me groceries",
            context={"auto_channel_session": True, "session_channel": "dashboard.command"},
        )
    )
    second = router.route(
        AskRequest(
            text="add milk to groceries",
            context={"auto_channel_session": True, "session_channel": "dashboard.command"},
        )
    )

    assert first["session_id"] == second["session_id"]


def test_router_channel_session_rotates_after_idle_timeout():
    clock = {"now": 0.0}
    store = SessionStore(
        channel_idle_timeout_seconds=60.0,
        time_fn=lambda: float(clock["now"]),
    )
    router = _build_router_with_store(store)
    first = router.route(
        AskRequest(
            text="show me groceries",
            context={"auto_channel_session": True, "session_channel": "dashboard.command"},
        )
    )
    clock["now"] = 61.0
    second = router.route(
        AskRequest(
            text="show me groceries",
            context={"auto_channel_session": True, "session_channel": "dashboard.command"},
        )
    )

    assert first["session_id"] != second["session_id"]


def test_router_channel_session_rotates_on_explicit_wake_command():
    router = _build_router_with_store(SessionStore(channel_idle_timeout_seconds=300.0))
    first = router.route(
        AskRequest(
            text="show me groceries",
            context={"auto_channel_session": True, "session_channel": "dashboard.command"},
        )
    )
    second = router.route(
        AskRequest(
            text="wake up",
            context={"auto_channel_session": True, "session_channel": "dashboard.command"},
        )
    )

    assert first["session_id"] != second["session_id"]


def test_router_includes_channel_session_runtime_metadata():
    router = _build_router_with_store(SessionStore(channel_idle_timeout_seconds=180.0))
    response = router.route(
        AskRequest(
            text="show me groceries",
            user_id="jordan",
            context={"auto_channel_session": True, "session_channel": "dashboard.command"},
        )
    )

    runtime = response.get("session_runtime")
    assert isinstance(runtime, dict)
    channel = runtime.get("channel")
    assert isinstance(channel, dict)
    assert channel.get("channel_key") == "jordan:dashboard.command"
    assert channel.get("session_id") == response["session_id"]
    assert channel.get("expired") is False


def test_router_discord_channel_session_is_shared_across_users():
    router = _build_router_with_store(SessionStore(channel_idle_timeout_seconds=180.0))
    first = router.route(
        AskRequest(
            text="show me groceries",
            user_id="discord-user-1",
            source="discord",
            context={
                "auto_channel_session": True,
                "channel_session_scope": "shared",
                "session_channel": "discord.guild.123.channel.456",
            },
        )
    )
    second = router.route(
        AskRequest(
            text="add milk to groceries",
            user_id="discord-user-2",
            source="discord",
            context={
                "auto_channel_session": True,
                "channel_session_scope": "shared",
                "session_channel": "discord.guild.123.channel.456",
            },
        )
    )

    assert first["session_id"] == second["session_id"]
    channel = second.get("session_runtime", {}).get("channel", {})
    assert channel.get("channel_key") == "discord.guild.123.channel.456"


def test_router_discord_message_auto_wakes_and_forces_main_owner():
    runtime_power = RuntimePowerController()
    runtime_power.sleep()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(),
        session_store=SessionStore(),
        runtime_power=runtime_power,
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    response = router.route(
        AskRequest(
            text="show me groceries",
            user_id="discord-user",
            source="discord",
            context={
                "auto_channel_session": True,
                "channel_session_scope": "shared",
                "session_channel": "discord.guild.123.channel.456",
            },
        )
    )

    assert response["power_state"] == "AWAKE"
    assert response["route"] != "sleep_guard"
    assert response["owner"] == "main_jarvis"


def test_handoff_main_to_micro_after_conversational_turn():
    router, event_log = _build_router_with_log()

    first = router.route(AskRequest(text="help me plan dinners this week", session_id="s2"))
    assert first["route"] == "main_jarvis"
    assert first["owner"] == "main_jarvis"
    assert first["state"] == "CONVERSATIONAL"

    second = router.route(AskRequest(text="add chicken to groceries", session_id="s2"))
    assert second["route"] == "micro_tool"
    assert second["owner"] == "micro_jarvis"
    assert second["result"]["status"] == "ok"

    event_types = [item["event_type"] for item in event_log.recent(limit=200)]
    assert "handoff.main_to_micro" in event_types


def test_router_executes_list_get_fast_command():
    router, _ = _build_router_with_log()
    router.route(AskRequest(text="add milk to groceries", session_id="list-get"))

    response = router.route(AskRequest(text="show me my grocery list", session_id="list-get"))
    assert response["intent"] == "lists.get_items"
    assert response["route"] == "micro_tool"
    assert response["result"]["status"] == "ok"
    assert response["result"]["list_name"] == "groceries"
    assert response["result"]["items"] == ["milk"]


def test_router_executes_list_get_with_hi_jarvis_prefix():
    router, _ = _build_router_with_log()
    response = router.route(AskRequest(text="Hi Jarvis what's on my grocery list", session_id="list-get-hi"))

    assert response["intent"] == "lists.get_items"
    assert response["route"] == "micro_tool"
    assert response["result"]["status"] == "ok"
    assert response["result"]["list_name"] == "groceries"


def test_router_routes_add_to_it_through_context_resolved_micro_execution():
    router, _ = _build_router_with_log()
    router.route(AskRequest(text="add milk to groceries", session_id="list-pronoun"))
    router.route(AskRequest(text="show me groceries", session_id="list-pronoun"))

    response = router.route(AskRequest(text="add tofu to it", session_id="list-pronoun"))
    assert response["intent"] == "lists.add_item"
    assert response["route"] == "micro_tool"
    assert response["result"]["status"] == "ok"
    assert response["result"]["list_name"] == "groceries"
    assert response["result"]["item_text"] == "tofu"


def test_router_routes_add_on_it_through_context_resolved_micro_execution():
    router, _ = _build_router_with_log()
    router.route(AskRequest(text="create easter prep list", session_id="list-pronoun-on"))

    response = router.route(AskRequest(text="add pick up dog poop on it", session_id="list-pronoun-on"))
    assert response["intent"] == "lists.add_item"
    assert response["route"] == "micro_tool"
    assert response["result"]["status"] == "ok"
    assert response["result"]["list_name"] == "easter prep"
    assert response["result"]["item_text"] == "pick up dog poop"


def test_router_splits_compound_list_add_into_multiple_items():
    router, _ = _build_router_with_log()
    response = router.route(
        AskRequest(
            text="add bananas tofu and burrito shells to grocery",
            session_id="list-compound-items",
        )
    )

    assert response["intent"] == "lists.add_item"
    assert response["result"]["status"] == "ok"
    assert response["result"]["added_items"] == ["bananas", "tofu", "burrito shells"]

    check = router.route(
        AskRequest(
            text="show me grocery list",
            session_id="list-compound-items",
        )
    )
    assert check["intent"] == "lists.get_items"
    assert check["result"]["status"] == "ok"
    assert check["result"]["items"] == ["bananas", "tofu", "burrito shells"]


def test_router_executes_numbered_five_item_create_and_add_plan():
    router, event_log = _build_router_with_log()
    response = router.route(
        AskRequest(
            text=(
                "lets make a list called ICDP party to-do. On it lets add- "
                "1) Rocket Fundraiser (Jordan), 2) location testing (Jordan), "
                "food prep (Taylor), yard layout (Taylor), Get tables from kelly (Taylor)"
            ),
            session_id="list-numbered-create-and-add",
        )
    )

    assert response["intent"] == "conversation.general"
    assert response["route"] == "main_jarvis"
    assert response["result"]["status"] == "executed"
    assert response["result"]["execution"]["success_count"] == 6
    assert response["assistant"]["text"] == "Created `ICDP party to-do` and added 5 item(s). (list action)"
    executed = [
        event
        for event in event_log.recent(limit=200)
        if event["event_type"] == "main.plan.command.executed"
    ]
    assert len(executed) == 6

    check = router.route(
        AskRequest(
            text="whats on the icdp party to-do",
            session_id="list-numbered-create-and-add",
        )
    )
    assert check["result"]["items"] == [
        "Rocket Fundraiser (Jordan)",
        "location testing (Jordan)",
        "food prep (Taylor)",
        "yard layout (Taylor)",
        "Get tables from kelly (Taylor)",
    ]


def test_router_executes_calendar_view_fast_command():
    router, _ = _build_router_with_log()
    router.route(AskRequest(text="add dentist appointment to my calendar tomorrow", session_id="cal-view"))

    response = router.route(AskRequest(text="what's on my calendar today", session_id="cal-view"))
    assert response["intent"] == "calendar.view"
    assert response["route"] == "micro_tool"
    assert response["result"]["status"] == "ok"
    assert response["result"]["source"] == "local_stub"
    assert response["result"]["event_count"] == 1


def test_router_executes_calendar_view_for_common_calendar_misspelling():
    router, _ = _build_router_with_log()
    response = router.route(AskRequest(text="what is on my calandar for today", session_id="cal-view-misspelling"))

    assert response["intent"] == "calendar.view"
    assert response["route"] == "micro_tool"
    assert response["result"]["status"] == "ok"


def test_router_handles_natural_language_calendar_add_event_phrase():
    router, _ = _build_router_with_log()
    response = router.route(
        AskRequest(
            text=(
                "Jarvis can you add an event on my calendar tomorrow for "
                "opioid settlement fund disbursement committee"
            ),
            session_id="cal-natural",
        )
    )

    assert response["intent"] == "calendar.add_event"
    assert response["route"] == "main_jarvis_repair"
    assert response["owner"] == "main_jarvis"
    assert response["result"]["status"] == "ok"
    assert response["result"]["event"]["event_title"] == "opioid settlement fund disbursement committee"
    assert response["result"]["event"]["when_hint"] == "tomorrow"


def test_router_calendar_main_repair_keeps_high_conf_micro_entities_and_extracts_invitees():
    class MicroBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "calendar.add_event",
                "confidence": 0.95,
                "entities": {
                    "event_title": "dinner",
                    "when_hint": "five o'clock",
                    "person_name": "Jordan",
                },
                "ambiguity_flags": ["short"],
                "reasoning": "model_backend",
            }

    class RepairBackend:
        def repair_action(self, text: str, context=None):
            return {
                "status": "resolved_action",
                "intent": "calendar.add_event",
                "confidence": 0.79,
                "reasoning": "main_repair_calendar_add",
                "entities": {
                    "event_title": "dinner onto my calendar today at five o'clock and invite Jordan",
                    "when_hint": "today",
                },
                "source": "heuristic",
            }

    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=MicroBackend()),
        main_jarvis=MainJarvis(repair_backend=RepairBackend()),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    response = router.route(
        AskRequest(
            text="add dinner onto my calendar today at five o'clock and invite Jordan",
            session_id="cal-main-repair-prefers-micro",
        )
    )

    assert response["route"] == "main_jarvis_repair"
    assert response["result"]["status"] == "ok"
    assert response["classification"]["reasoning"].endswith("_using_high_conf_micro_calendar_entities")
    assert response["classification"]["recovered_from"]["entities"]["event_title"] == "dinner"
    assert response["result"]["event"]["event_title"] == "dinner"
    assert response["result"]["event"]["when_hint"] == "five o'clock"
    assert "person_name" not in response["result"]["event"]
    assert response["result"]["event"]["invitee_names"] == ["Jordan"]
    assert response["result"]["invite_flow"]["recognized_invitees"] == ["Jordan"]


def test_router_calendar_add_requires_explicit_invite_phrase_for_invitees():
    class MicroBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "calendar.add_event",
                "confidence": 0.97,
                "entities": {
                    "event_title": "Jen coming to stay",
                    "when_hint": "tomorrow",
                    "invitee_names": ["Jen"],
                },
                "ambiguity_flags": [],
                "reasoning": "model_backend",
            }

    class RepairBackend:
        def repair_action(self, text: str, context=None):
            return {
                "status": "resolved_action",
                "intent": "calendar.add_event",
                "confidence": 0.82,
                "reasoning": "main_repair_calendar_add",
                "entities": {
                    "event_title": "Jen coming to stay",
                    "when_hint": "tomorrow",
                    "invitee_names": ["Jen"],
                },
                "source": "backend",
            }

    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=MicroBackend()),
        main_jarvis=MainJarvis(repair_backend=RepairBackend()),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    response = router.route(
        AskRequest(
            text="add Jen coming to stay to my calendar tomorrow",
            session_id="cal-invite-explicit-only",
        )
    )

    assert response["route"] == "main_jarvis_repair"
    assert response["result"]["status"] == "ok"
    assert response["result"]["event"]["event_title"] == "Jen coming to stay"
    assert response["result"]["event"]["invitee_names"] == []


def test_router_calendar_add_extracts_invitees_from_send_to_phrase():
    class MicroBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "calendar.add_event",
                "confidence": 0.95,
                "entities": {
                    "event_title": "dinner",
                    "when_hint": "tomorrow",
                },
                "ambiguity_flags": [],
                "reasoning": "model_backend",
            }

    class RepairBackend:
        def repair_action(self, text: str, context=None):
            return {
                "status": "resolved_action",
                "intent": "calendar.add_event",
                "confidence": 0.79,
                "reasoning": "main_repair_calendar_add",
                "entities": {
                    "event_title": "dinner",
                    "when_hint": "tomorrow",
                },
                "source": "backend",
            }

    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=MicroBackend()),
        main_jarvis=MainJarvis(repair_backend=RepairBackend()),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    response = router.route(
        AskRequest(
            text="add dinner to my calendar tomorrow and send to Jordan and Taylor",
            session_id="cal-send-to-invitees",
        )
    )

    assert response["route"] == "main_jarvis_repair"
    assert response["result"]["status"] == "ok"
    assert response["result"]["event"]["invitee_names"] == ["Jordan", "Taylor"]
    assert response["result"]["invite_flow"]["recognized_invitees"] == ["Jordan", "Taylor"]


def test_router_requires_schedule_for_calendar_add_and_then_executes_after_clarification():
    router, _ = _build_router_with_log()
    first = router.route(
        AskRequest(
            text="add dentist appointment to my calendar",
            session_id="cal-need-when",
        )
    )

    assert first["intent"] == "calendar.add_event"
    assert first["route"] == "main_jarvis_repair"
    assert first["owner"] == "main_jarvis"
    assert first["result"]["status"] == "needs_clarification"
    assert "when_hint" in first["result"]["missing_fields"]
    assert first["dialog"]["mode"] == "conversation_pending"

    second = router.route(
        AskRequest(
            text="tomorrow at noon",
            session_id="cal-need-when",
        )
    )

    assert second["intent"] == "calendar.add_event"
    assert second["route"] == "main_jarvis_repair"
    assert second["result"]["status"] == "ok"
    assert second["result"]["event"]["event_title"] == "dentist appointment"
    assert second["result"]["event"]["when_hint"] == "tomorrow at noon"
    assert second["state"] == "IDLE"


def test_router_resolves_make_that_all_day_against_latest_calendar_event():
    class CalendarMutationRepairBackend:
        def repair_action(self, text: str, context=None):
            context = context or {}
            micro_intent = str(context.get("micro_intent") or "")
            entities = dict(context.get("micro_entities") or {})
            return {
                "status": "resolved_action",
                "intent": micro_intent,
                "confidence": 0.95,
                "reasoning": "calendar_mutation_test",
                "entities": entities,
                "missing_fields": [],
                "source": "backend",
            }

    class FakeGoogleLive:
        def __init__(self):
            self.updated = []

        def add_event(self, *, event_title, when_hint, invitee_names=None):
            return {
                "status": "ok",
                "source": "google_live",
                "sync_status": "synced_to_google",
                "event": {
                    "event_title": event_title,
                    "when_hint": when_hint,
                    "google_event_id": "event-arcese",
                    "host_calendar_id": "personal@example.com",
                    "invitee_names": invitee_names or [],
                },
            }

        def update_event(self, **kwargs):
            self.updated.append(kwargs)
            return {
                "status": "ok",
                "source": "google_live",
                "sync_status": "synced_to_google",
                "event": {
                    "event_title": kwargs["event_reference"],
                    "all_day": kwargs["all_day"],
                    "google_event_id": kwargs["event_id"],
                    "host_calendar_id": kwargs["calendar_id"],
                },
            }

    google = FakeGoogleLive()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(repair_backend=CalendarMutationRepairBackend()),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries"]),
        calendar_service=CalendarService(google_live=google),
        home_service=HomeService(default_switch_names=["office test light"]),
    )
    first = router.route(
        AskRequest(
            text="add Dinner with Arcese family to my calendar on August 28 at 5pm",
            session_id="calendar-update-followup",
        )
    )
    second = router.route(
        AskRequest(
            text="please make that an all day event actually",
            session_id="calendar-update-followup",
        )
    )

    assert first["result"]["status"] == "ok"
    assert second["intent"] == "calendar.update_event"
    assert second["route"] == "main_jarvis_repair"
    assert second["result"]["status"] == "ok"
    assert second["result"]["event"]["all_day"] is True
    assert google.updated == [
        {
            "event_reference": "Dinner with Arcese family",
            "new_event_title": None,
            "new_when_hint": None,
            "all_day": True,
            "event_id": "event-arcese",
            "calendar_id": "personal@example.com",
        }
    ]


def test_router_resolves_make_that_all_day_from_cross_session_calendar_memory():
    class EchoRepairBackend:
        def repair_action(self, text: str, context=None):
            context = context or {}
            return {
                "status": "resolved_action",
                "intent": str(context.get("micro_intent") or ""),
                "confidence": 0.95,
                "reasoning": "memory_handoff_test",
                "entities": dict(context.get("micro_entities") or {}),
                "missing_fields": [],
                "source": "backend",
            }

    class Memory:
        def recent(self, limit=50):
            return [
                {
                    "session_id": "older-session",
                    "user_id": "jordan",
                    "intent": "calendar.add_event",
                    "request_text": "Please create a calandar event for Sept 19 called ICDP party",
                    "response_summary": "ok",
                }
            ]

        def record_interaction(self, **kwargs):
            return None

    class Google:
        def __init__(self):
            self.updated = []

        def update_event(self, **kwargs):
            self.updated.append(kwargs)
            return {
                "status": "ok",
                "source": "google_live",
                "event": {"event_title": kwargs["event_reference"], "all_day": kwargs["all_day"]},
            }

    google = Google()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(repair_backend=EchoRepairBackend()),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=Memory(),
        lists_service=ListsService(default_list_names=["groceries"]),
        calendar_service=CalendarService(google_live=google),
        home_service=HomeService(default_switch_names=["office test light"]),
    )

    response = router.route(
        AskRequest(
            text="please make that an all day event actually",
            session_id="new-session-after-restart",
            user_id="jordan",
        )
    )

    assert response["intent"] == "calendar.update_event"
    assert response["result"]["status"] == "ok"
    assert google.updated[0]["event_reference"] == "ICDP party"
    assert google.updated[0]["all_day"] is True


def test_router_keeps_followup_context_for_unknown_switch_recovery():
    router, _ = _build_router_with_log()
    first = router.route(
        AskRequest(
            text="turn garage floodlight on",
            session_id="switch-clarify",
        )
    )

    assert first["intent"] == "home.set_switch"
    assert first["route"] == "main_jarvis_repair"
    assert first["state"] == "AWAITING_CONFIRMATION"
    assert first["result"]["status"] == "unknown_switch"
    assert "switch_name" in first["result"]["missing_fields"]
    assert first["dialog"]["mode"] == "conversation_pending"

    second = router.route(
        AskRequest(
            text="I think you have it called Office",
            session_id="switch-clarify",
        )
    )

    assert second["intent"] == "home.set_switch"
    assert second["route"] == "main_jarvis_repair"
    assert second["result"]["status"] == "ok"
    assert second["result"]["switch_name"] == "office test light"
    assert second["result"]["action"] == "on"
    assert second["state"] == "IDLE"


def test_router_uses_main_repair_when_micro_is_unknown():
    router, event_log = _build_router_with_log()
    response = router.route(
        AskRequest(
            text="Schedule opioid settlement fund disbursement committee for tomorrow on my calendar",
            session_id="cal-repair",
        )
    )

    assert response["intent"] == "calendar.add_event"
    assert response["route"] == "main_jarvis_repair"
    assert response["owner"] == "main_jarvis"
    assert response["result"]["status"] == "ok"
    assert response["result"]["event"]["event_title"] == "opioid settlement fund disbursement committee"
    assert response["result"]["event"]["when_hint"] == "tomorrow"
    assert response["result"]["repaired_by"] == "main_jarvis"
    assert response["result"]["repair_source"] == "backend"
    assert response["classification"]["repair_status"] == "resolved_action"
    assert response["classification"]["recovered_from"]["intent"] == "unknown"

    event_types = [item["event_type"] for item in event_log.recent(limit=200)]
    assert "main.repair.attempted" in event_types
    assert "main.repair.executed" in event_types


def test_router_handles_clarification_followup_for_pending_calendar_event():
    router, event_log = _build_router_with_log()
    first = router.route(
        AskRequest(
            text="schedule on my calendar tomorrow",
            session_id="cal-clarify",
        )
    )

    assert first["route"] == "main_jarvis_repair"
    assert first["result"]["status"] == "needs_clarification"
    assert first["state"] == "AWAITING_CONFIRMATION"
    assert first["result"]["missing_fields"] == ["event_title"]
    assert first["dialog"]["mode"] == "conversation_pending"
    assert first["dialog"]["turn_complete"] is False
    assert "What should I name the calendar event?" in first["assistant"]["text"]

    second = router.route(
        AskRequest(
            text="lunch with babbers at noon",
            session_id="cal-clarify",
        )
    )

    assert second["route"] == "main_jarvis_repair"
    assert second["result"]["status"] == "ok"
    assert second["result"]["event"]["event_title"] == "lunch with babbers at noon"
    assert second["state"] == "IDLE"
    assert second["dialog"]["mode"] == "command_action"
    assert second["dialog"]["turn_complete"] is True
    assert second["assistant"]["text"].startswith('Added "lunch with babbers at noon"')

    event_types = [item["event_type"] for item in event_log.recent(limit=200)]
    assert "main.repair.clarification.executed" in event_types


def test_router_calendar_clarification_strips_invite_clause_from_title():
    router, _ = _build_router_with_log()
    first = router.route(
        AskRequest(
            text="schedule on my calendar tomorrow at 2pm",
            session_id="cal-title-clean",
        )
    )

    assert first["route"] == "main_jarvis_repair"
    assert first["result"]["status"] == "needs_clarification"
    assert first["result"]["missing_fields"] == ["event_title"]

    second = router.route(
        AskRequest(
            text="Let's call it lunch and invite Jordan and Taylor",
            session_id="cal-title-clean",
        )
    )

    assert second["route"] == "main_jarvis_repair"
    assert second["result"]["status"] == "ok"
    assert second["result"]["event"]["event_title"] == "lunch"
    assert second["result"]["event"]["when_hint"] == "tomorrow at 2pm"
    assert second["result"]["event"]["invitee_names"] == ["Jordan", "Taylor"]


def test_router_pending_clarification_uses_model_layer_with_context_first():
    class ClarificationBackend:
        def repair_action(self, text: str, context=None):
            if "schedule on my calendar" in text.lower():
                return {
                    "status": "needs_clarification",
                    "intent": "calendar.add_event",
                    "confidence": 0.72,
                    "reasoning": "calendar_add_missing_fields",
                    "entities": {},
                    "missing_fields": ["event_title", "when_hint"],
                    "question": "What should I name the calendar event?",
                    "source": "backend",
                }
            if "whatever words" in text.lower():
                return {
                    "status": "resolved_action",
                    "intent": "calendar.add_event",
                    "confidence": 0.91,
                    "reasoning": "pending_context_parse",
                    "entities": {
                        "event_name": "lunch",
                        "start_time": "today at 2pm",
                        "invitees": ["Jordan", "Taylor"],
                    },
                    "missing_fields": [],
                    "source": "backend",
                }
            return None

    event_log = EventLogService()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(repair_backend=ClarificationBackend()),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=event_log,
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    first = router.route(
        AskRequest(
            text="schedule on my calendar",
            session_id="cal-model-clarify",
        )
    )
    assert first["result"]["status"] == "needs_clarification"

    second = router.route(
        AskRequest(
            text="whatever words",
            session_id="cal-model-clarify",
        )
    )
    assert second["result"]["status"] == "ok"
    assert second["result"]["event"]["event_title"] == "lunch"
    assert second["result"]["event"]["when_hint"] == "today at 2pm"
    assert second["result"]["event"]["invitee_names"] == []

    event_types = [item["event_type"] for item in event_log.recent(limit=400)]
    assert "main.repair.clarification.attempted" in event_types


def test_router_uses_micro_entities_when_main_repair_drops_required_switch_fields():
    class DriftBackend:
        def repair_action(self, text: str, context=None):
            return {
                "status": "resolved_action",
                "intent": "home.set_switch",
                "confidence": 1.0,
                "reasoning": "model_drift_all_lights_shape",
                "entities": {"action": "turn_off_all_lights"},
                "missing_fields": [],
                "source": "backend",
            }

    event_log = EventLogService()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(repair_backend=DriftBackend()),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=event_log,
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    response = router.route(
        AskRequest(
            text="turn off all the lights",
            session_id="all-lights-repair-fallback",
        )
    )

    assert response["intent"] == "home.set_switch"
    assert response["route"] == "main_jarvis"
    assert response["result"]["status"] == "executed"
    execution = response["result"]["execution"]
    assert execution["status"] == "ok"
    assert execution["success_count"] == execution["requested_count"]


def test_router_main_plan_execution_includes_agent_loop_trace():
    router, _ = _build_router_with_log()
    response = router.route(
        AskRequest(
            text="Jarvis lets create a weekend list and add bananas to it",
            session_id="agent-loop-trace",
        )
    )

    assert response["route"] == "main_jarvis"
    assert response["result"]["status"] == "executed"
    execution = response["result"]["execution"]
    assert execution["status"] == "ok"
    assert execution["loop_state"] == "COMPLETED"
    assert len(execution["agent_loop"]["trace"]) >= 2
    assert execution["agent_loop"]["context_budget"]["used_tokens_estimate"] >= 1


def test_router_main_plan_policy_block_in_child_context_and_token_session_persisted():
    event_log = EventLogService()
    session_store = SessionStore()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(),
        session_store=session_store,
        runtime_power=RuntimePowerController(),
        event_log=event_log,
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
        main_agent_content_policy_enabled=True,
        main_agent_content_policy_children_only=True,
        main_agent_content_policy_blocked_patterns=[r"\bweapon\b"],
    )

    response = router.route(
        AskRequest(
            text="Jarvis lets create a weapon list and add apples to it",
            session_id="agent-loop-policy-block",
            context={"kid_mode": True},
        )
    )

    assert response["route"] == "main_jarvis"
    assert response["result"]["status"] == "planned"
    execution = response["result"]["execution"]
    assert execution["status"] == "needs_input"
    assert execution["loop_state"] == "WAITING_FOR_USER"
    assert execution["agent_loop"]["policy"]["latest"]["status"] == "blocked"

    persisted = session_store.get_or_create(
        session_id="agent-loop-policy-block",
        user_id="local_user",
        source="web",
    )
    token_session = persisted.context_reference.get("main_agent_token_session")
    assert isinstance(token_session, dict)
    assert isinstance(token_session.get("turn_summaries"), list)
    assert token_session.get("turn_summaries")


def test_should_not_attempt_main_repair_for_complete_all_lights_switch_decision():
    decision = MicroDecision(
        intent=Intent.HOME_SET_SWITCH,
        confidence=0.97,
        entities={"switch_name": "all lights", "action": "off", "scope": "all"},
        ambiguity_flags=["bulk_scope_requires_planning"],
        recommended_owner=SessionOwner.MAIN,
        reasoning="all_lights_pattern",
    )

    assert JarvisRouter._should_attempt_main_repair(decision) is False


def test_router_executes_high_confidence_short_list_get_from_model():
    class MicroBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "lists.get_items",
                "confidence": 1.0,
                "entities": {"list_name": "grocery"},
                "ambiguity_flags": ["short"],
                "reasoning": "short_query_inferred_grocery",
            }

    event_log = EventLogService()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=MicroBackend()),
        main_jarvis=MainJarvis(),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=event_log,
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    response = router.route(
        AskRequest(
            text="What's on my grocery list Jarvis",
            session_id="short-list-get",
        )
    )

    assert response["intent"] == "lists.get_items"
    assert response["route"] == "micro_tool"
    assert response["owner"] == "micro_jarvis"
    assert response["result"]["status"] == "ok"
    assert response["result"]["list_name"] == "groceries"


def test_router_executes_fast_intent_when_routed_to_main_without_plan():
    class MicroBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "lists.get_items",
                "confidence": 1.0,
                "entities": {"list_name": "groceries"},
                "ambiguity_flags": ["unknown_intent"],
                "reasoning": "forced_main_route_for_regression_test",
            }

    event_log = EventLogService()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=MicroBackend()),
        main_jarvis=MainJarvis(),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=event_log,
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    response = router.route(
        AskRequest(
            text="what's on my groceries list",
            session_id="main-fast-fallback",
        )
    )

    assert response["intent"] == "lists.get_items"
    assert response["route"] == "main_jarvis"
    assert response["owner"] == "main_jarvis"
    assert response["result"]["status"] == "ok"
    assert response["result"]["list_name"] == "groceries"
    assert response["result"]["executed_by"] == "main_fast_fallback"
    assert response["model_runtime"]["main_labeled_count"] == 1
    assert response["model_runtime"]["micro_labeled_count"] == 0
    assert response["model_runtime"]["larger_models_active"] is True

    event_types = [item["event_type"] for item in event_log.recent(limit=400)]
    assert "main.fast_fallback.executed" in event_types


def test_router_labels_main_for_main_repair_even_when_intent_is_fast_command():
    router, _ = _build_router_with_log()
    response = router.route(
        AskRequest(
            text="what is on blue list",
            session_id="main-repair-runtime-label",
        )
    )

    assert response["route"] == "main_jarvis_repair"
    assert response["intent"] == "lists.get_items"
    assert response["model_runtime"]["main_labeled_count"] == 1
    assert response["model_runtime"]["micro_labeled_count"] == 0
    assert response["model_runtime"]["larger_models_active"] is True


def test_router_cancels_pending_clarification_on_nevermind():
    router, _ = _build_router_with_log()
    first = router.route(
        AskRequest(
            text="add dentist appointment to my calendar",
            session_id="cal-cancel",
        )
    )
    assert first["result"]["status"] == "needs_clarification"

    second = router.route(
        AskRequest(
            text="never mind",
            session_id="cal-cancel",
        )
    )

    assert second["route"] == "main_jarvis_repair"
    assert second["intent"] == "conversation.general"
    assert second["result"]["status"] == "cancelled"
    assert "did not make any changes" in second["result"]["message"].lower()
    assert second["state"] == "IDLE"


def test_router_exit_skill_phrase_cancels_pending_and_resets_to_listening():
    router, _ = _build_router_with_log()
    first = router.route(
        AskRequest(
            text="add dentist appointment to my calendar",
            session_id="exit-skill-pending",
        )
    )
    assert first["result"]["status"] == "needs_clarification"
    assert first["state"] == "AWAITING_CONFIRMATION"

    second = router.route(
        AskRequest(
            text="jarvis, exit this skill",
            session_id="exit-skill-pending",
        )
    )

    assert second["route"] == "session_control"
    assert second["intent"] == "conversation.general"
    assert second["owner"] == "system"
    assert second["state"] == "IDLE"
    assert second["result"]["status"] == "cancelled"
    assert second["result"]["cancelled_intent"] == "calendar.add_event"
    assert "exited current skill context" in second["result"]["message"].lower()

    third = router.route(
        AskRequest(
            text="tomorrow at noon",
            session_id="exit-skill-pending",
        )
    )
    assert third["intent"] != "calendar.add_event"


def test_router_exit_skill_phrase_clears_main_sticky_followup():
    router, _ = _build_router_with_log()
    first = router.route(
        AskRequest(
            text="add dentist appointment to my calendar",
            session_id="exit-skill-sticky",
        )
    )
    assert first["state"] == "AWAITING_CONFIRMATION"

    second = router.route(
        AskRequest(
            text="exit skill",
            session_id="exit-skill-sticky",
        )
    )
    assert second["route"] == "session_control"
    assert second["owner"] == "system"
    assert second["state"] == "IDLE"

    third = router.route(
        AskRequest(
            text="turn kitchen light on",
            session_id="exit-skill-sticky",
        )
    )
    assert third["route"] == "micro_tool"
    assert third["owner"] == "micro_jarvis"
    assert third["intent"] == "home.set_switch"
    assert third["result"]["status"] == "ok"


def test_router_enters_conversational_clarification_for_ambiguous_mid_confidence_repair():
    class MicroBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "lists.add_item",
                "confidence": 0.94,
                "entities": {"item_text": "milk", "list_name": "groceries"},
                "ambiguity_flags": ["deictic_list_reference"],
                "reasoning": "model_backend",
            }

    class RepairBackend:
        def repair_action(self, text: str, context=None):
            return {
                "status": "resolved_action",
                "intent": "lists.add_item",
                "confidence": 0.66,
                "reasoning": "repair_mid_confidence",
                "entities": {"item_text": "milk", "list_name": "groceries"},
                "source": "backend",
            }

    session_store = SessionStore()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=MicroBackend(), heuristic_fallback_enabled=False),
        main_jarvis=MainJarvis(repair_backend=RepairBackend()),
        session_store=session_store,
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    response = router.route(
        AskRequest(
            text="add milk to groceries",
            session_id="confidence-gate-mid",
        )
    )

    assert response["route"] == "main_jarvis_repair"
    assert response["result"]["status"] == "needs_clarification"
    assert response["result"]["confidence_gate"] == "ambiguous_mid_confidence"
    assert response["result"]["missing_fields"] == ["list_name"]
    assert response["state"] == "AWAITING_CONFIRMATION"

    persisted = session_store.get_or_create(
        session_id="confidence-gate-mid",
        user_id="local_user",
        source="web",
    )
    assert persisted.context_reference.get("main_sticky_followup_turns_remaining") == 2


def test_router_enters_conversational_clarification_for_low_confidence_repair():
    class MicroBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "unknown",
                "confidence": 0.4,
                "entities": {},
                "ambiguity_flags": [],
                "reasoning": "model_backend",
            }

    class RepairBackend:
        def repair_action(self, text: str, context=None):
            return {
                "status": "resolved_action",
                "intent": "lists.add_item",
                "confidence": 0.49,
                "reasoning": "repair_low_confidence",
                "entities": {"item_text": "milk", "list_name": "groceries"},
                "source": "backend",
            }

    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=MicroBackend(), heuristic_fallback_enabled=False),
        main_jarvis=MainJarvis(repair_backend=RepairBackend()),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    response = router.route(
        AskRequest(
            text="add milk to groceries",
            session_id="confidence-gate-low",
        )
    )

    assert response["route"] == "main_jarvis_repair"
    assert response["result"]["status"] == "needs_clarification"
    assert response["result"]["confidence_gate"] == "low_confidence"
    assert response["result"]["missing_fields"] == ["list_name"]
    assert response["state"] == "AWAITING_CONFIRMATION"


def test_router_keeps_main_sticky_for_followup_turns_after_clarification_prompt():
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
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    first = router.route(
        AskRequest(
            text="add milk to blue list",
            session_id="sticky-followup",
        )
    )
    assert first["result"]["status"] == "unknown_list"
    assert first["state"] == "AWAITING_CONFIRMATION"

    second = router.route(
        AskRequest(
            text="what is on my grocery list",
            session_id="sticky-followup",
        )
    )
    assert second["owner"] == "main_jarvis"

    persisted_after_second = session_store.get_or_create(
        session_id="sticky-followup",
        user_id="local_user",
        source="web",
    )
    assert persisted_after_second.context_reference.get("main_sticky_followup_turns_remaining") == 1

    third = router.route(
        AskRequest(
            text="what is on my to-do list",
            session_id="sticky-followup",
        )
    )
    assert third["owner"] == "main_jarvis"

    persisted_after_third = session_store.get_or_create(
        session_id="sticky-followup",
        user_id="local_user",
        source="web",
    )
    assert "main_sticky_followup_turns_remaining" not in persisted_after_third.context_reference


def test_router_pending_list_clarification_resolves_yes_plus_suggested_name_with_strict_mode():
    router, _ = _build_router_with_log()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "easter prep", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
        main_pending_clarification_heuristic_fallback_enabled=False,
    )

    first = router.route(
        AskRequest(
            text="Jarvis what's on my Easter list",
            session_id="strict-list-followup",
        )
    )
    assert first["result"]["status"] == "unknown_list"
    assert first["state"] == "AWAITING_CONFIRMATION"

    second = router.route(
        AskRequest(
            text="yeah my Easter prep list",
            session_id="strict-list-followup",
        )
    )
    assert second["intent"] == "lists.get_items"
    assert second["route"] == "main_jarvis_repair"
    assert second["result"]["status"] == "ok"
    assert second["result"]["list_name"] == "easter prep"


def test_router_pending_list_clarification_resolves_show_me_phrase_to_existing_list():
    router, _ = _build_router_with_log()
    first = router.route(
        AskRequest(
            text="what is on easter prep list",
            session_id="pending-list-followup-show-me",
        )
    )
    assert first["result"]["status"] == "unknown_list"
    assert first["state"] == "AWAITING_CONFIRMATION"

    second = router.route(
        AskRequest(
            text="show me grocery list",
            session_id="pending-list-followup-show-me",
        )
    )
    assert second["intent"] == "lists.get_items"
    assert second["route"] == "main_jarvis_repair"
    assert second["result"]["status"] == "ok"
    assert second["result"]["list_name"] == "groceries"


def test_router_pending_clarification_uses_context_contract_when_main_returns_no_updates():
    class NullAfterFirstRepairBackend:
        def repair_action(self, text: str, context=None):
            if "add dentist appointment to my calendar" in text.lower():
                return {
                    "status": "needs_clarification",
                    "intent": "calendar.add_event",
                    "confidence": 0.82,
                    "reasoning": "calendar_add_missing_when",
                    "entities": {"event_title": "dentist appointment"},
                    "missing_fields": ["when_hint"],
                    "question": "When should I schedule it?",
                    "source": "backend",
                }
            return None

    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(repair_backend=NullAfterFirstRepairBackend()),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
        main_pending_clarification_heuristic_fallback_enabled=False,
    )

    first = router.route(
        AskRequest(
            text="add dentist appointment to my calendar",
            session_id="strict-clarification-ask-again",
        )
    )
    assert first["result"]["status"] == "needs_clarification"

    second = router.route(
        AskRequest(
            text="tomorrow at noon",
            session_id="strict-clarification-ask-again",
        )
    )
    assert second["route"] == "main_jarvis_repair"
    assert second["result"]["status"] == "ok"
    assert second["result"]["event"]["event_title"] == "dentist appointment"
    assert second["result"]["event"]["when_hint"] == "tomorrow at noon"
    assert second["state"] == "IDLE"


def test_router_returns_not_actionable_for_calendar_sync_request():
    router, _ = _build_router_with_log()
    response = router.route(
        AskRequest(
            text="can you sync my calendar again",
            session_id="cal-sync",
        )
    )

    assert response["route"] == "main_jarvis_repair"
    assert response["result"]["status"] == "not_actionable"
    assert "sync" in str(response["result"].get("message") or "").lower()


def test_router_defers_generic_not_actionable_to_conversation_reply():
    class GenericRepairBackend:
        def repair_action(self, text: str, context=None):
            return {
                "status": "not_actionable",
                "reasoning": "model_no_supported_action",
                "message": "I could not map that request to a supported action yet.",
                "source": "backend",
            }

    class ConversationBackend:
        def respond(self, text: str, context=None):
            return "Great question. Start with protein, add a quick sauce, and pair with rice."

    router = JarvisRouter(
        micro_jarvis=MicroJarvis(),
        main_jarvis=MainJarvis(
            repair_backend=GenericRepairBackend(),
            conversation_backend=ConversationBackend(),
        ),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    response = router.route(
        AskRequest(
            text="how do I make a fast dinner with tofu",
            session_id="conversation-fallback",
        )
    )

    assert response["route"] == "main_jarvis"
    assert response["intent"] == "conversation.general"
    assert response["result"]["status"] == "conversation"
    assert response["result"]["conversation_source"] == "model"
    assert "protein" in response["result"]["message"].lower()


def test_informational_unknowns_bypass_action_repair_and_reach_conversation():
    class UnknownMicroBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "unknown",
                "confidence": 0.95,
                "entities": {},
                "ambiguity_flags": ["unknown_intent"],
                "reasoning": "test_unknown",
            }

    class RepairBackend:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def repair_action(self, text: str, context=None):
            self.calls.append(text)
            return {
                "status": "not_actionable",
                "reasoning": "capability_gap",
                "message": "I cannot answer that.",
                "source": "backend",
            }

    class ConversationBackend:
        def respond(self, text: str, context=None):
            return f"Conversation answer for: {text}"

    repair = RepairBackend()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=UnknownMicroBackend(), heuristic_fallback_enabled=False),
        main_jarvis=MainJarvis(
            repair_backend=repair,
            conversation_backend=ConversationBackend(),
        ),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light"]),
    )

    for index, text in enumerate(
        (
            "can you tell me a recipe for pancakes",
            "What is the best lord of the rings movie",
            "How do I stop beetles from eating my fruit tree?",
        )
    ):
        response = router.route(AskRequest(text=text, session_id=f"conversation-lane-{index}"))
        assert response["route"] == "main_jarvis"
        assert response["intent"] == "conversation.general"
        assert response["result"]["status"] == "conversation"
        assert response["result"]["conversation_source"] == "model"

    assert repair.calls == []


def test_elliptical_lion_followup_stays_out_of_action_repair():
    class UnknownMicroBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "unknown",
                "confidence": 0.95,
                "entities": {},
                "ambiguity_flags": ["unknown_intent"],
                "reasoning": "test_unknown",
            }

    class RepairBackend:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def repair_action(self, text: str, context=None):
            self.calls.append(text)
            return {"status": "not_actionable", "message": "wrong lane", "source": "backend"}

    class ConversationBackend:
        def respond(self, text: str, context=None):
            if "stuck" in text.lower():
                return "Small debris can catch in a mane, but it normally does not immobilize the lion."
            return "A male lion's mane can cover and cushion the neck during fights."

    repair = RepairBackend()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=UnknownMicroBackend(), heuristic_fallback_enabled=False),
        main_jarvis=MainJarvis(
            repair_backend=repair,
            conversation_backend=ConversationBackend(),
        ),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light"]),
    )

    first = router.route(
        AskRequest(text="Why do male lions have big manes", session_id="lion-lane")
    )
    second = router.route(
        AskRequest(text="do things get stuck in it during a fight", session_id="lion-lane")
    )

    assert first["route"] == "main_jarvis"
    assert second["route"] == "main_jarvis"
    assert second["result"]["status"] == "conversation"
    assert "debris" in second["result"]["message"].lower()
    assert repair.calls == []


def test_explicit_web_search_stays_out_of_action_repair():
    class UnknownMicroBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "unknown",
                "confidence": 0.4,
                "entities": {},
                "ambiguity_flags": ["unknown_intent"],
                "reasoning": "test_unknown",
            }

    class RepairBackend:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def repair_action(self, text: str, context=None):
            self.calls.append(text)
            return {"status": "not_actionable", "message": "wrong lane", "source": "backend"}

    class ConversationBackend:
        def respond(self, text: str, context=None):
            return "Research answer."

    repair = RepairBackend()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=UnknownMicroBackend(), heuristic_fallback_enabled=False),
        main_jarvis=MainJarvis(repair_backend=repair, conversation_backend=ConversationBackend()),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light"]),
    )

    response = router.route(
        AskRequest(
            text="Search the web for the official SearXNG search API documentation",
            session_id="research-lane",
        )
    )

    assert response["route"] == "main_jarvis"
    assert response["result"]["status"] == "conversation"
    assert repair.calls == []


def test_router_falls_back_to_generic_missing_field_clarification_for_action_intents():
    class MicroBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "lists.add_item",
                "confidence": 0.9,
                "entities": {"item_text": "milk"},
                "ambiguity_flags": [],
                "reasoning": "backend_list_add_missing_list_name",
            }

    class NotActionableRepairBackend:
        def repair_action(self, text: str, context=None):
            return {
                "status": "not_actionable",
                "reasoning": "main_repair_model_unavailable_or_invalid",
                "message": "",
                "source": "unavailable",
            }

    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=MicroBackend(), heuristic_fallback_enabled=False),
        main_jarvis=MainJarvis(repair_backend=NotActionableRepairBackend()),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    response = router.route(
        AskRequest(
            text="add milk",
            session_id="generic-missing-fields-fallback",
        )
    )

    assert response["route"] == "main_jarvis_repair"
    assert response["intent"] == "lists.add_item"
    assert response["result"]["status"] == "needs_clarification"
    assert "list_name" in response["result"]["missing_fields"]
    assert response["result"]["repair_source"] == "fallback"
    assert response["state"] == "AWAITING_CONFIRMATION"


def test_router_forces_main_owner_for_conversation_even_if_micro_recommends_micro():
    class MicroBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "conversation.general",
                "confidence": 0.92,
                "entities": {},
                "ambiguity_flags": [],
                "recommended_owner": "micro_jarvis",
                "reasoning": "backend_conversation_guess",
            }

    class ConversationBackend:
        def respond(self, text: str, context=None):
            return "Monkeys mostly eat fruit, leaves, seeds, and insects."

    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=MicroBackend(), heuristic_fallback_enabled=False),
        main_jarvis=MainJarvis(conversation_backend=ConversationBackend()),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    response = router.route(
        AskRequest(
            text="what do monkeys eat",
            session_id="conversation-main-owner-enforced",
        )
    )

    assert response["route"] == "main_jarvis"
    assert response["owner"] == "main_jarvis"
    assert response["intent"] == "conversation.general"
    assert response["result"]["status"] == "conversation"
    assert "fruit" in response["result"]["message"].lower()


def test_router_opens_and_resolves_conversation_pending_followup():
    class MicroBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "conversation.general",
                "confidence": 0.9,
                "entities": {},
                "ambiguity_flags": [],
                "reasoning": "conversation_followup_test",
            }

    class ConversationBackend:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def respond(self, text: str, context=None):
            self.calls.append({"text": text, "context": str(context or {})})
            lowered = text.strip().lower()
            if "resolved subject: narwhal" in lowered:
                return "For narwhals, mostly males have the tusk while females rarely do."
            return "Which animal do you mean?"

    conversation_backend = ConversationBackend()
    router = JarvisRouter(
        micro_jarvis=MicroJarvis(backend=MicroBackend(), heuristic_fallback_enabled=False),
        main_jarvis=MainJarvis(conversation_backend=conversation_backend),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries", "to-do"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
    )

    first = router.route(
        AskRequest(
            text="Do both males and females have the tusk?",
            session_id="conversation-pending-followup",
        )
    )
    assert first["route"] == "main_jarvis"
    assert first["result"]["status"] == "needs_clarification"
    assert first["dialog"]["mode"] == "conversation_pending"
    assert first["dialog"]["pending_intent"] == "conversation.general"
    assert "topic_subject" in first["dialog"]["awaiting_fields"]
    assert first["state"] == "AWAITING_CONFIRMATION"

    second = router.route(
        AskRequest(
            text="narwhal",
            session_id="conversation-pending-followup",
        )
    )
    assert second["route"] == "main_jarvis_repair"
    assert second["intent"] == "conversation.general"
    assert second["result"]["status"] == "conversation"
    assert "narwhal" in str(second["result"].get("message") or "").lower()
    assert second["state"] == "CONVERSATIONAL"
    assert any("Resolved subject: narwhal" in call["text"] for call in conversation_backend.calls)


def test_router_rewrites_contextual_followup_using_conversation_topic():
    class MicroBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "conversation.general",
                "confidence": 0.88,
                "entities": {},
                "ambiguity_flags": [],
                "reasoning": "conversation_contextual_followup_test",
            }

    class ConversationBackend:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def respond(self, text: str, context=None):
            self.calls.append(text)
            if "only live in water" in text.lower():
                return "Narwhals are marine mammals and live in Arctic waters."
            return "Narwhals are Arctic whales known for their tusks."

    conversation_backend = ConversationBackend()
    scratch = Path("data") / "test_conversation_history" / f"router-{uuid4()}"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        router = JarvisRouter(
            micro_jarvis=MicroJarvis(backend=MicroBackend(), heuristic_fallback_enabled=False),
            main_jarvis=MainJarvis(conversation_backend=conversation_backend),
            session_store=SessionStore(),
            runtime_power=RuntimePowerController(),
            event_log=EventLogService(),
            memory_service=None,
            conversation_history_service=ConversationHistoryService(base_dir=str(scratch)),
            lists_service=ListsService(default_list_names=["groceries", "to-do"]),
            calendar_service=CalendarService(),
            home_service=HomeService(default_switch_names=["office test light", "kitchen light", "living room lamp"]),
        )

        first = router.route(
            AskRequest(
                text="tell me about narwhals",
                session_id="conversation-contextual-followup",
            )
        )
        assert first["result"]["status"] == "conversation"

        second = router.route(
            AskRequest(
                text="do they only live in water?",
                session_id="conversation-contextual-followup",
            )
        )
        assert second["route"] == "main_jarvis"
        assert second["result"]["status"] == "conversation"
        assert len(conversation_backend.calls) >= 2
        rewritten_second = conversation_backend.calls[-1]
        assert rewritten_second.lower().startswith("for ")
        assert "do they only live in water?" in rewritten_second.lower()
    finally:
        shutil.rmtree(scratch.parent, ignore_errors=True)


def test_router_returns_capability_gap_for_unsupported_thermostat_request():
    router, _ = _build_router_with_log()
    response = router.route(
        AskRequest(
            text="set the house heat to 68 degrees",
            session_id="thermostat-gap",
        )
    )

    assert response["route"] == "main_jarvis_repair"
    assert response["intent"] == "conversation.general"
    assert response["result"]["status"] == "not_actionable"
    assert response["result"]["inferred_intent"] == "home.set_thermostat"
    assert response["result"]["inferred_entities"]["target_temperature_f"] == 68
    assert response["result"]["debug_intent_label"] == "thermostat action"
    assert response["assistant"]["text"].endswith("(thermostat action)")


def test_router_recovers_asr_list_add_to_it_using_last_list_context():
    router, _ = _build_router_with_log()
    router.route(AskRequest(text="add tofu to groceries", session_id="asr-list-add"))
    response = router.route(
        AskRequest(text="can you ride burrito shells to it", session_id="asr-list-add")
    )

    assert response["intent"] == "lists.add_item"
    assert response["route"] == "main_jarvis_repair"
    assert response["result"]["status"] == "ok"
    assert response["result"]["list_name"] == "groceries"
    assert response["result"]["item_text"] == "burrito shells"


def test_router_clarifies_all_of_them_to_all_lights():
    router, _ = _build_router_with_log()
    first = router.route(
        AskRequest(
            text="turn house lights on",
            session_id="switch-all-followup",
        )
    )
    assert first["result"]["status"] == "unknown_switch"
    assert first["state"] == "AWAITING_CONFIRMATION"

    second = router.route(
        AskRequest(
            text="all of them",
            session_id="switch-all-followup",
        )
    )

    assert second["intent"] == "home.set_switch"
    assert second["route"] == "main_jarvis_repair"
    assert second["result"]["status"] == "ok"
    assert second["result"]["switch_name"] == "all lights"
    assert second["result"]["action"] == "on"


def test_main_route_appends_debug_intent_label_for_lights_action():
    router, _ = _build_router_with_log()
    response = router.route(
        AskRequest(
            text="turn off all the lights jarvis",
            session_id="debug-intent-lights",
        )
    )

    assert response["route"] == "main_jarvis"
    assert response["result"]["debug_intent_label"] == "lights action"
    assert response["assistant"]["debug_intent_label"] == "lights action"
    assert response["assistant"]["text"].endswith("(lights action)")


def test_main_repair_followup_appends_debug_intent_label():
    router, _ = _build_router_with_log()
    first = router.route(
        AskRequest(
            text="add dentist appointment to my calendar",
            session_id="debug-intent-followup",
        )
    )
    assert first["result"]["status"] == "needs_clarification"
    assert first["result"]["debug_intent_label"] == "calendar action"
    assert first["assistant"]["text"].endswith("(calendar action)")

    second = router.route(
        AskRequest(
            text="tomorrow at noon",
            session_id="debug-intent-followup",
        )
    )
    assert second["result"]["status"] == "ok"
    assert second["result"]["debug_intent_label"] == "follow up from previous | calendar action"
    assert second["assistant"]["text"].endswith("(follow up from previous | calendar action)")


def test_router_interrupts_pending_list_clarification_for_new_light_command():
    router, _ = _build_router_with_log()
    first = router.route(
        AskRequest(
            text="add milk to blue list",
            session_id="pending-break-lights",
        )
    )
    assert first["result"]["status"] == "unknown_list"
    assert first["state"] == "AWAITING_CONFIRMATION"

    second = router.route(
        AskRequest(
            text="turn off all the lights jarvis",
            session_id="pending-break-lights",
        )
    )

    assert second["intent"] == "home.set_switch"
    assert second["result"]["status"] in {"executed", "ok"}
    if second["result"]["status"] == "executed":
        assert second["result"]["execution"]["status"] == "ok"


def test_router_interrupts_pending_list_clarification_for_new_list_get_command():
    router, _ = _build_router_with_log()
    first = router.route(
        AskRequest(
            text="add milk to blue list",
            session_id="pending-break-list-get",
        )
    )
    assert first["result"]["status"] == "unknown_list"
    assert first["state"] == "AWAITING_CONFIRMATION"

    second = router.route(
        AskRequest(
            text="what's on to-do list",
            session_id="pending-break-list-get",
        )
    )

    assert second["intent"] == "lists.get_items"
    assert second["result"]["status"] == "ok"
    assert second["result"]["list_name"] == "to-do"


def test_router_interrupts_pending_list_clarification_for_general_question():
    router, event_log = _build_router_with_log()
    first = router.route(
        AskRequest(
            text="add milk to blue list",
            session_id="pending-break-general-question",
        )
    )
    assert first["result"]["status"] == "unknown_list"
    assert first["state"] == "AWAITING_CONFIRMATION"

    second = router.route(
        AskRequest(
            text="who are you?",
            session_id="pending-break-general-question",
        )
    )

    assert second["route"] == "main_jarvis"
    assert second["result"]["status"] == "conversation"

    event_types = [item["event_type"] for item in event_log.recent(limit=200)]
    assert "pending.clarification.interrupted" in event_types
