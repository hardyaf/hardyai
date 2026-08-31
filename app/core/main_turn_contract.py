from __future__ import annotations

from typing import Any

from app.core.tool_loop_types import MainActionCommitment, ToolLoopContractError
from app.core.types import MAIN_ACTION_INTENTS


MAIN_TURN_MODES = {
    "conversation",
    "clarify_action",
    "execute_action",
}
MAIN_TURN_ACTION_INTENTS = {intent.value for intent in MAIN_ACTION_INTENTS}


def normalize_main_turn_decision(
    raw: dict[str, Any] | None,
    *,
    execution_mode: str = "off",
) -> dict[str, Any] | None:
    """Validate Main's conversational/action commitment boundary."""

    if not isinstance(raw, dict):
        return None

    mode_setting = str(execution_mode or "off").strip().casefold()
    if mode_setting in {"shadow", "active"}:
        try:
            return MainActionCommitment.from_mapping(raw).to_dict()
        except ToolLoopContractError:
            return None
    if mode_setting != "off":
        return None

    mode = str(raw.get("mode") or "").strip().lower()
    if mode not in MAIN_TURN_MODES:
        return None

    confidence = _coerce_confidence(raw.get("confidence"))
    if confidence is None:
        return None

    reasoning = str(raw.get("reasoning") or "").strip()
    if not reasoning:
        return None

    source = _optional_text(raw.get("source")) or "backend"
    message = _optional_text(raw.get("message") or raw.get("reply"))
    question = _optional_text(raw.get("question"))

    if mode == "conversation":
        if message is None:
            return None
        return {
            "mode": mode,
            "intent": None,
            "confidence": confidence,
            "reasoning": reasoning,
            "entities": {},
            "missing_fields": [],
            "message": message,
            "question": None,
            "source": source,
        }

    intent = str(raw.get("intent") or "").strip().lower()
    if intent not in MAIN_TURN_ACTION_INTENTS:
        return None

    entities = raw.get("entities")
    if not isinstance(entities, dict):
        entities = {}
    missing_fields = _string_list(raw.get("missing_fields"))
    if missing_fields is None:
        return None

    if mode == "clarify_action":
        if not missing_fields or question is None:
            return None
        message = message or "I need one detail before I can do that."
    else:
        if missing_fields:
            return None
        question = None
        message = message or ""

    return {
        "mode": mode,
        "intent": intent,
        "confidence": confidence,
        "reasoning": reasoning,
        "entities": dict(entities),
        "missing_fields": missing_fields,
        "message": message,
        "question": question,
        "source": source,
    }


def _coerce_confidence(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 1.0))
    try:
        return max(0.0, min(float(str(value).strip()), 1.0))
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _string_list(value: Any) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        cleaned = str(item or "").strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(cleaned)
    return normalized
