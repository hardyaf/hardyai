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
    del services
    # The local calendar is intentionally excluded: its in-memory store cannot
    # be queried safely by a delayed worker after restart.
    supported_intents = {
        "calendar.add_event": "add_event",
        "calendar.update_event": "update_event",
        "calendar.delete_event": "delete_event",
    }
    action = supported_intents.get(intent)
    if action is None or result.get("source") != "google_live":
        return None
    if str(result.get("status") or "").casefold() != "ok":
        return None
    event = result.get("event") if isinstance(result.get("event"), dict) else {}
    event_id = str(event.get("google_event_id") or "").strip()
    calendar_id = str(event.get("host_calendar_id") or "").strip()
    if not event_id or not calendar_id:
        return None

    request_id = str(context.get("request_id") or "untracked")
    resource_key = f"google-calendar-event:{calendar_id}:{event_id}"
    expected = {"exists": intent != "calendar.delete_event"}
    if expected["exists"]:
        expected.update(
            {
                "title": str(event.get("event_title") or ""),
                "start_at": str(event.get("start_at") or ""),
                "end_at": str(event.get("end_at") or ""),
                "attendee_emails": sorted(
                    str(item).strip().casefold()
                    for item in event.get("attendee_emails", [])
                    if str(item).strip()
                ),
            }
        )
    idempotency_key = content_hash(
        {"request_id": request_id, "intent": intent, "calendar_id": calendar_id, "event_id": event_id}
    )
    return {
        "operation_id": str(uuid5(NAMESPACE_URL, f"jarvis:{idempotency_key}")),
        "idempotency_key": idempotency_key,
        "capability": intent,
        "action": action,
        "resource_key": resource_key,
        "provider_resource_id": event_id,
        "provider_revision": str(event.get("google_event_etag") or "") or None,
        "status": "committed",
        "committed_at": iso_utc(),
        "expected_effect": expected,
        "validator_name": "calendar.google",
        "validator_version": "1",
        "resource_locator": {"calendar_id": calendar_id, "event_id": event_id},
        "execution_observation": dict(event),
        "result": {key: value for key, value in result.items() if not str(key).startswith("_")},
    }
