from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.schemas.api import AskRequest


class PrincipalKind(str, Enum):
    OPERATOR = "operator"
    DISCORD_ADAPTER = "discord_adapter"
    TEST = "test"


@dataclass(frozen=True)
class RequestPrincipal:
    subject: str
    kind: PrincipalKind
    user_id: str
    source: str
    scopes: frozenset[str]
    authenticated_by: str

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def discord_adapter_principal() -> RequestPrincipal:
    return RequestPrincipal(
        subject="embedded-discord-adapter",
        kind=PrincipalKind.DISCORD_ADAPTER,
        user_id="discord_user",
        source="discord",
        scopes=frozenset({"ask", "discord"}),
        authenticated_by="in_process",
    )


_UNTRUSTED_CONTEXT_KEYS = {
    "agent_id",
    "agent_display_name",
    "age_band",
    "content_profile",
    "identity_bound",
    "is_child",
    "policy_profile",
    "presentation_profile",
    "principal_kind",
    "principal_subject",
    "skill_scopes",
}

_DISCORD_CONTEXT_KEYS = {
    "auto_channel_session",
    "discord_channel_id",
    "discord_guild_id",
    "discord_message_id",
    "discord_role_ids",
    "discord_routing_lane",
    "document_attachment_ids",
    "external_display_name",
    "external_message_id",
    "external_user_id",
    "force_main_owner",
    "micro_command_explicit",
    "request_id",
    "session_channel",
    "wake_on_message",
}

_OPERATOR_CONTEXT_KEYS = {
    "auto_channel_session",
    "channel_session_scope",
    "mode",
    "request_id",
    "session_channel",
    "wake_on_message",
}


def trusted_ask_request(payload: AskRequest, principal: RequestPrincipal) -> AskRequest:
    """Build the only request envelope allowed to cross into the router."""

    supplied_context = dict(payload.context)
    for key in _UNTRUSTED_CONTEXT_KEYS:
        supplied_context.pop(key, None)

    if principal.kind == PrincipalKind.DISCORD_ADAPTER:
        context: dict[str, Any] = {
            key: supplied_context[key]
            for key in _DISCORD_CONTEXT_KEYS
            if key in supplied_context
        }
        raw_scopes = payload.context.get("skill_scopes")
        if isinstance(raw_scopes, list):
            context["skill_scopes"] = [
                str(item).strip()
                for item in raw_scopes[:32]
                if isinstance(item, str) and str(item).strip()
            ]
        external_user_id = str(context.get("external_user_id") or payload.user_id or "").strip()
        context["external_user_id"] = external_user_id
        source = "discord"
        user_id = external_user_id or principal.user_id
    elif principal.kind == PrincipalKind.TEST:
        context = supplied_context
        source = payload.source
        user_id = payload.user_id
    else:
        context = {
            key: supplied_context[key]
            for key in _OPERATOR_CONTEXT_KEYS
            if key in supplied_context
        }
        source = principal.source
        user_id = principal.user_id

    context["principal_kind"] = principal.kind.value
    context["principal_subject"] = principal.subject
    context["principal_authenticated_by"] = principal.authenticated_by

    return AskRequest(
        text=payload.text,
        request_id=payload.request_id,
        session_id=payload.session_id,
        user_id=user_id,
        source=source,
        context=context,
    )
