from __future__ import annotations

import re
from typing import Any, Callable

from app.context.reference_resolver import ReferenceResolver
from app.context.types import EntityRegistry


CONVERSATION_INTENTS = {"conversation.general", "unknown"}
YES_VALUES = {"yes", "yeah", "yep", "yup", "sure", "correct", "right", "affirmative", "please do"}
NO_VALUES = {"no", "nope", "nah", "negative", "dont", "don't", "stop"}
ACTION_PREFIX_PATTERN = re.compile(
    r"^(?:add|create|make|show|get|delete|remove|turn|set|schedule|invite|mark|open|close)\b",
    flags=re.IGNORECASE,
)


class ConversationContextContract:
    contract_id = "conversation"

    def supports_intent(self, *, intent: str) -> bool:
        return str(intent or "").strip().lower() in CONVERSATION_INTENTS

    def normalize_entities(self, *, intent: str, entities: dict[str, Any]) -> dict[str, Any]:
        del intent
        return dict(entities)

    def apply_text_constraints(
        self,
        *,
        intent: str,
        text: str,
        entities: dict[str, Any],
    ) -> dict[str, Any]:
        del intent
        del text
        return dict(entities)

    def emit_context_updates(self, *, intent: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        del intent
        del result
        return []

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
        return None

    def clarification_question(
        self,
        *,
        intent: str,
        field_name: str,
    ) -> str | None:
        del intent
        del field_name
        return None

    def continue_pending_interaction(
        self,
        *,
        intent: str,
        text: str,
        missing_fields: list[str],
        current_entities: dict[str, Any],
    ) -> dict[str, Any]:
        intent_value = str(intent or "").strip().lower()
        if intent_value not in CONVERSATION_INTENTS:
            return {}
        normalized_missing = [str(item).strip().lower() for item in missing_fields if str(item).strip()]
        cleaned_text = _normalize_text(text)
        if not cleaned_text:
            return {}

        updates: dict[str, Any] = {}
        if "confirmation" in normalized_missing:
            confirmation = _extract_confirmation(cleaned_text)
            if confirmation:
                updates["confirmation"] = confirmation

        wants_topic = {"topic_subject", "topic_entity"} & set(normalized_missing)
        if wants_topic:
            topic = _extract_topic_subject(
                cleaned_text=cleaned_text,
                current_entities=current_entities,
            )
            if topic:
                updates["topic_subject"] = topic
                if "topic_entity" in normalized_missing:
                    updates["topic_entity"] = topic

        if not normalized_missing:
            topic = _extract_topic_subject(
                cleaned_text=cleaned_text,
                current_entities=current_entities,
            )
            if topic:
                updates["topic_subject"] = topic
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
        del intent
        del status
        del tool_result
        del entities
        del missing_fields
        del question
        del registry
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
        del registry
        del context_reference
        del runtime_context
        del intent
        del route
        return {}


def _normalize_text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "").strip())
    return cleaned


def _extract_confirmation(cleaned_text: str) -> str | None:
    lowered = re.sub(r"[^a-z0-9'\s]+", " ", cleaned_text.lower())
    lowered = re.sub(r"\s+", " ", lowered).strip()
    if lowered in YES_VALUES:
        return "yes"
    if lowered in NO_VALUES:
        return "no"
    return None


def _extract_topic_subject(
    *,
    cleaned_text: str,
    current_entities: dict[str, Any],
) -> str | None:
    del current_entities
    candidate = cleaned_text.strip().strip("`\"' ")
    if not candidate:
        return None
    if len(candidate) > 90:
        return None

    normalized = re.sub(r"^(?:it is|it's|its|about|i mean)\s+", "", candidate, flags=re.IGNORECASE).strip()
    if not normalized:
        return None

    normalized = normalized.strip(".,;:!? ").strip("`\"' ")
    if not normalized:
        return None

    lowered = normalized.lower()
    if lowered in YES_VALUES or lowered in NO_VALUES:
        return None
    if lowered in {"it", "that", "this", "them", "they", "he", "she"}:
        return None

    token_count = len([part for part in normalized.split(" ") if part])
    if token_count > 10:
        return None
    if token_count > 2 and ACTION_PREFIX_PATTERN.match(lowered):
        return None
    if token_count > 6 and normalized.endswith("?"):
        return None
    if normalized.endswith("?") and token_count <= 6:
        normalized = normalized[:-1].strip()
    return normalized or None
