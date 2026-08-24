from __future__ import annotations

from typing import Any


def run(
    *,
    intent: str,
    entities: dict[str, Any],
    services: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    if intent != "home.set_switch":
        return {"status": "error", "message": f"Unsupported lights intent `{intent}`."}

    home_service = services.get("home_service")
    if home_service is None:
        return {"status": "error", "message": "Home service unavailable."}

    return home_service.set_switch(
        switch_name=str(entities.get("switch_name") or ""),
        action=str(entities.get("action") or ""),
        source_interface=str(context.get("source_interface") or "") or None,
        requested_by_user_id=str(context.get("requested_by_user_id") or "") or None,
    )

