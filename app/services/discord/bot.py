from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
import traceback
import uuid

from app.api.principals import discord_adapter_principal
from app.core.assistant_response import build_assistant_payload
from app.integrations.discord_attachment.types import (
    DiscordAttachmentDescriptor,
    DiscordAttachmentIngressPort,
)
from app.schemas.api import AskRequest
from app.skills.domains.private_notes.service import PrivateNotesChannelConfig

try:
    import discord
except ImportError:  # pragma: no cover - exercised in environments without discord.py
    discord = None


def extract_command_text(content: str, prefix: str = "!jarvis") -> str | None:
    prefix = str(prefix or "!jarvis").strip() or "!jarvis"
    normalized = content.strip()
    if not normalized:
        return None
    if len(prefix) == 1:
        if normalized == prefix:
            return ""
        if normalized.startswith(prefix):
            return normalized[len(prefix) :].strip()
        return None

    lowered = normalized.lower()
    expected = f"{prefix.lower()} "
    if lowered == prefix.lower():
        return ""
    if not lowered.startswith(expected):
        return None
    return normalized[len(prefix) + 1 :].strip()


@dataclass(frozen=True)
class DiscordMessageEnvelope:
    """Accepted Discord text plus its explicit model-routing lane."""

    text: str
    lane: Literal["micro", "main"]
    micro_command_explicit: bool


def build_session_id(guild_id: int | None, channel_id: int, user_id: int) -> str:
    guild_part = str(guild_id) if guild_id is not None else "dm"
    return f"discord:{guild_part}:{channel_id}:{user_id}"


def build_session_channel(guild_id: int | None, channel_id: int) -> str:
    if guild_id is None:
        return f"discord.dm.{channel_id}"
    return f"discord.guild.{guild_id}.channel.{channel_id}"


def parse_discord_channel_id(raw: int | str | None) -> int | None:
    if isinstance(raw, int) and raw > 0:
        return raw
    text = str(raw or "").strip()
    if text.isdigit():
        parsed = int(text)
        if parsed > 0:
            return parsed
    return None


def parse_discord_guild_id(raw: int | str | None) -> int | None:
    if isinstance(raw, int) and raw > 0:
        return raw
    text = str(raw or "").strip()
    if text.isdigit():
        parsed = int(text)
        if parsed > 0:
            return parsed
    return None


def _as_bool_value(raw: Any, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _as_int_set(raw: Any) -> set[int]:
    if not isinstance(raw, list):
        return set()
    parsed: set[int] = set()
    for item in raw:
        value = parse_discord_channel_id(item)
        if value is not None:
            parsed.add(value)
    return parsed


def split_discord_message(text: str, max_chars: int = 1900) -> list[str]:
    limit = max(200, min(int(max_chars), 1990))
    remaining = str(text or "").strip()
    if not remaining:
        return ["Jarvis processed your request."]
    parts: list[str] = []
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        cut = remaining.rfind("\n\n", 0, limit + 1)
        if cut < limit // 3:
            cut = remaining.rfind("\n", 0, limit + 1)
        if cut < limit // 3:
            cut = remaining.rfind(" ", 0, limit + 1)
        if cut < limit // 3:
            cut = limit
        part = remaining[:cut].strip()
        if part:
            parts.append(part)
        remaining = remaining[cut:].strip()
    return parts[:20]


def _extract_dict_path(source: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = source
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _load_permissions_map(permissions_path: str | None) -> dict[str, Any] | None:
    path_text = str(permissions_path or "").strip()
    if not path_text:
        return None
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists() or not path.is_file():
        return None

    try:
        import yaml
    except Exception:
        return None

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded


def load_discord_permissions_policy(permissions_path: str | None) -> dict[str, Any]:
    loaded = _load_permissions_map(permissions_path)
    if not isinstance(loaded, dict):
        return {}

    defaults = loaded.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
    command_prefix = str(defaults.get("command_prefix") or "").strip()
    policy: dict[str, Any] = {
        "command_prefix": command_prefix,
        "require_prefix": _as_bool_value(defaults.get("require_prefix"), False),
        "allow_direct_messages": _as_bool_value(defaults.get("allow_direct_messages"), False),
        "allowed_guild_ids": _as_int_set(defaults.get("allowed_guild_ids")),
        "allowed_channel_ids": _as_int_set(defaults.get("allowed_channel_ids")),
        "allowed_role_ids": _as_int_set(defaults.get("allowed_role_ids")),
        "allowed_user_ids": _as_int_set(defaults.get("allowed_user_ids")),
        "denied_user_ids": _as_int_set(defaults.get("denied_user_ids")),
        "guilds": {},
        "private_notes_channels": [],
        "skill_channel_access": [],
    }

    guild_rows = loaded.get("guilds")
    if not isinstance(guild_rows, list):
        guild_rows = []
    guild_policy_map: dict[int, dict[str, Any]] = {}
    private_notes_channels: list[dict[str, Any]] = []
    skill_channel_access: list[dict[str, Any]] = []
    for row in guild_rows:
        if not isinstance(row, dict):
            continue
        guild_id = parse_discord_guild_id(row.get("guild_id"))
        if guild_id is None:
            continue
        require_prefix_value = row.get("require_prefix")
        require_prefix = (
            _as_bool_value(require_prefix_value, False)
            if require_prefix_value is not None
            else None
        )
        guild_policy_map[guild_id] = {
            "guild_id": guild_id,
            "guild_name": str(row.get("guild_name") or "").strip() or None,
            "require_prefix": require_prefix,
            "allowed_channel_ids": _as_int_set(row.get("allowed_channel_ids")),
            "allowed_role_ids": _as_int_set(row.get("allowed_role_ids")),
            "allowed_user_ids": _as_int_set(row.get("allowed_user_ids")),
            "denied_user_ids": _as_int_set(row.get("denied_user_ids")),
        }
        private_rows = row.get("private_notes_channels")
        if not isinstance(private_rows, list):
            private_rows = []
        for private_row in private_rows[:10]:
            if not isinstance(private_row, dict):
                continue
            channel_id = parse_discord_channel_id(private_row.get("channel_id"))
            if channel_id is None:
                continue
            delivery_channel_id = parse_discord_channel_id(
                private_row.get("delivery_channel_id") or channel_id
            )
            allowed_user_ids = _as_int_set(private_row.get("allowed_user_ids"))
            private_notes_channels.append(
                {
                    "guild_id": str(guild_id),
                    "channel_id": str(channel_id),
                    "delivery_channel_id": str(delivery_channel_id or channel_id),
                    "allowed_user_ids": [str(item) for item in sorted(allowed_user_ids)],
                    "owner_user_id": str(private_row.get("owner_user_id") or "").strip(),
                    "owner_display_name": str(private_row.get("owner_display_name") or "").strip(),
                    "agent_id": str(private_row.get("agent_id") or "catparty").strip(),
                    "timezone": str(private_row.get("timezone") or "America/New_York").strip(),
                    "digest_at": str(private_row.get("digest_at") or "18:00").strip(),
                    "skip_if_empty": _as_bool_value(private_row.get("skip_if_empty"), True),
                    "raw_note_retention_days": private_row.get("raw_note_retention_days", 30),
                }
            )
        skill_rows = row.get("skill_channel_access")
        if not isinstance(skill_rows, list):
            skill_rows = []
        for skill_row in skill_rows[:50]:
            if not isinstance(skill_row, dict):
                continue
            skill_id = str(skill_row.get("skill_id") or "").strip().casefold()
            channel_id = parse_discord_channel_id(skill_row.get("channel_id"))
            allowed_user_ids = _as_int_set(skill_row.get("allowed_user_ids"))
            audiences = [
                str(item).strip().casefold()
                for item in skill_row.get("audiences") or []
                if str(item).strip()
            ]
            if not skill_id or channel_id is None or not allowed_user_ids:
                continue
            skill_channel_access.append(
                {
                    "guild_id": str(guild_id),
                    "channel_id": str(channel_id),
                    "skill_id": skill_id,
                    "allowed_user_ids": [str(item) for item in sorted(allowed_user_ids)],
                    "audiences": audiences,
                }
            )
    policy["guilds"] = guild_policy_map
    policy["private_notes_channels"] = private_notes_channels
    policy["skill_channel_access"] = skill_channel_access
    return policy


def discord_policy_has_allow_scope(policy: dict[str, Any] | None) -> bool:
    if not isinstance(policy, dict) or not policy:
        return False
    for key in (
        "allowed_guild_ids",
        "allowed_channel_ids",
        "allowed_role_ids",
        "allowed_user_ids",
    ):
        if policy.get(key):
            return True
    guilds = policy.get("guilds")
    if isinstance(guilds, dict) and bool(guilds):
        return True
    private_channels = policy.get("private_notes_channels")
    if isinstance(private_channels, list) and bool(private_channels):
        return True
    skill_channels = policy.get("skill_channel_access")
    return isinstance(skill_channels, list) and bool(skill_channels)


def resolve_command_channel_id(
    *,
    command_channel_id: int | str | None,
    permissions_path: str | None,
) -> int | None:
    explicit = parse_discord_channel_id(command_channel_id)
    if explicit is not None:
        return explicit

    loaded = _load_permissions_map(permissions_path)
    if not isinstance(loaded, dict):
        return None

    policy = load_discord_permissions_policy(permissions_path)
    guilds = policy.get("guilds")
    if isinstance(guilds, dict):
        for guild_policy in guilds.values():
            if not isinstance(guild_policy, dict):
                continue
            channels = guild_policy.get("allowed_channel_ids")
            if isinstance(channels, set) and channels:
                return sorted(channels)[0]
    defaults_channels = policy.get("allowed_channel_ids")
    if isinstance(defaults_channels, set) and defaults_channels:
        return sorted(defaults_channels)[0]

    candidate_paths: tuple[tuple[str, ...], ...] = (
        ("discord", "command_channel_id"),
        ("discord", "command", "channel_id"),
        ("discord", "channels", "command", "id"),
        ("channels", "discord", "command_channel_id"),
        ("channels", "discord", "command", "channel_id"),
        ("discord_command_channel_id",),
        ("command_channel_id",),
    )
    for candidate_path in candidate_paths:
        parsed = parse_discord_channel_id(_extract_dict_path(loaded, candidate_path))
        if parsed is not None:
            return parsed
    return None


def resolve_command_guild_id(
    *,
    command_guild_id: int | str | None,
    permissions_path: str | None,
) -> int | None:
    explicit = parse_discord_guild_id(command_guild_id)
    if explicit is not None:
        return explicit

    loaded = _load_permissions_map(permissions_path)
    if not isinstance(loaded, dict):
        return None

    policy = load_discord_permissions_policy(permissions_path)
    allowed_guild_ids = policy.get("allowed_guild_ids")
    if isinstance(allowed_guild_ids, set) and allowed_guild_ids:
        return sorted(allowed_guild_ids)[0]
    guilds = policy.get("guilds")
    if isinstance(guilds, dict) and guilds:
        return sorted(guilds.keys())[0]

    candidate_paths: tuple[tuple[str, ...], ...] = (
        ("discord", "command_guild_id"),
        ("discord", "command", "guild_id"),
        ("channels", "discord", "command_guild_id"),
        ("channels", "discord", "command", "guild_id"),
        ("discord_command_guild_id",),
        ("command_guild_id",),
        ("discord", "guild_id"),
    )
    for candidate_path in candidate_paths:
        parsed = parse_discord_guild_id(_extract_dict_path(loaded, candidate_path))
        if parsed is not None:
            return parsed
    return None


def parse_discord_message_envelope(
    *,
    content: str,
    prefix: str = "!jarvis",
    channel_id: int | None = None,
    guild_id: int | None = None,
    command_channel_id: int | None = None,
    command_guild_id: int | None = None,
    require_prefix: bool = False,
    allow_unprefixed: bool = True,
) -> DiscordMessageEnvelope | None:
    if command_guild_id is not None:
        if guild_id is None or int(guild_id) != int(command_guild_id):
            return None
    if command_channel_id is not None:
        if channel_id is None or int(channel_id) != int(command_channel_id):
            return None

    explicit = extract_command_text(content, prefix=prefix)
    if explicit is not None:
        return DiscordMessageEnvelope(
            text=explicit,
            lane="micro",
            micro_command_explicit=True,
        )
    if allow_unprefixed or (command_channel_id is not None and not require_prefix):
        return DiscordMessageEnvelope(
            text=content.strip(),
            lane="main",
            micro_command_explicit=False,
        )
    return None


def parse_discord_message_text(
    *,
    content: str,
    prefix: str = "!jarvis",
    channel_id: int | None = None,
    guild_id: int | None = None,
    command_channel_id: int | None = None,
    command_guild_id: int | None = None,
    require_prefix: bool = False,
    allow_unprefixed: bool = True,
) -> str | None:
    """Compatibility wrapper for callers that only need accepted message text."""

    envelope = parse_discord_message_envelope(
        content=content,
        prefix=prefix,
        channel_id=channel_id,
        guild_id=guild_id,
        command_channel_id=command_channel_id,
        command_guild_id=command_guild_id,
        require_prefix=require_prefix,
        allow_unprefixed=allow_unprefixed,
    )
    return envelope.text if envelope is not None else None


def build_ask_request_payload(
    *,
    command_text: str,
    guild_id: int | None,
    channel_id: int,
    user_id: int,
    session_id: str | None = None,
    display_name: str | None = None,
    message_id: int | str | None = None,
    skill_scopes: list[str] | None = None,
    micro_command_explicit: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "text": command_text.strip(),
        "user_id": str(user_id),
        "source": "discord",
        "context": {
            "mode": "discord_command",
            "auto_channel_session": True,
            "channel_session_scope": "per_user",
            "force_main_owner": micro_command_explicit is not True,
            "wake_on_message": True,
            "session_channel": build_session_channel(guild_id=guild_id, channel_id=channel_id),
            "discord_channel_id": str(channel_id),
            "discord_guild_id": str(guild_id) if guild_id is not None else "dm",
            "external_user_id": str(user_id),
            "external_display_name": str(display_name or "").strip() or None,
            "micro_command_explicit": micro_command_explicit is True,
            "discord_routing_lane": "micro" if micro_command_explicit is True else "main",
        },
    }
    if message_id is not None and str(message_id).strip():
        external_message_id = f"discord:{message_id}"
        payload["request_id"] = external_message_id
        payload["context"]["external_message_id"] = external_message_id
    if skill_scopes:
        payload["context"]["skill_scopes"] = [
            str(item).strip().casefold()
            for item in skill_scopes
            if str(item).strip()
        ]
    session_value = str(session_id).strip() if isinstance(session_id, str) else ""
    if session_value:
        payload["session_id"] = session_value
    return payload


def summarize_ask_response(payload: dict[str, Any]) -> str:
    assistant = payload.get("assistant")
    if isinstance(assistant, dict):
        text = assistant.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

    result = payload.get("result")
    if not isinstance(result, dict):
        return "No response payload returned."
    intent = str(payload.get("intent") or "")
    route = str(payload.get("route") or "")
    dialog = payload.get("dialog")
    if not isinstance(dialog, dict):
        dialog = {}
    assistant_payload = build_assistant_payload(
        intent=intent,
        route=route,
        result=result,
        dialog=dialog,
    )
    text = assistant_payload.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return "Jarvis processed your request."


def _trim_error_text(text: str, max_len: int = 280) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def _extract_response_body_text(response: Any) -> str:
    if response is None:
        return ""
    text = ""
    try:
        text = str(getattr(response, "text", "") or "")
    except Exception:
        text = ""
    return _trim_error_text(text)


def summarize_discord_api_error(exc: Exception) -> str:
    exc_type = exc.__class__.__name__
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        body = _extract_response_body_text(response)
        if body:
            return f"{exc_type} (HTTP {status_code}) body={body}"
        return f"{exc_type} (HTTP {status_code})"

    message = str(exc).strip()
    if message:
        return f"{exc_type}: {_trim_error_text(message)}"
    return exc_type


if discord is not None:

    class DiscordJarvisBot(discord.Client):
        def __init__(
            self,
            command_prefix: str = "!jarvis",
            command_channel_id: int | str | None = None,
            command_guild_id: int | str | None = None,
            permissions_path: str | None = None,
            private_notes_service: Any | None = None,
            private_notes_poll_seconds: float = 30.0,
            turn_service: Any | None = None,
            attachment_ingress: DiscordAttachmentIngressPort | None = None,
            attachment_max_bytes: int = 52428800,
            attachment_max_per_message: int = 4,
        ) -> None:
            intents = discord.Intents.default()
            intents.message_content = True
            super().__init__(intents=intents)
            env_command_prefix = str(command_prefix or "!jarvis").strip() or "!jarvis"
            self._permissions_path = str(permissions_path or "").strip() or None
            self._permissions_policy = load_discord_permissions_policy(self._permissions_path)
            self._private_notes_service = private_notes_service
            self._turn_service = turn_service
            self._attachment_ingress = attachment_ingress
            self._attachment_max_bytes = max(1024, min(int(attachment_max_bytes), 104857600))
            self._attachment_max_per_message = max(1, min(int(attachment_max_per_message), 10))
            self._private_notes_poll_seconds = max(5.0, float(private_notes_poll_seconds))
            self._private_notes_digest_task: asyncio.Task[None] | None = None
            self._private_notes_channels: dict[tuple[str, str], PrivateNotesChannelConfig] = {}
            self._skill_channel_access: dict[tuple[str, str], list[dict[str, Any]]] = {}
            duplicate_private_notes_channels: set[tuple[str, str]] = set()
            raw_private_notes_channels = self._permissions_policy.get("private_notes_channels")
            if isinstance(raw_private_notes_channels, list):
                for raw_config in raw_private_notes_channels:
                    if not isinstance(raw_config, dict):
                        continue
                    try:
                        config = PrivateNotesChannelConfig.from_mapping(raw_config)
                    except ValueError as exc:
                        print(f"[discord] private notes channel disabled: {exc}")
                        continue
                    key = (config.guild_id, config.channel_id)
                    if key in duplicate_private_notes_channels:
                        continue
                    if key in self._private_notes_channels:
                        print(f"[discord] duplicate private notes channel disabled: guild={key[0]} channel={key[1]}")
                        self._private_notes_channels.pop(key, None)
                        duplicate_private_notes_channels.add(key)
                        continue
                    self._private_notes_channels[key] = config
            raw_skill_channel_access = self._permissions_policy.get("skill_channel_access")
            if isinstance(raw_skill_channel_access, list):
                for row in raw_skill_channel_access:
                    if not isinstance(row, dict):
                        continue
                    key = (str(row.get("guild_id") or ""), str(row.get("channel_id") or ""))
                    if not all(key):
                        continue
                    self._skill_channel_access.setdefault(key, []).append(dict(row))
            policy_prefix = str(self._permissions_policy.get("command_prefix") or "").strip()
            self._command_prefix = policy_prefix or env_command_prefix
            # Environment arguments are an optional hard single-channel scope.
            # Policy allowlists remain multi-channel and are evaluated per message.
            self._command_channel_id = parse_discord_channel_id(command_channel_id)
            self._command_guild_id = parse_discord_guild_id(command_guild_id)
            if (
                self._command_channel_id is None
                and self._command_guild_id is None
                and not discord_policy_has_allow_scope(self._permissions_policy)
            ):
                raise RuntimeError(
                    "Discord is enabled without a restrictive scope. Configure "
                    "DISCORD_COMMAND_GUILD_ID, DISCORD_COMMAND_CHANNEL_ID, or a policy allowlist."
                )

        async def setup_hook(self) -> None:
            if self._private_notes_service is not None and self._private_notes_channels:
                self._private_notes_digest_task = asyncio.create_task(
                    self._private_notes_digest_loop(),
                    name="jarvis-private-notes-digest",
                )

        async def close(self) -> None:
            task = self._private_notes_digest_task
            self._private_notes_digest_task = None
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await super().close()

        async def on_ready(self) -> None:
            channel_scope = (
                str(self._command_channel_id)
                if self._command_channel_id is not None
                else "all_channels_with_prefix"
            )
            guild_scope = (
                str(self._command_guild_id)
                if self._command_guild_id is not None
                else "all_guilds"
            )
            policy_loaded = bool(self._permissions_policy)
            print(
                f"Discord Jarvis connected as {self.user} "
                f"(prefix={self._command_prefix}, guild_scope={guild_scope}, channel_scope={channel_scope}, "
                f"policy_loaded={policy_loaded}, private_notes_channels={len(self._private_notes_channels)})"
            )

        async def _private_notes_digest_loop(self) -> None:
            await self.wait_until_ready()
            while not self.is_closed():
                try:
                    await self._run_private_notes_digest_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pragma: no cover - defensive scheduler boundary
                    print(f"[discord] private notes scheduler error: {summarize_discord_api_error(exc)}")
                await asyncio.sleep(self._private_notes_poll_seconds)

        async def _run_private_notes_digest_once(self, now: datetime | None = None) -> None:
            if self._private_notes_service is None:
                return
            current = now or datetime.now(timezone.utc)
            for config in list(self._private_notes_channels.values())[:10]:
                prepared = await asyncio.to_thread(
                    self._private_notes_service.prepare_due_digest,
                    config=config,
                    now=current,
                )
                if not isinstance(prepared, dict):
                    continue
                digest_id = str(prepared.get("digest_id") or "").strip()
                if not digest_id:
                    continue
                try:
                    delivery_channel_id = int(str(prepared.get("delivery_channel_id") or "0"))
                    channel = self.get_channel(delivery_channel_id)
                    if channel is None:
                        channel = await self.fetch_channel(delivery_channel_id)
                    parts = [str(item) for item in prepared.get("parts") or [] if str(item).strip()]
                    delivered_ids = [
                        str(item) for item in prepared.get("discord_message_ids") or [] if str(item).strip()
                    ]
                    for part in parts[len(delivered_ids) :]:
                        sent = await channel.send(
                            part,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        message_id = str(getattr(sent, "id", "") or "").strip()
                        if not message_id:
                            raise RuntimeError("Discord returned no message ID for a digest part.")
                        await asyncio.to_thread(
                            self._private_notes_service.record_delivery_part,
                            digest_id=digest_id,
                            message_id=message_id,
                        )
                    await asyncio.to_thread(
                        self._private_notes_service.mark_delivered,
                        digest_id=digest_id,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    error_summary = summarize_discord_api_error(exc)
                    print(f"[discord] private notes delivery failed digest_id={digest_id} error={error_summary}")
                    await asyncio.to_thread(
                        self._private_notes_service.mark_delivery_failed,
                        digest_id=digest_id,
                        error=error_summary,
                    )

        async def _handle_document_attachments(self, message: discord.Message) -> bool:
            attachments = list(getattr(message, "attachments", None) or [])
            if not attachments:
                return False
            if self._attachment_ingress is None:
                await message.channel.send(
                    "Document attachment intake is not enabled yet.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return True

            accepted_extensions = {".pdf", ".jpg", ".jpeg", ".png"}
            candidates: list[Any] = []
            rejected: list[str] = []
            for attachment in attachments[: self._attachment_max_per_message]:
                filename = str(getattr(attachment, "filename", "") or "").strip()
                size = getattr(attachment, "size", 0)
                try:
                    size_bytes = int(size)
                except (TypeError, ValueError):
                    size_bytes = 0
                if Path(filename).suffix.casefold() not in accepted_extensions:
                    rejected.append(filename or "unnamed attachment")
                    continue
                if size_bytes <= 0 or size_bytes > self._attachment_max_bytes:
                    rejected.append(filename or "unnamed attachment")
                    continue
                candidates.append(attachment)

            if len(attachments) > self._attachment_max_per_message:
                rejected.extend(
                    str(getattr(item, "filename", "") or "additional attachment")
                    for item in attachments[self._attachment_max_per_message :]
                )
            if candidates:
                await message.channel.send(
                    f"Securely submitting {len(candidates)} document attachment"
                    f"{'s' if len(candidates) != 1 else ''}…",
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            outcomes: list[str] = []
            for attachment in candidates:
                filename = str(getattr(attachment, "filename", "") or "").strip()
                try:
                    descriptor = DiscordAttachmentDescriptor(
                        guild_id=str(message.guild.id) if message.guild else None,
                        channel_id=str(message.channel.id),
                        user_id=str(message.author.id),
                        message_id=str(message.id),
                        attachment_id=str(attachment.id),
                        filename=filename,
                        content_type=str(getattr(attachment, "content_type", "") or ""),
                        size_bytes=int(attachment.size),
                        source_url=str(attachment.url),
                        title=Path(filename).stem,
                    )
                    receipt = await self._attachment_ingress.submit(descriptor)
                    if receipt.duplicate:
                        outcomes.append(f"Already received `{receipt.filename}`.")
                    elif receipt.enqueue_confirmed:
                        outcomes.append(f"Queued `{receipt.filename}` for secure local archiving.")
                    else:
                        outcomes.append(
                            f"Accepted `{receipt.filename}` securely; archival enqueue recovery is pending."
                        )
                except Exception as exc:
                    print(
                        "[discord] attachment intake failed "
                        f"guild={getattr(message.guild, 'id', None)} "
                        f"channel={getattr(message.channel, 'id', None)} "
                        f"error={summarize_discord_api_error(exc)}"
                    )
                    outcomes.append(f"Could not accept `{filename or 'attachment'}`.")
            if rejected:
                outcomes.append(
                    "Skipped unsupported, empty, oversized, or excess attachments: "
                    + ", ".join(f"`{name}`" for name in rejected[:10])
                    + ". PDF, JPEG, and PNG are accepted."
                )
            if outcomes:
                await message.channel.send(
                    "\n".join(outcomes),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return True

        async def on_message(self, message: discord.Message) -> None:
            if message.author.bot:
                return

            guild_id = message.guild.id if message.guild else None
            channel_id = message.channel.id
            user_id = message.author.id
            scoped_rows = self._skill_channel_access.get((str(guild_id), str(channel_id)), [])
            matched_skill_rows = [
                row
                for row in scoped_rows
                if str(user_id) in {
                    str(item) for item in row.get("allowed_user_ids") or []
                }
            ]
            skill_channel_allowed = bool(matched_skill_rows)
            skill_scopes = sorted(
                {
                    str(row.get("skill_id") or "").strip().casefold()
                    for row in matched_skill_rows
                    if str(row.get("skill_id") or "").strip()
                }
            )
            private_notes_config = self._private_notes_channels.get(
                (str(guild_id), str(channel_id))
            )
            if private_notes_config is not None:
                if str(user_id) not in private_notes_config.allowed_user_ids:
                    return
                await self._handle_document_attachments(message)
                if self._private_notes_service is None:
                    print(
                        f"[discord] private notes capture unavailable guild={guild_id} channel={channel_id}"
                    )
                    return
                content = str(getattr(message, "content", "") or "").strip()
                if not content:
                    return
                try:
                    created_at = getattr(message, "created_at", None)
                    await asyncio.to_thread(
                        self._private_notes_service.capture_note,
                        config=private_notes_config,
                        external_message_id=str(getattr(message, "id", "") or ""),
                        author_external_user_id=str(user_id),
                        author_display_name=(
                            str(getattr(message.author, "display_name", "") or "").strip()
                            or str(getattr(message.author, "global_name", "") or "").strip()
                            or str(getattr(message.author, "name", "") or "").strip()
                            or None
                        ),
                        content=content,
                        captured_at=created_at if isinstance(created_at, datetime) else None,
                    )
                except Exception as exc:
                    print(
                        "[discord] private notes capture failed "
                        f"guild={guild_id} channel={channel_id} error={summarize_discord_api_error(exc)}"
                    )
                return
            role_ids: set[int] = set()
            author_roles = getattr(message.author, "roles", None)
            if isinstance(author_roles, list):
                for role in author_roles:
                    role_id = parse_discord_channel_id(getattr(role, "id", None))
                    if role_id is not None:
                        role_ids.add(role_id)

            allow_direct_messages = False
            require_prefix = False
            allow_unprefixed = self._command_channel_id is not None
            allowed_guild_ids: set[int] = set()
            allowed_channel_ids: set[int] = set()
            allowed_role_ids: set[int] = set()
            allowed_user_ids: set[int] = set()
            denied_user_ids: set[int] = set()

            if isinstance(self._permissions_policy, dict) and self._permissions_policy:
                allow_direct_messages = bool(self._permissions_policy.get("allow_direct_messages"))
                require_prefix = bool(self._permissions_policy.get("require_prefix"))
                allow_unprefixed = not require_prefix
                allowed_guild_ids = set(self._permissions_policy.get("allowed_guild_ids") or set())
                allowed_channel_ids = set(self._permissions_policy.get("allowed_channel_ids") or set())
                allowed_role_ids = set(self._permissions_policy.get("allowed_role_ids") or set())
                allowed_user_ids = set(self._permissions_policy.get("allowed_user_ids") or set())
                denied_user_ids = set(self._permissions_policy.get("denied_user_ids") or set())
                guild_policies = self._permissions_policy.get("guilds")
                guild_policy = guild_policies.get(guild_id) if isinstance(guild_policies, dict) else None
                if isinstance(guild_policy, dict):
                    guild_require_prefix = guild_policy.get("require_prefix")
                    if isinstance(guild_require_prefix, bool):
                        require_prefix = guild_require_prefix
                        allow_unprefixed = not guild_require_prefix
                    guild_allowed_channels = set(guild_policy.get("allowed_channel_ids") or set())
                    guild_allowed_roles = set(guild_policy.get("allowed_role_ids") or set())
                    guild_allowed_users = set(guild_policy.get("allowed_user_ids") or set())
                    guild_denied_users = set(guild_policy.get("denied_user_ids") or set())
                    if guild_allowed_channels:
                        allowed_channel_ids = guild_allowed_channels
                    if guild_allowed_roles:
                        allowed_role_ids = guild_allowed_roles
                    if guild_allowed_users:
                        allowed_user_ids = guild_allowed_users
                    if guild_denied_users:
                        denied_user_ids = guild_denied_users

            if guild_id is None and not allow_direct_messages:
                return
            if allowed_guild_ids and (guild_id is None or guild_id not in allowed_guild_ids):
                return
            if self._command_guild_id is not None:
                if (guild_id is None or int(guild_id) != int(self._command_guild_id)) and not skill_channel_allowed:
                    return
            if self._command_channel_id is not None:
                if int(channel_id) != int(self._command_channel_id) and not skill_channel_allowed:
                    return
                if int(channel_id) == int(self._command_channel_id):
                    allow_unprefixed = not require_prefix
            if allowed_channel_ids and int(channel_id) not in allowed_channel_ids and not skill_channel_allowed:
                return
            if denied_user_ids and int(user_id) in denied_user_ids:
                return
            if allowed_user_ids and int(user_id) not in allowed_user_ids and not skill_channel_allowed:
                return
            if allowed_role_ids and not skill_channel_allowed:
                if guild_id is None:
                    # Discord DMs carry no guild-role context. A separate user
                    # allowlist is therefore required to admit a role-scoped user.
                    if not allowed_user_ids or int(user_id) not in allowed_user_ids:
                        return
                elif not (role_ids & allowed_role_ids):
                    return

            attachments_handled = await self._handle_document_attachments(message)
            if attachments_handled and not str(getattr(message, "content", "") or "").strip():
                return

            command_envelope = parse_discord_message_envelope(
                content=message.content,
                prefix=self._command_prefix,
                channel_id=channel_id,
                guild_id=guild_id,
                command_channel_id=(
                    self._command_channel_id
                    if self._command_channel_id is not None
                    and int(channel_id) == int(self._command_channel_id)
                    else None
                ),
                command_guild_id=(
                    self._command_guild_id
                    if self._command_guild_id is not None
                    and guild_id is not None
                    and int(guild_id) == int(self._command_guild_id)
                    else None
                ),
                require_prefix=require_prefix,
                allow_unprefixed=allow_unprefixed,
            )
            if command_envelope is None:
                return
            command_text = command_envelope.text
            if not command_text:
                if (
                    self._command_channel_id is not None
                    and int(message.channel.id) == int(self._command_channel_id)
                ):
                    return
                if len(self._command_prefix) == 1:
                    usage = f"Usage: `{self._command_prefix}<command>`"
                else:
                    usage = f"Usage: `{self._command_prefix} <command>`"
                await message.channel.send(usage)
                return

            request_payload = build_ask_request_payload(
                command_text=command_text,
                guild_id=message.guild.id if message.guild else None,
                channel_id=message.channel.id,
                user_id=message.author.id,
                display_name=(
                    str(getattr(message.author, "display_name", "") or "").strip()
                    or str(getattr(message.author, "global_name", "") or "").strip()
                    or str(getattr(message.author, "name", "") or "").strip()
                ),
                message_id=message.id,
                skill_scopes=skill_scopes,
                micro_command_explicit=command_envelope.micro_command_explicit,
            )

            try:
                if self._turn_service is None:
                    raise RuntimeError("embedded turn service is not configured")
                payload = await self._turn_service.route(
                    AskRequest.model_validate(request_payload),
                    principal=discord_adapter_principal(),
                )
            except Exception as exc:
                trace_id = uuid.uuid4().hex[:8]
                error_summary = summarize_discord_api_error(exc)
                print(f"[discord] /ask failed trace_id={trace_id} error={error_summary}")
                print(traceback.format_exc())
                await message.channel.send(f"Jarvis API error ({trace_id}): {error_summary}")
                return

            response_text = summarize_ask_response(payload)
            for part in split_discord_message(response_text):
                await message.channel.send(
                    part,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

else:

    class DiscordJarvisBot:
        def __init__(
            self,
            command_prefix: str = "!jarvis",
            command_channel_id: int | str | None = None,
            command_guild_id: int | str | None = None,
            permissions_path: str | None = None,
            private_notes_service: Any | None = None,
            private_notes_poll_seconds: float = 30.0,
            turn_service: Any | None = None,
            attachment_ingress: DiscordAttachmentIngressPort | None = None,
            attachment_max_bytes: int = 52428800,
            attachment_max_per_message: int = 4,
        ) -> None:
            del private_notes_service
            del private_notes_poll_seconds
            del turn_service
            del attachment_ingress
            del attachment_max_bytes
            del attachment_max_per_message
            raise RuntimeError(
                "discord.py is not installed. Install dependencies with `pip install -r requirements.txt`."
            )
