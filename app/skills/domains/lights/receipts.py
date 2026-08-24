from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.tickets.repository import content_hash
from app.tickets.types import iso_utc


def build_operation_receipt(
    *,
    intent: str,
    entities: dict[str, Any],
    context: dict[str, Any],
    result: dict[str, Any],
    services: dict[str, Any],
) -> dict[str, Any] | None:
    if intent not in {"home.set_switch", "home.list_switches"}:
        return None
    home_service = services.get("home_service")
    if home_service is None:
        return None
    if intent == "home.list_switches":
        snapshot = home_service.list_switches()
        states = {
            str(item.get("name") or "").strip().lower(): str(item.get("state") or "").strip().lower()
            for item in snapshot
            if isinstance(item, dict) and item.get("name")
        }
        request_id = str(context.get("request_id") or "untracked").strip() or "untracked"
        idempotency_key = content_hash(
            {"request_id": request_id, "intent": intent, "snapshot": states}
        )
        return {
            "operation_id": str(uuid5(NAMESPACE_URL, f"jarvis:{idempotency_key}")),
            "idempotency_key": idempotency_key,
            "capability": intent,
            "action": "list_switches",
            "resource_key": "switches:all",
            "provider_resource_id": "switches:all",
            "provider_revision": content_hash(snapshot),
            "status": "committed",
            "committed_at": iso_utc(),
            "expected_effect": {"switch_states": states, "read_snapshot": True},
            "validator_name": "home.sqlite_simulated",
            "validator_version": "1",
            "resource_locator": {"switch_names": sorted(states), "simulated": True},
            "execution_observation": {"switch_states": states, "simulated": True},
            "result": {key: value for key, value in result.items() if not str(key).startswith("_")},
        }
    switch_name = str(
        result.get("switch_name")
        or result.get("resolved_switch_name")
        or entities.get("switch_name")
        or ""
    ).strip().lower()
    action = str(result.get("action") or entities.get("action") or "").strip().lower()
    if not switch_name or action not in {"on", "off"}:
        return None
    snapshot = home_service.list_switches()
    state_by_name = {
        str(item.get("name") or "").strip().lower(): str(item.get("state") or "").strip().lower()
        for item in snapshot
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    if str(result.get("scope") or "").strip().lower() == "all":
        targets = [str(item).strip().lower() for item in result.get("affected_switches") or [] if str(item).strip()]
    else:
        targets = [switch_name]
    expected_states = {target: action for target in targets}
    request_id = str(context.get("request_id") or "untracked").strip() or "untracked"
    resource_key = "switches:all" if len(targets) != 1 else f"switch:{targets[0]}"
    idempotency_key = content_hash(
        {"request_id": request_id, "intent": intent, "targets": targets, "action": action}
    )
    committed = str(result.get("status") or "").strip().lower() == "ok"
    return {
        "operation_id": str(uuid5(NAMESPACE_URL, f"jarvis:{idempotency_key}")),
        "idempotency_key": idempotency_key,
        "capability": intent,
        "action": "set_switch",
        "resource_key": resource_key,
        "provider_resource_id": ",".join(targets),
        "provider_revision": content_hash(snapshot),
        "status": "committed" if committed else "attempted",
        "committed_at": iso_utc() if committed else None,
        "expected_effect": {"switch_states": expected_states},
        "validator_name": "home.sqlite_simulated",
        "validator_version": "1",
        "resource_locator": {"switch_names": targets, "simulated": True},
        "execution_observation": {"switch_states": state_by_name, "simulated": True},
        "result": {key: value for key, value in result.items() if not str(key).startswith("_")},
    }
