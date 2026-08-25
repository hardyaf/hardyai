from __future__ import annotations

from typing import Any


def describe_capability(
    *,
    services: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    service = services.get("email_agent_service")
    if service is None:
        return {
            "configured": False,
            "authorized_here": False,
            "availability": "disabled",
            "access_note": "The shared email agent is not enabled or configured.",
        }
    describe = getattr(service, "capability_access", None)
    if not callable(describe):
        return {
            "configured": True,
            "authorized_here": False,
            "availability": "restricted",
            "access_note": "The shared email agent requires an authorized private channel.",
        }
    result = describe(context=context)
    return result if isinstance(result, dict) else {
        "configured": True,
        "authorized_here": False,
        "availability": "restricted",
        "access_note": "The shared email agent requires an authorized private channel.",
    }


def run(
    *,
    intent: str,
    entities: dict[str, Any],
    services: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    service = services.get("email_agent_service")
    if service is None:
        return {
            "status": "disabled",
            "message": "The email agent is not enabled or configured yet.",
        }
    result = service.execute(intent=intent, entities=entities, context=context)
    if isinstance(result, dict):
        result.setdefault("_persistence_policy", "sensitive_domain")
    return result
