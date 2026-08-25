from __future__ import annotations

from typing import Any


def describe_capability(*, services: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    service = services.get("documents_service")
    if service is None:
        return {
            "configured": False,
            "authorized_here": False,
            "availability": "disabled",
            "access_note": "The local Documents service is disabled.",
        }
    return service.capability_access(context=context)


def run(
    *,
    intent: str,
    entities: dict[str, Any],
    services: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    service = services.get("documents_service")
    if service is None:
        return {
            "status": "disabled",
            "message": "The local Documents service is disabled.",
            "_persistence_policy": "restricted_read",
        }
    return service.execute(intent=intent, entities=entities, context=context)
