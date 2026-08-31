from __future__ import annotations

import importlib
from typing import Any, Callable, Mapping

from app.skills.tool_contracts import (
    ToolArgumentCanonicalizationError,
    ToolCallEnvelope,
    ToolContractError,
    ToolDescriptor,
    canonical_json,
    thaw_json,
)


class SkillExecutionDispatcher:
    def __init__(
        self,
        *,
        lists_service: Any = None,
        calendar_service: Any = None,
        home_service: Any = None,
        email_agent_service: Any | None = None,
        service_bindings: dict[str, Any] | None = None,
        domain_handlers: dict[str, Any] | None = None,
    ) -> None:
        self._services = {
            "lists_service": lists_service,
            "calendar_service": calendar_service,
            "home_service": home_service,
            "email_agent_service": email_agent_service,
        }
        for name, service in (service_bindings or {}).items():
            normalized = str(name or "").strip()
            if not normalized or not normalized.endswith("_service"):
                raise ValueError("skill service binding names must end with _service")
            self._services[normalized] = service
        self._domain_handlers: dict[str, Any] = {}
        for skill_id, handler in (domain_handlers or {}).items():
            normalized_skill_id = str(skill_id or "").strip().casefold()
            if not normalized_skill_id.startswith("skill.") or not callable(
                getattr(handler, "execute_tool", None)
            ):
                raise ValueError("typed domain handlers require a skill ID and execute_tool method")
            self._domain_handlers[normalized_skill_id] = handler
        self._callable_cache: dict[str, Callable[..., Any]] = {}

    def canonicalize_tool_arguments(
        self,
        *,
        skill: dict[str, Any],
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        context: dict[str, Any],
    ) -> Mapping[str, Any]:
        """Run one bounded domain-owned read resolver before identity creation."""

        skill_id = str(skill.get("skill_id") or "").strip().casefold()
        if skill_id != descriptor.skill_id:
            raise ToolArgumentCanonicalizationError("canonicalizer_skill_mismatch")
        handler = self._domain_handlers.get(skill_id)
        canonicalizer = getattr(handler, "canonicalize_tool_arguments", None)
        if not callable(canonicalizer):
            return arguments
        mutable_arguments = thaw_json(arguments)
        before = canonical_json(mutable_arguments)
        try:
            result = canonicalizer(
                tool_id=descriptor.tool_id,
                validated_arguments=mutable_arguments,
                request_context=dict(context),
            )
        except ToolArgumentCanonicalizationError:
            raise
        except Exception as exc:
            raise ToolArgumentCanonicalizationError(
                "argument_canonicalization_failed"
            ) from exc
        if canonical_json(mutable_arguments) != before:
            raise ToolArgumentCanonicalizationError("canonicalizer_mutated_arguments")
        if not isinstance(result, Mapping):
            raise ToolArgumentCanonicalizationError("canonicalizer_result_invalid")
        try:
            canonical_json(result)
        except ToolContractError as exc:
            raise ToolArgumentCanonicalizationError("canonicalizer_result_invalid") from exc
        return result

    def execute_tool(self, envelope: ToolCallEnvelope) -> dict[str, Any] | None:
        """Dispatch a server-built typed envelope through its owning domain contract."""

        if not isinstance(envelope, ToolCallEnvelope):
            return None
        handler = self._domain_handlers.get(envelope.skill_id)
        execute = getattr(handler, "execute_tool", None)
        if not callable(execute):
            return None
        try:
            result = execute(envelope=envelope, services=dict(self._services))
        except Exception:
            return {"status": "error", "message": "Typed skill execution failed."}
        if not isinstance(result, dict):
            return {"status": "error", "message": "Typed skill execution returned invalid output."}
        return result

    @staticmethod
    def _is_safe_execution_ref(value: str) -> bool:
        normalized = value.strip()
        if not normalized:
            return False
        return normalized.startswith("app.skills.domains.") and ":" in normalized

    def _resolve_callable(self, execution_ref: str) -> Callable[..., Any] | None:
        normalized = execution_ref.strip()
        if normalized in self._callable_cache:
            return self._callable_cache[normalized]
        if not self._is_safe_execution_ref(normalized):
            return None

        module_name, _, attr_name = normalized.partition(":")
        module_name = module_name.strip()
        attr_name = attr_name.strip() or "run"
        if not module_name or not attr_name:
            return None

        try:
            module = importlib.import_module(module_name)
        except Exception:
            return None
        handler = getattr(module, attr_name, None)
        if not callable(handler):
            return None
        self._callable_cache[normalized] = handler
        return handler

    def execute(
        self,
        *,
        skill: dict[str, Any] | None,
        intent: str,
        entities: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Legacy intent shim retained until the later Main cutover phase."""

        if not isinstance(skill, dict):
            return None
        execution_ref = str(skill.get("execution_ref") or "").strip()
        if not execution_ref:
            return None

        handler = self._resolve_callable(execution_ref)
        if handler is None:
            return None

        try:
            result = handler(
                intent=intent,
                entities=dict(entities),
                services=self._services,
                context=dict(context),
            )
        except Exception:
            return {"status": "error", "message": "Skill execution failed."}

        if not isinstance(result, dict):
            return {"status": "error", "message": "Skill execution returned invalid output."}
        package_name = execution_ref.partition(":")[0].rsplit(".", 1)[0]
        try:
            receipts_module = importlib.import_module(f"{package_name}.receipts")
            receipt_builder = getattr(receipts_module, "build_operation_receipt", None)
            if callable(receipt_builder):
                receipt = receipt_builder(
                    intent=intent,
                    entities=dict(entities),
                    context=dict(context),
                    result=dict(result),
                    services=self._services,
                )
                if isinstance(receipt, dict):
                    result["_operation_receipt"] = receipt
        except ModuleNotFoundError:
            pass
        except Exception:
            result["_operation_receipt_error"] = "receipt_builder_failed"
        return result

    def describe_capability(
        self,
        *,
        skill: dict[str, Any] | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Describe current availability without executing the skill."""
        if not isinstance(skill, dict):
            return {
                "configured": False,
                "authorized_here": False,
                "availability": "unavailable",
                "access_note": "This skill is not registered for the active user and agent.",
            }
        execution_ref = str(skill.get("execution_ref") or "").strip()
        handler = self._resolve_callable(execution_ref) if execution_ref else None
        if handler is None:
            return {
                "configured": False,
                "authorized_here": False,
                "availability": "unavailable",
                "access_note": "This skill has no active domain handler.",
            }

        module_name = execution_ref.partition(":")[0]
        try:
            module = importlib.import_module(module_name)
            descriptor = getattr(module, "describe_capability", None)
            if callable(descriptor):
                described = descriptor(services=self._services, context=dict(context))
                if isinstance(described, dict):
                    return described
        except Exception:
            return {
                "configured": False,
                "authorized_here": False,
                "availability": "unavailable",
                "access_note": "This skill's availability check failed.",
            }
        return {
            "configured": True,
            "authorized_here": True,
            "availability": "available",
            "access_note": "Available in the current context.",
        }
