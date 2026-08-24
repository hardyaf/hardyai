from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

from app.context.reference_resolver import ReferenceResolver
from app.context.types import EntityRegistry
from app.core.types import SessionOwner


LIST_INTENTS = {
    "lists.create_list",
    "lists.add_item",
    "lists.get_items",
    "lists.delete_list",
    "lists.remove_item",
    "lists.mark_item_done",
}


def emit_context_entities(*, intent: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    if intent not in LIST_INTENTS:
        return []
    status = str(result.get("status") or "").strip().lower()
    if status not in {"ok", "partial"}:
        return []
    list_name = str(result.get("list_name") or "").strip()
    if not list_name:
        return []
    aliases = _aliases_for_list_name(list_name)
    salience = 0.93 if intent == "lists.get_items" else 0.9
    return [
        {
            "domain": "lists",
            "entity_type": "list",
            "display_name": list_name,
            "aliases": aliases,
            "salience": salience,
            "resolution_hints": {
                "intent": intent,
                "status": status,
            },
        }
    ]


def resolve_deictic_list_name(
    *,
    list_name: str,
    resolver: ReferenceResolver,
    registry: EntityRegistry,
) -> str | None:
    resolved = resolver.resolve_reference(
        value=list_name,
        registry=registry,
        domain="lists",
        entity_type="list",
        deictic_only=True,
    )
    if resolved is None:
        return None
    return str(resolved.entity.display_name or "").strip() or None


def _aliases_for_list_name(list_name: str) -> list[str]:
    base = re.sub(r"\s+", " ", list_name.strip().lower()).strip()
    aliases = {base}
    if base.endswith(" list"):
        aliases.add(base[: -len(" list")].strip())
    aliases.add(re.sub(r"^(?:my|the|our)\s+", "", base).strip())
    return sorted(item for item in aliases if item)


class ListsContextContract:
    contract_id = "lists"
    _FOLLOWUP_INTENTS = {
        "lists.get_items",
        "lists.add_item",
        "lists.delete_list",
        "lists.remove_item",
        "lists.mark_item_done",
    }

    def supports_intent(self, *, intent: str) -> bool:
        return str(intent or "").strip().lower() in LIST_INTENTS

    def emit_context_updates(self, *, intent: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        return emit_context_entities(intent=intent, result=result)

    def resolve_followup(
        self,
        *,
        decision: Any,
        registry: EntityRegistry,
        resolver: ReferenceResolver,
        required_fields_for_intent: Callable[[Any, dict[str, Any]], list[str]],
        has_blocking_ambiguity: Callable[[Any], bool],
    ) -> Any:
        intent_value = str(getattr(getattr(decision, "intent", None), "value", "")).strip().lower()
        if not intent_value:
            intent_value = str(getattr(decision, "intent", "")).strip().lower()
        if intent_value not in self._FOLLOWUP_INTENTS:
            return decision

        entities = getattr(decision, "entities", {})
        if not isinstance(entities, dict):
            return decision
        list_name_raw = entities.get("list_name")
        list_name = str(list_name_raw).strip() if list_name_raw is not None else ""
        resolved_list_name = resolve_deictic_list_name(
            list_name=list_name,
            resolver=resolver,
            registry=registry,
        )
        if not resolved_list_name:
            return decision

        entities["list_name"] = resolved_list_name
        decision.entities = entities
        decision.ambiguity_flags = [
            str(flag)
            for flag in getattr(decision, "ambiguity_flags", [])
            if str(flag).strip().lower() != "deictic_list_reference"
        ]
        if "list_reference_resolved_from_context" not in decision.ambiguity_flags:
            decision.ambiguity_flags.append("list_reference_resolved_from_context")
        if getattr(decision, "reasoning", ""):
            decision.reasoning = f"{decision.reasoning}_with_list_context"
        else:
            decision.reasoning = "list_context_resolution"

        try:
            current_intent = getattr(decision, "intent")
            missing = required_fields_for_intent(current_intent, decision.entities)
        except Exception:
            missing = []
        confidence_floor = 0.89 if intent_value == "lists.get_items" else 0.85
        decision.confidence = max(float(getattr(decision, "confidence", 0.0)), confidence_floor)
        if not missing and not has_blocking_ambiguity(decision):
            decision.recommended_owner = SessionOwner.MICRO
        return decision

    def refine_missing_fields(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        missing_fields: list[str],
        resolver: ReferenceResolver,
    ) -> list[str]:
        intent_value = str(intent or "").strip().lower()
        next_missing: list[str] = []
        for field_name in missing_fields:
            candidate = str(field_name).strip()
            if candidate and candidate not in next_missing:
                next_missing.append(candidate)

        if intent_value in {"lists.add_item", "lists.remove_item", "lists.mark_item_done"}:
            list_name = str(entities.get("list_name") or "").strip()
            if resolver.is_deictic_reference(value=list_name, entity_type="list") and "list_name" not in next_missing:
                next_missing.append("list_name")

        if intent_value in self._FOLLOWUP_INTENTS:
            list_name = str(entities.get("list_name") or "").strip()
            available_lists = _as_clean_str_list(entities.get("available_lists"))
            if list_name and available_lists:
                normalized_list_name = _normalize_list_label(list_name)
                normalized_available = {
                    _normalize_list_label(candidate)
                    for candidate in available_lists
                    if _normalize_list_label(candidate)
                }
                if normalized_list_name and normalized_list_name not in normalized_available:
                    if "list_name" not in next_missing:
                        next_missing.append("list_name")

        return next_missing

    def required_fields(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        resolver: ReferenceResolver,
    ) -> list[str] | None:
        intent_value = str(intent or "").strip().lower()
        required_by_intent: dict[str, list[str]] = {
            "lists.create_list": ["list_name"],
            "lists.add_item": ["list_name", "item_text"],
            "lists.get_items": ["list_name"],
            "lists.delete_list": ["list_name"],
            "lists.remove_item": ["list_name", "item_text"],
            "lists.mark_item_done": ["list_name", "item_text"],
        }
        required = required_by_intent.get(intent_value)
        if required is None:
            return None

        missing: list[str] = []
        for field_name in required:
            value = entities.get(field_name)
            if value is None:
                missing.append(field_name)
                continue
            if isinstance(value, str) and not value.strip():
                missing.append(field_name)
        return self.refine_missing_fields(
            intent=intent_value,
            entities=entities,
            missing_fields=missing,
            resolver=resolver,
        )

    def clarification_question(
        self,
        *,
        intent: str,
        field_name: str,
    ) -> str | None:
        prompts = {
            ("lists.add_item", "item_text"): "What item should I add?",
            ("lists.add_item", "list_name"): "Which list should I add that to?",
            ("lists.create_list", "list_name"): "What should I call the new list?",
            ("lists.get_items", "list_name"): "Which list should I show?",
            ("lists.delete_list", "list_name"): "Which list should I delete?",
            ("lists.remove_item", "list_name"): "Which list should I remove that item from?",
            ("lists.remove_item", "item_text"): "Which item should I remove?",
            ("lists.mark_item_done", "list_name"): "Which list is that item on?",
            ("lists.mark_item_done", "item_text"): "Which item should I mark complete?",
            ("lists.mark_item_done", "completion_mode"): "Should I remove it from the list, or just mark it done?",
        }
        return prompts.get((str(intent or "").strip().lower(), str(field_name or "").strip()))

    def continue_pending_interaction(
        self,
        *,
        intent: str,
        text: str,
        missing_fields: list[str],
        current_entities: dict[str, Any],
    ) -> dict[str, Any]:
        intent_value = str(intent or "").strip().lower()
        if intent_value not in LIST_INTENTS:
            return {}
        if not text.strip() or not missing_fields:
            return {}
        updates: dict[str, Any] = {}
        if "list_name" in missing_fields:
            resolved_list = _resolve_list_name_from_pending_context(
                text=text,
                current_entities=current_entities,
            )
            if resolved_list:
                updates["list_name"] = resolved_list
        if "item_text" in missing_fields:
            resolved_item = _resolve_item_from_pending_context(
                text=text,
                current_entities=current_entities,
            )
            if resolved_item:
                updates["item_text"] = resolved_item
        if "completion_mode" in missing_fields:
            mode = _resolve_completion_mode_from_pending_context(text=text)
            if mode:
                updates["completion_mode"] = mode
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
        intent_value = str(intent or "").strip().lower()
        if intent_value not in LIST_INTENTS:
            return {}

        next_entities = dict(entities)
        next_missing = [str(item).strip() for item in missing_fields if str(item).strip()]
        next_question = str(question).strip() if isinstance(question, str) and str(question).strip() else None

        if str(status or "").strip().lower() == "unknown_list":
            if "list_name" not in next_missing:
                next_missing.append("list_name")
            available_lists = _as_clean_str_list(tool_result.get("available_lists"))
            if available_lists:
                next_entities["available_lists"] = available_lists
            suggestions = _as_clean_str_list(tool_result.get("suggestions"))
            if suggestions:
                next_entities["list_suggestions"] = suggestions
            last_list_name = str(next_entities.get("last_list_name") or "").strip()
            if not last_list_name:
                registry_last_list = _latest_entity_display_name(
                    registry=registry,
                    domain="lists",
                    entity_type="list",
                )
                if registry_last_list:
                    next_entities["last_list_name"] = registry_last_list

        if str(status or "").strip().lower() == "unknown_item" and intent_value in {
            "lists.remove_item",
            "lists.mark_item_done",
        }:
            if "item_text" not in next_missing:
                next_missing.append("item_text")
            suggestions = _as_clean_str_list(tool_result.get("item_suggestions"))
            if suggestions:
                next_entities["item_suggestions"] = suggestions
            available_items = _as_clean_str_list(tool_result.get("available_items"))
            if available_items:
                next_entities["available_items"] = available_items

        if next_question is None and "list_name" in next_missing:
            suggestions = _as_clean_str_list(next_entities.get("list_suggestions"))
            if suggestions:
                top_suggestions = ", ".join(f"`{item}`" for item in suggestions[:3])
                next_question = f"Did you mean {top_suggestions}? To create a new one, say `create <name> list`."
            else:
                last_list_name = str(next_entities.get("last_list_name") or "").strip()
                if last_list_name:
                    next_question = (
                        f"Do you want `{last_list_name}`? "
                        "If not, tell me the list name or say `create <name> list`."
                    )

        if (
            next_question is None
            and intent_value in {"lists.remove_item", "lists.mark_item_done"}
            and "item_text" in next_missing
        ):
            suggestions = _as_clean_str_list(next_entities.get("item_suggestions"))
            if suggestions:
                top_suggestions = ", ".join(f"`{item}`" for item in suggestions[:3])
                next_question = f"Which item did you mean? For example: {top_suggestions}."
            else:
                available_items = _as_clean_str_list(next_entities.get("available_items"))
                if available_items:
                    top_available = ", ".join(f"`{item}`" for item in available_items[:3])
                    next_question = f"Which item should I use? For example: {top_available}."

        return {
            "entities": next_entities,
            "missing_fields": next_missing,
            "question": next_question,
        }

    def legacy_main_handoff_hints(
        self,
        *,
        registry: EntityRegistry,
        context_reference: dict[str, Any],
        runtime_context: dict[str, Any] | None = None,
        intent: str | None = None,
        route: str | None = None,
    ) -> dict[str, Any]:
        del runtime_context
        del intent
        del route
        list_name = _latest_entity_display_name(
            registry=registry,
            domain="lists",
            entity_type="list",
        )
        if not list_name:
            list_name = str(context_reference.get("last_list_name") or "").strip() or None
        if not list_name:
            return {}
        return {"last_list_name": list_name}


def _resolve_list_name_from_pending_context(
    *,
    text: str,
    current_entities: dict[str, Any],
) -> str | None:
    suggestions = _as_clean_str_list(current_entities.get("list_suggestions"))
    available = _as_clean_str_list(current_entities.get("available_lists"))
    if not suggestions and not available:
        return None

    lowered = re.sub(r"\s+", " ", text.strip().lower())
    affirmative = bool(
        re.match(
            r"^(?:yes|yeah|yep|yup|correct|right|exactly|affirmative|ok|okay|sure|that(?:'s| is)? right)\b",
            lowered,
            flags=re.IGNORECASE,
        )
    )
    candidate = _clean_list_followup_candidate(text)
    if not candidate:
        if affirmative and suggestions:
            return suggestions[0]
        return None

    matched_suggestion = _match_list_candidate(candidate=candidate, candidates=suggestions)
    if matched_suggestion is not None:
        return matched_suggestion

    matched_available = _match_list_candidate(candidate=candidate, candidates=available)
    if matched_available is not None:
        return matched_available

    if affirmative and suggestions:
        return suggestions[0]
    return None


def _resolve_item_from_pending_context(
    *,
    text: str,
    current_entities: dict[str, Any],
) -> str | None:
    suggestions = _as_clean_str_list(current_entities.get("item_suggestions"))
    available = _as_clean_str_list(current_entities.get("available_items"))
    if not suggestions and not available:
        return None

    lowered = re.sub(r"\s+", " ", text.strip().lower())
    affirmative = bool(
        re.match(
            r"^(?:yes|yeah|yep|yup|correct|right|exactly|affirmative|ok|okay|sure|that(?:'s| is)? right)\b",
            lowered,
            flags=re.IGNORECASE,
        )
    )
    candidate = _clean_list_followup_candidate(text)
    if not candidate:
        if affirmative and suggestions:
            return suggestions[0]
        return None

    matched_suggestion = _match_list_candidate(candidate=candidate, candidates=suggestions)
    if matched_suggestion is not None:
        return matched_suggestion

    matched_available = _match_list_candidate(candidate=candidate, candidates=available)
    if matched_available is not None:
        return matched_available

    if affirmative and suggestions:
        return suggestions[0]
    return None


def _resolve_completion_mode_from_pending_context(*, text: str) -> str | None:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.strip().lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None
    if re.search(r"\b(remove|delete|clear)\b", cleaned):
        return "remove"
    if re.search(r"\b(done|complete|completed|check(?:ed)?(?: off)?)\b", cleaned):
        return "done"
    return None


def _as_clean_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        cleaned.append(text)
    return cleaned


def _clean_list_followup_candidate(text: str) -> str:
    candidate = text.strip()
    candidate = re.sub(
        r"^(?:yes|yeah|yep|yup|correct|right|exactly|affirmative|ok|okay|sure|that(?:'s| is)? right)\b[\s,.:;-]*",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip()
    candidate = re.sub(
        r"^(?:show|get|display|open|check)(?:\s+me)?\s+",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip()
    candidate = re.sub(
        r"^(?:what(?:'s| is)\s+(?:on|in|up)\s+)",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip()
    candidate = re.sub(r"^(?:my|the|our)\s+", "", candidate, flags=re.IGNORECASE).strip()
    candidate = re.sub(r"\s+lists?$", "", candidate, flags=re.IGNORECASE).strip()
    return candidate


def _match_list_candidate(*, candidate: str, candidates: list[str]) -> str | None:
    if not candidate or not candidates:
        return None
    normalized_candidate = _normalize_list_label(candidate)
    if not normalized_candidate:
        return None

    best_name: str | None = None
    best_score = 0.0
    candidate_tokens = _normalized_list_tokens(normalized_candidate)
    for list_name in candidates:
        normalized_list_name = _normalize_list_label(list_name)
        if not normalized_list_name:
            continue
        if normalized_candidate == normalized_list_name:
            return list_name
        list_tokens = _normalized_list_tokens(normalized_list_name)
        if not candidate_tokens or not list_tokens:
            continue
        overlap = len(candidate_tokens & list_tokens)
        union = len(candidate_tokens | list_tokens)
        if union == 0:
            continue
        score = overlap / union
        if score > best_score:
            best_score = score
            best_name = list_name
    if best_score >= 0.6:
        return best_name
    return None


def _normalize_list_label(value: str) -> str:
    normalized = str(value or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9\s_-]+", "", normalized)
    normalized = normalized.replace("_", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"^(?:my|the|our|a|an)\s+", "", normalized)
    normalized = re.sub(r"\s+list$", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    list_aliases = {
        "grocery": "groceries",
        "shopping": "groceries",
        "shopping list": "groceries",
        "grocery list": "groceries",
        "todo": "to-do",
        "to do": "to-do",
    }
    if normalized in list_aliases:
        return list_aliases[normalized]
    return normalized


def _normalized_list_tokens(value: str) -> set[str]:
    parts = re.split(r"[\s-]+", value)
    tokens: set[str] = set()
    for part in parts:
        token = part.strip()
        if not token:
            continue
        if token.endswith("ies") and len(token) > 3:
            token = f"{token[:-3]}y"
        elif token.endswith("s") and len(token) > 3:
            token = token[:-1]
        tokens.add(token)
    return tokens


def _latest_entity_display_name(
    *,
    registry: EntityRegistry,
    domain: str,
    entity_type: str,
) -> str | None:
    candidates = [
        item
        for item in registry.entities
        if item.domain == domain and item.entity_type == entity_type and str(item.display_name or "").strip()
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            float(item.salience),
            _sort_timestamp(item.last_confirmed_at),
        ),
        reverse=True,
    )
    return str(candidates[0].display_name or "").strip() or None


def _sort_timestamp(value: str | None) -> float:
    if not isinstance(value, str) or not value.strip():
        return 0.0
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
