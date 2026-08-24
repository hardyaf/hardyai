from __future__ import annotations

from typing import Any

from app.context.types import (
    CURRENT_SESSION_CONTEXT_VERSION,
    EntityRegistry,
    PendingInteraction,
    RecentTurn,
    SessionContextState,
    SessionSummary,
    TrackedEntity,
)


def deserialize_session_context(payload: dict[str, Any] | None) -> SessionContextState:
    raw = payload if isinstance(payload, dict) else {}

    state = SessionContextState(
        version=_as_int(raw.get("context_version"), default=CURRENT_SESSION_CONTEXT_VERSION),
        active_agent_id=_as_non_empty_str(raw.get("active_agent_id"), default="jarvis"),
        active_skill_id=_as_optional_str(raw.get("active_skill_id")),
        recent_turns=_parse_recent_turns(raw.get("recent_turns")),
        pending_interaction=_parse_pending_interaction(raw.get("pending_interaction")),
        session_summary=_parse_session_summary(raw.get("session_summary")),
        entity_registry=_parse_entity_registry(raw.get("entity_registry")),
        focus_stack=_parse_str_list(raw.get("focus_stack")),
        context_annotations=_parse_dict(raw.get("context_annotations")),
        channel_runtime=_parse_dict(raw.get("channel_runtime")),
        main_agent_token_session=_parse_dict(raw.get("main_agent_token_session")),
    )

    _apply_legacy_adapters(state=state, raw=raw)
    if state.version <= 0:
        state.version = CURRENT_SESSION_CONTEXT_VERSION
    return state


def serialize_session_context(state: SessionContextState) -> dict[str, Any]:
    serialized = state.to_dict()
    serialized["context_version"] = max(1, int(serialized.get("context_version") or CURRENT_SESSION_CONTEXT_VERSION))
    return serialized


def session_context_to_legacy_compat_dict(state: SessionContextState) -> dict[str, Any]:
    compat: dict[str, Any] = {}
    compat.update(serialize_session_context(state))

    if state.pending_interaction is not None:
        compat["pending_clarification"] = {
            "intent": state.pending_interaction.intent,
            "entities": dict(state.pending_interaction.proposed_action.get("entities", {}))
            if isinstance(state.pending_interaction.proposed_action, dict)
            else {},
            "missing_fields": list(state.pending_interaction.expected_fields),
            "question": state.pending_interaction.question,
        }

    legacy_list = _best_entity_name(state=state, domain="lists", entity_type="list")
    legacy_switch = _best_entity_name(state=state, domain="home", entity_type="switch")
    legacy_calendar_person = _best_entity_name(state=state, domain="calendar", entity_type="person")
    if legacy_list:
        compat["last_list_name"] = legacy_list
    if legacy_switch:
        compat["last_switch_name"] = legacy_switch
    if legacy_calendar_person:
        compat["last_calendar_person"] = legacy_calendar_person

    sticky = state.context_annotations.get("main_sticky_followup")
    if isinstance(sticky, dict):
        turns_remaining = _as_int(sticky.get("turns_remaining"), default=0)
        if turns_remaining > 0:
            compat["main_sticky_followup_turns_remaining"] = turns_remaining
            reason = _as_optional_str(sticky.get("reason"))
            if reason:
                compat["main_sticky_followup_reason"] = reason

    if state.main_agent_token_session:
        compat["main_agent_token_session"] = dict(state.main_agent_token_session)
    if state.channel_runtime:
        compat["channel_session"] = dict(state.channel_runtime)

    return compat


def _apply_legacy_adapters(*, state: SessionContextState, raw: dict[str, Any]) -> None:
    if state.pending_interaction is None:
        pending = raw.get("pending_clarification")
        if isinstance(pending, dict):
            state.pending_interaction = PendingInteraction(
                kind="missing_field",
                intent=_as_optional_str(pending.get("intent")),
                status="pending",
                question=_as_optional_str(pending.get("question")),
                expected_fields=_parse_str_list(pending.get("missing_fields")),
                proposed_action={
                    "entities": _parse_dict(pending.get("entities")),
                },
                metadata={"legacy_source": "pending_clarification"},
            )

    _ensure_legacy_entity(
        state=state,
        raw_value=raw.get("last_list_name"),
        domain="lists",
        entity_type="list",
        legacy_key="last_list_name",
    )
    _ensure_legacy_entity(
        state=state,
        raw_value=raw.get("last_switch_name"),
        domain="home",
        entity_type="switch",
        legacy_key="last_switch_name",
    )
    _ensure_legacy_entity(
        state=state,
        raw_value=raw.get("last_calendar_person"),
        domain="calendar",
        entity_type="person",
        legacy_key="last_calendar_person",
    )

    turns_remaining = _as_int(raw.get("main_sticky_followup_turns_remaining"), default=0)
    if turns_remaining > 0 and "main_sticky_followup" not in state.context_annotations:
        state.context_annotations["main_sticky_followup"] = {
            "turns_remaining": turns_remaining,
            "reason": _as_optional_str(raw.get("main_sticky_followup_reason")) or "clarification",
            "legacy_source": "main_sticky_followup_turns_remaining",
        }

    if not state.main_agent_token_session:
        token_session = raw.get("main_agent_token_session")
        if isinstance(token_session, dict):
            state.main_agent_token_session = dict(token_session)

    if not state.channel_runtime:
        channel_runtime = raw.get("channel_session")
        if isinstance(channel_runtime, dict):
            state.channel_runtime = dict(channel_runtime)


def _ensure_legacy_entity(
    *,
    state: SessionContextState,
    raw_value: Any,
    domain: str,
    entity_type: str,
    legacy_key: str,
) -> None:
    display_name = _as_optional_str(raw_value)
    if not display_name:
        return
    for entity in state.entity_registry.entities:
        if entity.domain == domain and entity.entity_type == entity_type and entity.display_name.lower() == display_name.lower():
            return
    state.entity_registry.entities.append(
        TrackedEntity(
            domain=domain,
            entity_type=entity_type,
            display_name=display_name,
            aliases=[display_name.lower()],
            salience=0.8,
            resolution_hints={
                "legacy_key": legacy_key,
            },
        )
    )


def _best_entity_name(*, state: SessionContextState, domain: str, entity_type: str) -> str | None:
    best: TrackedEntity | None = None
    for entity in state.entity_registry.entities:
        if entity.domain != domain or entity.entity_type != entity_type:
            continue
        if not entity.display_name.strip():
            continue
        if best is None or entity.salience > best.salience:
            best = entity
    if best is None:
        return None
    return best.display_name.strip() or None


def _parse_recent_turns(raw: Any) -> list[RecentTurn]:
    if not isinstance(raw, list):
        return []
    turns: list[RecentTurn] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        turns.append(
            RecentTurn(
                turn_id=_as_optional_str(item.get("turn_id")),
                role=_as_non_empty_str(item.get("role"), default=""),
                text=_as_non_empty_str(item.get("text"), default=""),
                normalized_text=_as_non_empty_str(item.get("normalized_text"), default=""),
                intent=_as_optional_str(item.get("intent")),
                skill_id=_as_optional_str(item.get("skill_id")),
                timestamp=_as_optional_str(item.get("timestamp")),
                references=_parse_dict(item.get("references")),
            )
        )
    return turns


def _parse_pending_interaction(raw: Any) -> PendingInteraction | None:
    if not isinstance(raw, dict):
        return None
    return PendingInteraction(
        kind=_as_non_empty_str(raw.get("kind"), default="clarification"),
        intent=_as_optional_str(raw.get("intent")),
        skill_id=_as_optional_str(raw.get("skill_id")),
        status=_as_non_empty_str(raw.get("status"), default="pending"),
        question=_as_optional_str(raw.get("question")),
        expected_fields=_parse_str_list(raw.get("expected_fields")),
        candidate_entities=_parse_dict_list(raw.get("candidate_entities")),
        proposed_action=_parse_dict(raw.get("proposed_action")),
        created_at=_as_optional_str(raw.get("created_at")),
        expires_at=_as_optional_str(raw.get("expires_at")),
        origin_turn_id=_as_optional_str(raw.get("origin_turn_id")),
        metadata=_parse_dict(raw.get("metadata")),
    )


def _parse_session_summary(raw: Any) -> SessionSummary:
    if not isinstance(raw, dict):
        return SessionSummary()
    source_turn_range = []
    for item in raw.get("source_turn_range", []):
        if isinstance(item, int):
            source_turn_range.append(item)
        elif isinstance(item, float):
            source_turn_range.append(int(item))
    return SessionSummary(
        summary_text=_as_non_empty_str(raw.get("summary_text"), default=""),
        active_goals=_parse_str_list(raw.get("active_goals")),
        resolved_decisions=_parse_str_list(raw.get("resolved_decisions")),
        open_threads=_parse_str_list(raw.get("open_threads")),
        important_entities=_parse_str_list(raw.get("important_entities")),
        last_updated_at=_as_optional_str(raw.get("last_updated_at")),
        source_turn_range=source_turn_range,
    )


def _parse_entity_registry(raw: Any) -> EntityRegistry:
    if not isinstance(raw, dict):
        return EntityRegistry()
    entities: list[TrackedEntity] = []
    entities_raw = raw.get("entities")
    if isinstance(entities_raw, list):
        for item in entities_raw:
            if not isinstance(item, dict):
                continue
            entities.append(
                TrackedEntity(
                    domain=_as_non_empty_str(item.get("domain"), default=""),
                    entity_type=_as_non_empty_str(item.get("entity_type"), default=""),
                    entity_id=_as_optional_str(item.get("entity_id")),
                    display_name=_as_non_empty_str(item.get("display_name"), default=""),
                    aliases=_parse_str_list(item.get("aliases")),
                    salience=_as_float(item.get("salience"), default=0.0),
                    last_confirmed_at=_as_optional_str(item.get("last_confirmed_at")),
                    resolution_hints=_parse_dict(item.get("resolution_hints")),
                )
            )
    alias_map = _parse_dict(raw.get("alias_map"))
    normalized_alias_map: dict[str, str] = {}
    for key, value in alias_map.items():
        key_str = _as_optional_str(key)
        value_str = _as_optional_str(value)
        if key_str and value_str:
            normalized_alias_map[key_str] = value_str
    return EntityRegistry(
        entities=entities,
        alias_map=normalized_alias_map,
    )


def _parse_str_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    for item in raw:
        value = _as_optional_str(item)
        if value:
            values.append(value)
    return values


def _parse_dict_list(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            rows.append(dict(item))
    return rows


def _parse_dict(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def _as_non_empty_str(raw: Any, *, default: str) -> str:
    if isinstance(raw, str):
        cleaned = raw.strip()
        if cleaned:
            return cleaned
    return default


def _as_optional_str(raw: Any) -> str | None:
    if isinstance(raw, str):
        cleaned = raw.strip()
        if cleaned:
            return cleaned
    return None


def _as_int(raw: Any, *, default: int) -> int:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        cleaned = raw.strip()
        if cleaned:
            try:
                return int(cleaned)
            except ValueError:
                return default
    return default


def _as_float(raw: Any, *, default: float) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        cleaned = raw.strip()
        if cleaned:
            try:
                return float(cleaned)
            except ValueError:
                return default
    return default

