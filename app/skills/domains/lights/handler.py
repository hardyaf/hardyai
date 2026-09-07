from __future__ import annotations

from typing import Any, Mapping

from app.skills.tool_contracts import (
    ToolArgumentCanonicalizationError,
    ToolCallEnvelope,
    thaw_json,
)


HOME_TYPED_TOOLS = frozenset(
    {"home.list_devices", "home.get_device_state", "home.set_device_state"}
)


def describe_capability(
    *,
    services: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    del context
    available = services.get("home_service") is not None
    return {
        "configured": available,
        "authorized_here": available,
        "availability": "available" if available else "unavailable",
        "access_note": (
            "Authorized reads of the local simulated Home state are available."
            if available
            else "The local simulated Home state service is unavailable."
        ),
    }


class HomeToolHandler:
    """Typed read-only access to the existing simulated Home state authority."""

    SKILL_ID = "skill.home.lights"

    def __init__(self, *, home_service: Any) -> None:
        self._home_service = home_service

    def canonicalize_tool_arguments(
        self,
        *,
        tool_id: str,
        validated_arguments: Mapping[str, Any],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        del request_context
        normalized_tool_id = str(tool_id or "").strip().casefold()
        arguments = dict(validated_arguments)
        if normalized_tool_id not in HOME_TYPED_TOOLS:
            raise ToolArgumentCanonicalizationError("home_tool_unsupported")
        if normalized_tool_id == "home.list_devices":
            if set(arguments) - {"limit"}:
                raise ToolArgumentCanonicalizationError("home_list_arguments_invalid")
            return {"limit": int(arguments.get("limit", 100))}
        if normalized_tool_id == "home.set_device_state":
            if set(arguments) != {"device_ref", "state"}:
                raise ToolArgumentCanonicalizationError("home_device_state_arguments_invalid")
            state = str(arguments.get("state") or "").strip().casefold()
            if state not in {"on", "off"}:
                raise ToolArgumentCanonicalizationError("home_device_state_invalid")
            selector = self._home_service.canonicalize_device_selector(
                device_ref=str(arguments.get("device_ref") or "").strip() or None,
            )
            return {"device_ref": str(selector.get("device_ref") or ""), "state": state}
        if len(arguments) != 1 or not set(arguments).issubset({"device_ref", "name"}):
            raise ToolArgumentCanonicalizationError("home_device_selector_invalid")
        return self._home_service.canonicalize_device_selector(
            device_ref=str(arguments.get("device_ref") or "").strip() or None,
            name=str(arguments.get("name") or "").strip() or None,
        )

    def execute_tool(
        self,
        *,
        envelope: ToolCallEnvelope,
        services: dict[str, Any],
    ) -> dict[str, Any]:
        del services
        if not isinstance(envelope, ToolCallEnvelope) or envelope.skill_id != self.SKILL_ID:
            return self._denied("home_tool_envelope_invalid")
        if envelope.tool_id not in HOME_TYPED_TOOLS:
            return self._denied("home_tool_unsupported")
        if not envelope.user_id.strip():
            return self._denied("home_tool_user_missing")
        arguments = thaw_json(envelope.arguments)
        if envelope.tool_id == "home.list_devices":
            payload = self._home_service.list_devices(limit=int(arguments.get("limit", 100)))
            return {
                "status": "ok",
                "message": (
                    f"Found {len(payload['devices'])} configured device(s) in simulated Home state."
                ),
                "payload": payload,
            }
        if envelope.tool_id == "home.get_device_state":
            return self._home_service.get_device_state(
                device_ref=str(arguments.get("device_ref") or "").strip() or None,
                name=str(arguments.get("name") or "").strip() or None,
            )
        result = self._home_service.set_device_state(
            device_ref=str(arguments.get("device_ref") or "").strip() or None,
            state=str(arguments.get("state") or ""),
            source_interface=envelope.source_interface,
            requested_by_user_id=envelope.user_id,
            operation_id=envelope.operation_id,
            arguments_hash=envelope.arguments_hash,
        )
        if result.get("status") == "ok":
            result["receipt_id"] = "home_receipt:" + envelope.operation_id
            payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
            result["committed_effect"] = bool(payload.get("changed")) and not bool(
                payload.get("idempotent_replay")
            )
        return result

    @staticmethod
    def _denied(reason: str) -> dict[str, Any]:
        return {
            "status": "policy_denied",
            "message": "This Home operation is not available in the current request context.",
            "denial_reason": reason,
        }


def run(
    *,
    intent: str,
    entities: dict[str, Any],
    services: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    home_service = services.get("home_service")
    if home_service is None:
        return {"status": "error", "message": "Home service unavailable."}

    if intent in {"home.list_devices", "home.list_switches"}:
        if intent == "home.list_switches":
            return {"status": "ok", "switches": home_service.list_switches(), "simulated": True}
        payload = home_service.list_devices(limit=int(entities.get("limit", 100)))
        return {
            "status": "ok",
            "message": "Listed configured devices from simulated Home state.",
            "payload": payload,
        }
    if intent in {"home.get_device_state", "home.get_switch_state"}:
        return home_service.get_device_state(
            device_ref=str(entities.get("device_ref") or "").strip() or None,
            name=str(
                entities.get("name")
                or entities.get("switch_name")
                or ""
            ).strip()
            or None,
        )
    if intent != "home.set_switch":
        return {"status": "error", "message": f"Unsupported lights intent `{intent}`."}

    return home_service.set_switch(
        switch_name=str(entities.get("switch_name") or ""),
        action=str(entities.get("action") or ""),
        source_interface=str(context.get("source_interface") or "") or None,
        requested_by_user_id=str(context.get("requested_by_user_id") or "") or None,
    )
