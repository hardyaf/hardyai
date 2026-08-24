from __future__ import annotations

from typing import Any


def run(
    *,
    intent: str,
    entities: dict[str, Any],
    services: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    del entities
    del services
    del context
    if intent not in {"conversation.general", "unknown"}:
        return {"status": "error", "message": f"Unsupported conversation intent `{intent}`."}
    return {
        "status": "conversation",
        "message": "Conversation is handled by Main Jarvis.",
    }

