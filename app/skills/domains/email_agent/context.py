from __future__ import annotations

from typing import Any, Callable

from app.context.reference_resolver import ReferenceResolver
from app.context.types import EntityRegistry
from app.skills.domains.email_agent.service import EMAIL_INTENTS


class EmailAgentContextContract:
    contract_id = "email"

    def __init__(self, *, email_agent_service: Any | None = None) -> None:
        self._email_agent_service = email_agent_service

    def supports_intent(self, *, intent: str) -> bool:
        return str(intent or "").strip().casefold() in EMAIL_INTENTS

    def emit_context_updates(self, *, intent: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        del intent
        rows = result.get("email_context_entities")
        return [dict(item) for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []

    def enrich_working_context(
        self,
        *,
        request_context: dict[str, Any],
        working_context: dict[str, Any],
    ) -> dict[str, Any]:
        if self._email_agent_service is None:
            return {}
        entity_hints = working_context.get("entity_hints")
        if isinstance(entity_hints, list) and any(
            isinstance(item, dict)
            and str(item.get("domain") or "").strip().casefold() == "email"
            for item in entity_hints
        ):
            return {}
        if isinstance(entity_hints, list) and any(
            isinstance(item, dict)
            and str(item.get("domain") or "").strip().casefold()
            not in {"", "conversation"}
            for item in entity_hints
        ):
            return {}
        hint = self._email_agent_service.working_context_hint(context=request_context)
        return {"active_skill_context": hint} if isinstance(hint, dict) and hint else {}

    def resolve_followup(
        self,
        *,
        decision: Any,
        registry: EntityRegistry,
        resolver: ReferenceResolver,
        required_fields_for_intent: Callable[[Any, dict[str, Any]], list[str]],
        has_blocking_ambiguity: Callable[[Any], bool],
    ) -> Any:
        del registry
        del resolver
        del required_fields_for_intent
        del has_blocking_ambiguity
        return decision

    def refine_missing_fields(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        missing_fields: list[str],
        resolver: ReferenceResolver,
    ) -> list[str]:
        del intent
        del entities
        del resolver
        return [str(item).strip() for item in missing_fields if str(item).strip()]

    def required_fields(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        resolver: ReferenceResolver,
    ) -> list[str] | None:
        del intent
        del entities
        del resolver
        # Durable E# state can supply a focused message, so the service owns
        # clarification without exposing Gmail IDs through generic context.
        return []

    def clarification_question(self, *, intent: str, field_name: str) -> str | None:
        del intent
        if field_name == "email_reference":
            return "Which email reference, such as E1 or E2, do you mean?"
        if field_name == "category_key":
            return "Which shared email category should I use?"
        if field_name == "snooze_until":
            return "When should Jarvis bring this email back?"
        return None

    def continue_pending_interaction(
        self,
        *,
        intent: str,
        text: str,
        missing_fields: list[str],
        current_entities: dict[str, Any],
    ) -> dict[str, Any]:
        del intent
        del current_entities
        missing = {str(item).strip().casefold() for item in missing_fields}
        updates: dict[str, Any] = {}
        if "email_reference" in missing:
            import re

            match = re.search(r"\bE\d{1,2}\b", str(text or ""), flags=re.IGNORECASE)
            if match:
                updates["reference"] = match.group(0).upper()
        if "category_key" in missing:
            updates["category"] = str(text or "").strip()
        if "snooze_until" in missing:
            updates["until"] = str(text or "").strip()
        return updates

    def shape_tool_followup(
        self,
        *,
        intent: str,
        status: str,
        tool_result: dict[str, Any],
        entities: dict[str, Any],
        missing_fields: list[str],
        question: str | None,
        registry: EntityRegistry,
    ) -> dict[str, Any]:
        del intent, status, tool_result, entities, missing_fields, question, registry
        return {}

    def legacy_main_handoff_hints(
        self,
        *,
        registry: EntityRegistry,
        context_reference: dict[str, Any],
        runtime_context: dict[str, Any] | None = None,
        intent: str | None = None,
        route: str | None = None,
    ) -> dict[str, Any]:
        del context_reference, runtime_context, intent, route
        candidates = [
            item for item in registry.entities
            if item.domain == "email" and item.entity_type == "message"
        ]
        candidates.sort(key=lambda item: float(item.salience), reverse=True)
        if not candidates:
            return {}
        latest = candidates[0]
        return {
            "last_email_result_refs": [str(item.display_name) for item in candidates[:10]],
            "focused_email_message_id": latest.resolution_hints.get("gmail_message_id"),
            "focused_email_thread_id": latest.resolution_hints.get("gmail_thread_id"),
            "last_email_reference_set_id": latest.resolution_hints.get("reference_set_id"),
            "last_email_source_route": latest.resolution_hints.get("source_route_key"),
            "last_email_category_key": latest.resolution_hints.get("category_key"),
        }

    def memory_handoff_hints(
        self,
        *,
        relevant_memory: list[dict[str, Any]],
        intent: str | None = None,
        request_text: str | None = None,
    ) -> dict[str, Any]:
        del relevant_memory, intent, request_text
        return {}
