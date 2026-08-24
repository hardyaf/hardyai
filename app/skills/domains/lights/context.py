from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

from app.context.reference_resolver import ReferenceResolver
from app.context.types import EntityRegistry
from app.core.types import SessionOwner


def _pick_first_text(container: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = container.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


LIGHT_INTENTS = {"home.set_switch"}


def emit_context_entities(*, intent: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    if intent not in LIGHT_INTENTS:
        return []
    status = str(result.get("status") or "").strip().lower()
    if status not in {"ok", "partial"}:
        return []
    switch_name = str(result.get("switch_name") or "").strip()
    if not switch_name:
        return []
    aliases = _aliases_for_switch_name(switch_name)
    return [
        {
            "domain": "home",
            "entity_type": "switch",
            "display_name": switch_name,
            "aliases": aliases,
            "salience": 0.9,
            "resolution_hints": {
                "intent": intent,
                "status": status,
                "action": str(result.get("action") or "").strip().lower() or None,
            },
        }
    ]


def resolve_deictic_switch_name(
    *,
    switch_name: str,
    resolver: ReferenceResolver,
    registry: EntityRegistry,
) -> str | None:
    resolved = resolver.resolve_reference(
        value=switch_name,
        registry=registry,
        domain="home",
        entity_type="switch",
        deictic_only=True,
    )
    if resolved is None:
        return None
    return str(resolved.entity.display_name or "").strip() or None


def _aliases_for_switch_name(switch_name: str) -> list[str]:
    base = re.sub(r"\s+", " ", switch_name.strip().lower()).strip()
    aliases = {base}
    aliases.add(re.sub(r"^(?:the|my|our)\s+", "", base).strip())
    if base.endswith(" light"):
        aliases.add(base[: -len(" light")].strip())
    if base.endswith(" lamp"):
        aliases.add(base[: -len(" lamp")].strip())
    return sorted(item for item in aliases if item)


class LightsContextContract:
    contract_id = "lights"
    _FOLLOWUP_INTENTS = {"home.set_switch"}

    def supports_intent(self, *, intent: str) -> bool:
        return str(intent or "").strip().lower() in LIGHT_INTENTS

    def normalize_entities(self, *, intent: str, entities: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(entities)
        if str(intent or "").strip().lower() != "home.set_switch":
            return normalized
        switch_name = _pick_first_text(normalized, ["switch_name", "switch", "device", "light"])
        action = _pick_first_text(normalized, ["action", "state"])
        if switch_name:
            normalized["switch_name"] = switch_name
        if action:
            normalized["action"] = action
        return normalized

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
        switch_name_raw = entities.get("switch_name")
        switch_name = str(switch_name_raw).strip() if switch_name_raw is not None else ""
        resolved_switch_name = resolve_deictic_switch_name(
            switch_name=switch_name,
            resolver=resolver,
            registry=registry,
        )
        if not resolved_switch_name:
            return decision

        entities["switch_name"] = resolved_switch_name
        decision.entities = entities
        if "switch_reference_resolved_from_context" not in decision.ambiguity_flags:
            decision.ambiguity_flags.append("switch_reference_resolved_from_context")
        if getattr(decision, "reasoning", ""):
            decision.reasoning = f"{decision.reasoning}_with_switch_context"
        else:
            decision.reasoning = "switch_context_resolution"

        try:
            current_intent = getattr(decision, "intent")
            missing = required_fields_for_intent(current_intent, decision.entities)
        except Exception:
            missing = []
        decision.confidence = max(float(getattr(decision, "confidence", 0.0)), 0.88)
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
        intent_value = str(intent or "").strip().lower()
        if intent_value != "home.set_switch":
            return None
        missing: list[str] = []
        for field_name in ["switch_name", "action"]:
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
        if str(intent or "").strip().lower() != "home.set_switch":
            return None
        cleaned = str(field_name or "").strip()
        if cleaned == "switch_name":
            return "Which switch should I control?"
        if cleaned == "action":
            return "Should I turn it on or off?"
        return None

    def continue_pending_interaction(
        self,
        *,
        intent: str,
        text: str,
        missing_fields: list[str],
        current_entities: dict[str, Any],
    ) -> dict[str, Any]:
        if str(intent or "").strip().lower() != "home.set_switch":
            return {}
        if "switch_name" not in {str(item).strip() for item in missing_fields}:
            return {}

        normalized_text = re.sub(r"\s+", " ", str(text or "").strip().lower()).replace("’", "'")
        if not normalized_text:
            return {}
        bulk_reply = normalized_text.strip(" .!?")
        if bulk_reply in {"all lights", "all of them", "all of the lights", "every light"}:
            return {"switch_name": "all lights", "scope": "all"}

        candidate_text = re.sub(
            r"\bnot\s+(?:all lights|all of them|all of the lights|every light)\b[,\s]*",
            " ",
            normalized_text,
        ).strip()
        if re.search(
            r"\b(?:not|never|except|without|avoid|no)\b|"
            r"\b(?:anything\s+but|other\s+than|don't|dont|do\s+not)\b",
            candidate_text,
        ):
            return {}
        explicit_actions = set(re.findall(r"\b(on|off)\b", candidate_text))
        current_action = str(current_entities.get("action") or "").strip().lower()
        if len(explicit_actions) > 1 or (
            explicit_actions
            and current_action in {"on", "off"}
            and current_action not in explicit_actions
        ):
            return {}

        available: list[str] = []
        for key in ("switch_suggestions", "available_switches"):
            values = current_entities.get(key)
            if not isinstance(values, list):
                continue
            for item in values:
                raw_name = item.get("name") if isinstance(item, dict) else item
                name = str(raw_name or "").strip()
                if name and name.lower() not in {candidate.lower() for candidate in available}:
                    available.append(name)
        if not available:
            return {}

        text_tokens = set(re.findall(r"[a-z0-9]+", candidate_text))
        generic_tokens = {
            "called",
            "have",
            "it",
            "lamp",
            "light",
            "lights",
            "one",
            "switch",
            "test",
            "the",
            "think",
            "you",
        }
        matched_names: list[str] = []
        for name in available:
            normalized_name = re.sub(r"\s+", " ", name.strip().lower())
            if normalized_name and re.search(rf"\b{re.escape(normalized_name)}\b", candidate_text):
                matched_names.append(name)
                continue
            name_tokens = set(re.findall(r"[a-z0-9]+", normalized_name)) - generic_tokens
            overlap = text_tokens & name_tokens
            if overlap:
                matched_names.append(name)

        if len(matched_names) != 1:
            return {}
        return {"switch_name": matched_names[0]}

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
        del registry
        if str(intent or "").strip().lower() not in LIGHT_INTENTS:
            return {}

        next_entities = dict(entities)
        next_missing = [str(item).strip() for item in missing_fields if str(item).strip()]
        next_question = str(question).strip() if isinstance(question, str) and str(question).strip() else None
        if str(status or "").strip().lower() != "unknown_switch":
            return {
                "entities": next_entities,
                "missing_fields": next_missing,
                "question": next_question,
            }

        if "switch_name" not in next_missing:
            next_missing.append("switch_name")
        available_switches = [
            str(item).strip()
            for item in tool_result.get("available_switches", [])
            if str(item).strip()
        ]
        if available_switches:
            next_entities["available_switches"] = available_switches
        suggestions = [
            str(item).strip()
            for item in tool_result.get("suggestions", [])
            if str(item).strip()
        ]
        if suggestions:
            next_entities["switch_suggestions"] = suggestions

        if next_question is None and "switch_name" in next_missing:
            if suggestions:
                top_suggestions = ", ".join(f"`{item}`" for item in suggestions[:3])
                next_question = f"Did you mean {top_suggestions}?"
            elif available_switches:
                top_available = ", ".join(f"`{item}`" for item in available_switches[:3])
                next_question = f"Which switch should I use? For example: {top_available}."

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
        del intent
        del route
        runtime = runtime_context if isinstance(runtime_context, dict) else {}
        hints: dict[str, Any] = {}
        switch_name = _latest_entity_display_name(
            registry=registry,
            domain="home",
            entity_type="switch",
        )
        if not switch_name:
            switch_name = str(context_reference.get("last_switch_name") or "").strip() or None
        if switch_name:
            hints["last_switch_name"] = switch_name

        available_switches = runtime.get("available_switches")
        if isinstance(available_switches, list):
            cleaned_switches: list[dict[str, Any] | str] = []
            for item in available_switches:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                    if not name:
                        continue
                    cleaned_switches.append(item)
                    continue
                text = str(item).strip()
                if text:
                    cleaned_switches.append(text)
            if cleaned_switches:
                hints["available_switches"] = cleaned_switches
        return hints


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
