import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.discord.bot import (
    DiscordJarvisBot,
    build_ask_request_payload,
    load_discord_permissions_policy,
    parse_discord_channel_id,
    parse_discord_guild_id,
    parse_discord_message_envelope,
    parse_discord_message_text,
    resolve_command_channel_id,
    resolve_command_guild_id,
    build_session_channel,
    build_session_id,
    extract_command_text,
    summarize_discord_api_error,
    summarize_ask_response,
    discord_policy_has_allow_scope,
    split_discord_message,
)


def test_extract_command_text():
    assert extract_command_text("!jarvis add milk to groceries") == "add milk to groceries"
    assert extract_command_text("!JARVIS turn office light on") == "turn office light on"
    assert extract_command_text("! turn office light on", prefix="!") == "turn office light on"
    assert extract_command_text("!turn office light on", prefix="!") == "turn office light on"
    assert extract_command_text("hello world") is None


def test_discord_bot_rejects_unscoped_configuration(tmp_path):
    with pytest.raises(RuntimeError, match="without a restrictive scope"):
        DiscordJarvisBot(
            command_prefix="!jarvis",
            permissions_path=str(tmp_path / "missing-policy.yaml"),
        )


def test_discord_bot_denies_role_only_scope_in_direct_messages(tmp_path):
    permissions = tmp_path / "discord_permissions.yaml"
    permissions.write_text(
        (
            "version: 1\n"
            "defaults:\n"
            "  command_prefix: \"!jarvis\"\n"
            "  require_prefix: true\n"
            "  allow_direct_messages: true\n"
            "  allowed_role_ids: [123456789]\n"
        ),
        encoding="utf-8",
    )
    bot = DiscordJarvisBot(
        command_prefix="!jarvis",
        permissions_path=str(permissions),
    )
    channel = SimpleNamespace(id=456, send=AsyncMock())
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False, id=789, roles=[]),
        guild=None,
        channel=channel,
        content="!jarvis hello",
    )

    asyncio.run(bot.on_message(message))

    channel.send.assert_not_awaited()


def test_parse_discord_channel_id():
    assert parse_discord_channel_id(12345) == 12345
    assert parse_discord_channel_id("12345") == 12345
    assert parse_discord_channel_id(" 12345 ") == 12345
    assert parse_discord_channel_id("") is None
    assert parse_discord_channel_id("abc") is None


def test_parse_discord_guild_id():
    assert parse_discord_guild_id(12345) == 12345
    assert parse_discord_guild_id("12345") == 12345
    assert parse_discord_guild_id(" 12345 ") == 12345
    assert parse_discord_guild_id("") is None


def test_parse_discord_message_text_supports_channel_listener_mode():
    assert (
        parse_discord_message_text(
            content="what is on my calendar tomorrow",
            prefix="!",
            channel_id=123,
            command_channel_id=123,
        )
        == "what is on my calendar tomorrow"
    )
    assert (
        parse_discord_message_text(
            content="what is on my calendar tomorrow",
            prefix="!",
            channel_id=999,
            command_channel_id=123,
        )
        is None
    )


def test_parse_discord_message_envelope_preserves_explicit_micro_boundary():
    prefixed = parse_discord_message_envelope(
        content="!what is on my calendar tomorrow",
        prefix="!",
        channel_id=123,
        command_channel_id=123,
    )
    unprefixed = parse_discord_message_envelope(
        content="what is on my calendar tomorrow",
        prefix="!",
        channel_id=123,
        command_channel_id=123,
    )

    assert prefixed is not None
    assert prefixed.text == "what is on my calendar tomorrow"
    assert prefixed.lane == "micro"
    assert prefixed.micro_command_explicit is True
    assert unprefixed is not None
    assert unprefixed.text == "what is on my calendar tomorrow"
    assert unprefixed.lane == "main"
    assert unprefixed.micro_command_explicit is False


def test_parse_discord_message_text_respects_prefix_requirement_and_guild_scope():
    assert (
        parse_discord_message_text(
            content="hello jarvis",
            prefix="!",
            channel_id=333333333333333333,
            guild_id=111111111111111111,
            command_channel_id=333333333333333333,
            command_guild_id=111111111111111111,
            require_prefix=True,
            allow_unprefixed=False,
        )
        is None
    )
    assert (
        parse_discord_message_text(
            content="! hello jarvis",
            prefix="!",
            channel_id=333333333333333333,
            guild_id=111111111111111111,
            command_channel_id=333333333333333333,
            command_guild_id=111111111111111111,
            require_prefix=True,
            allow_unprefixed=False,
        )
        == "hello jarvis"
    )
    assert (
        parse_discord_message_text(
            content="! hello jarvis",
            prefix="!",
            channel_id=333333333333333333,
            guild_id=666666666666666666,
            command_channel_id=333333333333333333,
            command_guild_id=111111111111111111,
            require_prefix=True,
            allow_unprefixed=False,
        )
        is None
    )


def test_permissions_policy_loads_defaults_and_guild_overrides(tmp_path):
    permissions = tmp_path / "discord_permissions.yaml"
    permissions.write_text(
        (
            "version: 1\n"
            "defaults:\n"
            "  command_prefix: \"!\"\n"
            "  require_prefix: false\n"
            "  allow_direct_messages: false\n"
            "  allowed_guild_ids:\n"
            "    - 111111111111111111\n"
            "  allowed_channel_ids: []\n"
            "  allowed_role_ids: []\n"
            "  allowed_user_ids: []\n"
            "  denied_user_ids: []\n"
            "guilds:\n"
            "  - guild_id: 111111111111111111\n"
            "    guild_name: Example_House\n"
            "    require_prefix: false\n"
            "    allowed_channel_ids:\n"
            "      - 333333333333333333\n"
            "    allowed_role_ids: []\n"
            "    allowed_user_ids: []\n"
            "    denied_user_ids: []\n"
        ),
        encoding="utf-8",
    )

    policy = load_discord_permissions_policy(str(permissions))
    assert policy["command_prefix"] == "!"
    assert policy["allow_direct_messages"] is False
    assert 111111111111111111 in policy["allowed_guild_ids"]
    guild = policy["guilds"][111111111111111111]
    assert guild["require_prefix"] is False
    assert 333333333333333333 in guild["allowed_channel_ids"]
    assert discord_policy_has_allow_scope(policy) is True

    assert (
        resolve_command_guild_id(command_guild_id=None, permissions_path=str(permissions))
        == 111111111111111111
    )
    assert (
        resolve_command_channel_id(command_channel_id=None, permissions_path=str(permissions))
        == 333333333333333333
    )


def test_permissions_policy_loads_multiple_exact_skill_channels(tmp_path):
    permissions = tmp_path / "discord_permissions.yaml"
    permissions.write_text(
        (
            "version: 1\n"
            "defaults:\n"
            "  require_prefix: false\n"
            "guilds:\n"
            "  - guild_id: 100\n"
            "    skill_channel_access:\n"
            "      - skill_id: skill.email.agent\n"
            "        channel_id: 201\n"
            "        allowed_user_ids: [41]\n"
            "        audiences: [shared]\n"
            "      - skill_id: skill.email.agent\n"
            "        channel_id: 202\n"
            "        allowed_user_ids: [42]\n"
            "        audiences: [shared]\n"
        ),
        encoding="utf-8",
    )

    policy = load_discord_permissions_policy(str(permissions))

    assert discord_policy_has_allow_scope(policy) is True
    assert [row["channel_id"] for row in policy["skill_channel_access"]] == ["201", "202"]
    assert policy["skill_channel_access"][1]["allowed_user_ids"] == ["42"]


def test_split_discord_message_is_bounded_and_preserves_content():
    source = "\n\n".join(f"E{index} " + ("summary " * 80) for index in range(1, 20))

    parts = split_discord_message(source, max_chars=500)

    assert 1 < len(parts) <= 20
    assert all(len(part) <= 500 for part in parts)
    assert "E1" in parts[0]


def test_private_notes_channel_captures_silently_before_command_routing(tmp_path):
    class PrivateNotesStub:
        def __init__(self) -> None:
            self.captures = []

        def capture_note(self, **kwargs):
            self.captures.append(kwargs)
            return {"status": "captured"}

    permissions = tmp_path / "discord_permissions.yaml"
    permissions.write_text(
        (
            "version: 1\n"
            "defaults:\n"
            "  command_prefix: \"!\"\n"
            "  require_prefix: false\n"
            "  allowed_guild_ids: [100]\n"
            "guilds:\n"
            "  - guild_id: 100\n"
            "    allowed_channel_ids: [200]\n"
            "    private_notes_channels:\n"
            "      - channel_id: 201\n"
            "        allowed_user_ids: [300]\n"
            "        owner_user_id: taylor\n"
            "        owner_display_name: Taylor\n"
            "        timezone: America/New_York\n"
            "        digest_at: \"18:00\"\n"
            "        raw_note_retention_days: 30\n"
        ),
        encoding="utf-8",
    )
    service = PrivateNotesStub()
    bot = DiscordJarvisBot(
        command_prefix="!",
        command_channel_id=200,
        command_guild_id=100,
        permissions_path=str(permissions),
        private_notes_service=service,
    )
    channel = SimpleNamespace(id=201, send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        author=SimpleNamespace(
            bot=False,
            id=300,
            roles=[],
            display_name="Taylor",
            global_name=None,
            name="taylor",
        ),
        guild=SimpleNamespace(id=100),
        channel=channel,
        content="maybe move the table; ask Jordan about Friday",
        created_at=datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc),
    )

    asyncio.run(bot.on_message(message))

    assert len(service.captures) == 1
    assert service.captures[0]["external_message_id"] == "555"
    assert service.captures[0]["content"] == "maybe move the table; ask Jordan about Friday"
    assert service.captures[0]["config"].owner_user_id == "taylor"
    assert service.captures[0]["config"].raw_note_retention_days == 30
    channel.send.assert_not_awaited()


def test_private_notes_channel_silently_ignores_unlisted_author(tmp_path):
    service = SimpleNamespace(capture_note=lambda **_: (_ for _ in ()).throw(AssertionError("not allowed")))
    permissions = tmp_path / "discord_permissions.yaml"
    permissions.write_text(
        (
            "version: 1\n"
            "defaults:\n"
            "  allowed_guild_ids: [100]\n"
            "guilds:\n"
            "  - guild_id: 100\n"
            "    allowed_channel_ids: [200]\n"
            "    private_notes_channels:\n"
            "      - channel_id: 201\n"
            "        allowed_user_ids: [300]\n"
            "        owner_user_id: taylor\n"
        ),
        encoding="utf-8",
    )
    bot = DiscordJarvisBot(
        command_channel_id=200,
        command_guild_id=100,
        permissions_path=str(permissions),
        private_notes_service=service,
    )
    channel = SimpleNamespace(id=201, send=AsyncMock())
    message = SimpleNamespace(
        id=556,
        author=SimpleNamespace(bot=False, id=999),
        guild=SimpleNamespace(id=100),
        channel=channel,
        content="do not capture",
    )

    asyncio.run(bot.on_message(message))

    channel.send.assert_not_awaited()


def test_private_notes_scheduler_resumes_parts_and_marks_delivery(tmp_path, monkeypatch):
    class PrivateNotesStub:
        def __init__(self) -> None:
            self.recorded = []
            self.delivered = []
            self.failed = []

        def prepare_due_digest(self, **_):
            return {
                "digest_id": "digest-1",
                "delivery_channel_id": "201",
                "parts": ["already sent", "second part"],
                "discord_message_ids": ["out-1"],
            }

        def record_delivery_part(self, **kwargs):
            self.recorded.append(kwargs)

        def mark_delivered(self, **kwargs):
            self.delivered.append(kwargs)

        def mark_delivery_failed(self, **kwargs):
            self.failed.append(kwargs)

    permissions = tmp_path / "discord_permissions.yaml"
    permissions.write_text(
        (
            "version: 1\n"
            "defaults:\n"
            "  allowed_guild_ids: [100]\n"
            "guilds:\n"
            "  - guild_id: 100\n"
            "    allowed_channel_ids: [200]\n"
            "    private_notes_channels:\n"
            "      - channel_id: 201\n"
            "        allowed_user_ids: [300]\n"
            "        owner_user_id: taylor\n"
        ),
        encoding="utf-8",
    )
    service = PrivateNotesStub()
    bot = DiscordJarvisBot(
        command_channel_id=200,
        command_guild_id=100,
        permissions_path=str(permissions),
        private_notes_service=service,
    )
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=777)))
    monkeypatch.setattr(bot, "get_channel", lambda channel_id: channel if channel_id == 201 else None)

    asyncio.run(
        bot._run_private_notes_digest_once(
            now=datetime(2026, 8, 16, 22, 0, tzinfo=timezone.utc)
        )
    )

    channel.send.assert_awaited_once()
    assert channel.send.await_args.args[0] == "second part"
    assert service.recorded == [{"digest_id": "digest-1", "message_id": "777"}]
    assert service.delivered == [{"digest_id": "digest-1"}]
    assert service.failed == []


def test_build_session_id():
    assert build_session_id(guild_id=123, channel_id=456, user_id=789) == "discord:123:456:789"
    assert build_session_id(guild_id=None, channel_id=456, user_id=789) == "discord:dm:456:789"


def test_build_session_channel():
    assert build_session_channel(guild_id=123, channel_id=456) == "discord.guild.123.channel.456"
    assert build_session_channel(guild_id=None, channel_id=456) == "discord.dm.456"


def test_build_ask_request_payload_uses_channel_session_contract():
    payload = build_ask_request_payload(
        command_text="turn the kitchen light on",
        guild_id=123,
        channel_id=456,
        user_id=789,
    )

    assert payload["text"] == "turn the kitchen light on"
    assert payload["source"] == "discord"
    assert payload["user_id"] == "789"
    assert "session_id" not in payload
    assert payload["context"]["mode"] == "discord_command"
    assert payload["context"]["auto_channel_session"] is True
    assert payload["context"]["channel_session_scope"] == "per_user"
    assert payload["context"]["external_user_id"] == "789"
    assert payload["context"]["force_main_owner"] is True
    assert payload["context"]["wake_on_message"] is True
    assert payload["context"]["session_channel"] == "discord.guild.123.channel.456"
    assert payload["context"]["discord_channel_id"] == "456"
    assert payload["context"]["discord_guild_id"] == "123"
    assert payload["context"]["micro_command_explicit"] is False
    assert payload["context"]["discord_routing_lane"] == "main"


def test_build_ask_request_payload_marks_explicit_micro_command():
    payload = build_ask_request_payload(
        command_text="what is on my calendar tomorrow",
        guild_id=123,
        channel_id=456,
        user_id=789,
        micro_command_explicit=True,
    )

    assert payload["context"]["micro_command_explicit"] is True
    assert payload["context"]["discord_routing_lane"] == "micro"
    assert payload["context"]["force_main_owner"] is False


def test_summarize_ask_response_prefers_assistant_text():
    payload = {
        "intent": "calendar.add_event",
        "route": "main_jarvis_repair",
        "assistant": {"text": "I still need the event title.\nWhat should I name the calendar event?"},
        "result": {"status": "needs_clarification"},
        "dialog": {"mode": "conversation_pending"},
    }

    assert summarize_ask_response(payload) == "I still need the event title.\nWhat should I name the calendar event?"


def test_summarize_ask_response_falls_back_to_shared_adapter():
    payload = {
        "intent": "lists.add_item",
        "route": "micro_tool",
        "result": {
            "status": "ok",
            "list_name": "groceries",
            "item_text": "milk",
        },
        "dialog": {"mode": "command_action", "turn_complete": True},
    }

    assert summarize_ask_response(payload) == 'Added "milk" to groceries.'


def test_summarize_discord_api_error_prefers_status_and_body():
    class DummyResponse:
        status_code = 500
        text = '{"detail":"internal failure"}'

    class DummyHTTPError(Exception):
        def __init__(self):
            self.response = DummyResponse()

    message = summarize_discord_api_error(DummyHTTPError())
    assert "HTTP 500" in message
    assert "internal failure" in message


def test_summarize_discord_api_error_with_empty_message_uses_type():
    class SilentError(Exception):
        def __str__(self):
            return ""

    assert summarize_discord_api_error(SilentError()) == "SilentError"
