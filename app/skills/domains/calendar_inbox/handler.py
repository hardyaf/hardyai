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
    if intent != "calendar_inbox.reconcile":
        return {"status": "error", "message": f"Unsupported calendar-inbox intent `{intent}`."}
    if services.get("calendar_inbox_service") is None:
        return {"status": "error", "message": "Calendar inbox service unavailable."}
    return {
        "status": "scheduler_owned",
        "message": "Calendar inbox reconciliation runs only from its configured clock schedule.",
    }
