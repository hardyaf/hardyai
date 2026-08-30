from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.config import settings
from app.api.operator_auth import require_operator
from app.dependencies import get_event_log, get_runtime_power
from app.core.state_machine import RuntimePowerController
from app.services.event_log import EventLogService

router = APIRouter(tags=["dashboard"])

_DASHBOARD_PATH = (Path(__file__).resolve().parents[2] / "ui" / "dashboard.html").resolve()
_STATUS_DASHBOARD_PATH = (Path(__file__).resolve().parents[2] / "ui" / "status_dashboard.html").resolve()


@router.get("/", include_in_schema=False)
@router.get("/dashboard", include_in_schema=False)
@router.get("/voice-test", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(_DASHBOARD_PATH)


@router.get("/status-dashboard", include_in_schema=False)
async def status_dashboard() -> FileResponse:
    return FileResponse(_STATUS_DASHBOARD_PATH)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_event(events: list[dict[str, Any]], *, event_type: str | None = None, prefix: str | None = None) -> dict[str, Any] | None:
    for row in reversed(events):
        current_type = str(row.get("event_type") or "")
        if event_type and current_type != event_type:
            continue
        if prefix and not current_type.startswith(prefix):
            continue
        return row
    return None


def _safe_status_event(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(event, dict) or not settings.action_tickets_enabled:
        return event
    safe = dict(event)
    payload = safe.get("payload")
    if isinstance(payload, dict):
        safe["payload"] = {
            key: value
            for key, value in payload.items()
            if key not in {"text", "normalized_text", "request_text", "assistant_text"}
        }
    return safe


@router.get("/dashboard/status", dependencies=[Depends(require_operator)])
async def dashboard_status(
    event_log: EventLogService = Depends(get_event_log),
    runtime_power: RuntimePowerController = Depends(get_runtime_power),
) -> dict[str, Any]:
    events = event_log.recent(limit=400)
    latest_input = _latest_event(events, event_type="input.received")
    latest_response = _latest_event(events, event_type="response.generated")
    latest_handoff = _latest_event(events, prefix="handoff.")
    latest_runtime_event = _latest_event(events, prefix="runtime.")
    latest_models_event = _latest_event(events, event_type="runtime.models.changed")
    latest_context_packet_event = _latest_event(events, event_type="context.packet.built")
    latest_pending_transition_event = _latest_event(events, event_type="context.pending_interaction.transition")
    latest_entity_registry_event = _latest_event(events, event_type="context.entity_registry.updated")
    latest_summary_update_event = _latest_event(events, event_type="context.session_summary.updated")
    compute_budget_escalations = [
        _safe_status_event(row)
        for row in events
        if str(row.get("event_type") or "") == "model.compute_budget.escalated"
    ]

    latest_payload = latest_response.get("payload") if isinstance(latest_response, dict) else {}
    if not isinstance(latest_payload, dict):
        latest_payload = {}
    input_payload = latest_input.get("payload") if isinstance(latest_input, dict) else {}
    if not isinstance(input_payload, dict):
        input_payload = {}

    active_channel = str(latest_payload.get("channel_key") or input_payload.get("channel_key") or "").strip() or None
    active_source = str(latest_payload.get("source") or input_payload.get("source") or "").strip() or None

    model_runtime = runtime_power.model_runtime_status()
    route = str(latest_payload.get("route") or "").strip().lower()
    owner = str(latest_payload.get("owner") or "").strip().lower()
    main_runtime_active = bool(model_runtime.get("larger_models_active") is True)
    if main_runtime_active:
        active_model_lane = "main"
        active_model_name = settings.main_repair_model_name or settings.micro_model_name
    elif owner == "main_jarvis" or route.startswith("main_jarvis"):
        # Main handled the latest turn but runtime has cooled down; micro is now active lane.
        active_model_lane = "micro"
        active_model_name = settings.micro_model_name
    else:
        active_model_lane = "micro"
        active_model_name = settings.micro_model_name

    if owner == "main_jarvis" and main_runtime_active:
        effective_owner = "main_jarvis"
    elif owner == "main_jarvis" and not main_runtime_active:
        effective_owner = "micro_jarvis"
    elif owner:
        effective_owner = owner
    else:
        effective_owner = "micro_jarvis" if not main_runtime_active else "main_jarvis"

    if owner == "main_jarvis" or route.startswith("main_jarvis"):
        owner_note = "latest turn was main-owned"
    elif owner:
        owner_note = "latest turn was non-main"
    else:
        owner_note = "no recent response"

    return {
        "timestamp": _utc_now(),
        "power_state": runtime_power.state.value,
        "model_runtime": model_runtime,
        "active_channel": active_channel,
        "active_source": active_source,
        "effective_owner": effective_owner,
        "owner_note": owner_note,
        "active_model_lane": active_model_lane,
        "active_model_name": active_model_name,
        "configured_models": {
            "micro_model_name": settings.micro_model_name,
            "main_model_name": settings.main_repair_model_name,
        },
        "adaptive_compute_budget": {
            "enabled": settings.model_adaptive_token_budget_enabled,
            "max_attempts": settings.model_adaptive_token_max_attempts,
            "growth_factor": settings.model_adaptive_token_growth_factor,
            "max_multiplier": settings.model_adaptive_token_max_multiplier,
            "recent_escalation_count": len(compute_budget_escalations),
            "latest_escalation": (
                compute_budget_escalations[-1] if compute_budget_escalations else None
            ),
        },
        "latest_input": _safe_status_event(latest_input),
        "latest_response": latest_response,
        "latest_handoff": latest_handoff,
        "latest_runtime_event": latest_runtime_event,
        "latest_model_runtime_event": latest_models_event,
        "latest_context_packet_event": latest_context_packet_event,
        "latest_pending_transition_event": latest_pending_transition_event,
        "latest_entity_registry_event": latest_entity_registry_event,
        "latest_summary_update_event": latest_summary_update_event,
        "poll_interval_seconds": 10,
    }
