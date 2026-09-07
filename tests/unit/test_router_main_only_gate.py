from __future__ import annotations

import inspect

from app.core.main_jarvis import MainJarvis
from app.core.router import JarvisRouter
from app.core.session_store import SessionStore
from app.core.state_machine import RuntimePowerController
from app.schemas.api import AskRequest
from app.services.discord.bot import build_ask_request_payload, parse_discord_message_envelope
from app.services.event_log import EventLogService
from app.tools.calendar_service import CalendarService
from app.tools.home_service import HomeService
from app.tools.lists_service import ListsService


class _ConversationModel:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def decide_turn(self, text: str, context=None):  # type: ignore[no-untyped-def]
        del context
        self.calls.append(text)
        return {
            "mode": "conversation",
            "confidence": 0.99,
            "reason_code": "conversation_only",
            "message": "Main handled this turn.",
        }


def _router(model: _ConversationModel) -> JarvisRouter:
    return JarvisRouter(
        main_jarvis=MainJarvis(),
        session_store=SessionStore(),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(),
        memory_service=None,
        lists_service=ListsService(default_list_names=["groceries"]),
        calendar_service=CalendarService(),
        home_service=HomeService(default_switch_names=["kitchen light"]),
        main_tool_model=model,
        main_tool_execution_mode="active",
    )


def test_router_has_no_secondary_semantic_classifier_dependency() -> None:
    parameters = inspect.signature(JarvisRouter).parameters

    assert "micro_jarvis" not in parameters
    assert "legacy_micro_routing_enabled" not in parameters


def test_discord_prefix_is_provenance_only_and_both_envelopes_use_main() -> None:
    prefixed = parse_discord_message_envelope(content="! turn on the kitchen light", prefix="!")
    unprefixed = parse_discord_message_envelope(content="turn on the kitchen light", prefix="!")

    assert prefixed is not None
    assert prefixed.text == "turn on the kitchen light"
    assert prefixed.lane == "main"
    assert prefixed.command_prefix_explicit is True
    assert unprefixed is not None
    assert unprefixed.lane == "main"
    assert unprefixed.command_prefix_explicit is False

    payload = build_ask_request_payload(
        command_text=prefixed.text,
        guild_id=1,
        channel_id=2,
        user_id=3,
        command_prefix_explicit=prefixed.command_prefix_explicit,
    )
    assert payload["context"]["command_prefix_explicit"] is True
    assert payload["context"]["discord_routing_lane"] == "main"
    assert "micro_command_explicit" not in payload["context"]


def test_prefixed_and_unprefixed_discord_turns_both_enter_main_commitment() -> None:
    model = _ConversationModel()
    router = _router(model)

    responses = [
        router.route(
            AskRequest(
                text="tell me hello",
                user_id="user-1",
                source="discord",
                context={
                    "command_prefix_explicit": explicit,
                    "auto_channel_session": True,
                    "session_channel": f"test-{explicit}",
                },
            )
        )
        for explicit in (True, False)
    ]

    assert model.calls == ["tell me hello", "tell me hello"]
    assert [item["owner"] for item in responses] == ["main_jarvis", "main_jarvis"]
    assert [item["route"] for item in responses] == ["main_tool_loop", "main_tool_loop"]
    assert all(item["result"]["message"] == "Main handled this turn." for item in responses)
