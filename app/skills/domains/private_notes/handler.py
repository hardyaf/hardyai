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
    del context
    if intent not in {
        "private_notes.capture",
        "private_notes.compile_digest",
        "private_notes.deliver_digest",
    }:
        return {"status": "error", "message": f"Unsupported private-notes intent `{intent}`."}
    if services.get("private_notes_service") is None:
        return {"status": "error", "message": "Private notes service unavailable."}
    return {
        "status": "adapter_owned",
        "message": "Private notes operations require a configured Discord event or schedule trigger.",
    }

