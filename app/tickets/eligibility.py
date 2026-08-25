from __future__ import annotations

from typing import Any


CONVERSATION_SKILL_ID = "skill.conversation.general"
EMAIL_SKILL_ID = "skill.email.agent"


def is_confirmed_conversation(
    *,
    intent: str,
    route: str,
    result: dict[str, Any],
    skill_id: str | None,
) -> bool:
    normalized_intent = str(intent or "").strip().lower()
    normalized_route = str(route or "").strip().lower()
    normalized_skill = str(skill_id or "").strip().lower()
    status = str(result.get("status") or "").strip().lower()

    if status != "conversation":
        return False
    if normalized_skill == CONVERSATION_SKILL_ID:
        return True
    return normalized_intent in {"conversation.general", "unknown"} and normalized_route in {
        "main_jarvis",
        "main_jarvis_repair",
    }


def ticket_is_eligible(
    *,
    intent: str,
    route: str,
    result: dict[str, Any],
    skill_id: str | None,
) -> bool:
    normalized_intent = str(intent or "").strip().lower()
    normalized_skill = str(skill_id or "").strip().lower()
    if (
        normalized_skill == EMAIL_SKILL_ID
        or normalized_intent.startswith("email.")
        or normalized_intent.startswith("documents.")
    ):
        return False
    return not is_confirmed_conversation(
        intent=intent,
        route=route,
        result=result,
        skill_id=skill_id,
    )
