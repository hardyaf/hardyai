from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.context.serialization import deserialize_session_context
from app.context.types import RecentTurn, TrackedEntity, WorkingContextPacket

if TYPE_CHECKING:
    from app.core.session_store import SessionRecord


class ContextBuilder:
    def __init__(
        self,
        *,
        max_recent_turns: int = 10,
        max_entity_hints: int = 8,
        max_memory_entries: int = 6,
        max_text_chars: int = 220,
    ) -> None:
        self._max_recent_turns = max(2, int(max_recent_turns))
        self._max_entity_hints = max(2, int(max_entity_hints))
        self._max_memory_entries = max(1, int(max_memory_entries))
        self._max_text_chars = max(64, int(max_text_chars))

    def build_packet(
        self,
        *,
        session: "SessionRecord",
        relevant_memory: list[dict[str, Any]] | None = None,
        active_skill_context: dict[str, Any] | None = None,
        channel_runtime: dict[str, Any] | None = None,
        budget_metadata: dict[str, Any] | None = None,
    ) -> WorkingContextPacket:
        state = deserialize_session_context(session.context_reference)
        recent_turns = self._bounded_recent_turns(state.recent_turns)
        entity_hints = self._bounded_entity_hints(state.entity_registry.entities)
        memory_rows = self._bounded_memory(relevant_memory or [])
        state.recent_turns = recent_turns
        state.entity_registry.entities = entity_hints
        state.entity_registry.alias_map = self._alias_map_for_entities(entity_hints)

        runtime = dict(channel_runtime or {})
        if not runtime and isinstance(state.channel_runtime, dict):
            runtime = dict(state.channel_runtime)
        if not runtime:
            legacy_channel = session.context_reference.get("channel_session")
            if isinstance(legacy_channel, dict):
                runtime = dict(legacy_channel)

        return WorkingContextPacket(
            session_state=state,
            pending_interaction=state.pending_interaction,
            recent_turns=recent_turns,
            session_summary=state.session_summary,
            relevant_memory=memory_rows,
            entity_hints=entity_hints,
            active_skill_context=dict(active_skill_context or {}),
            channel_runtime=runtime,
            budget_metadata=dict(budget_metadata or {}),
        )

    def _bounded_recent_turns(self, turns: list[RecentTurn]) -> list[RecentTurn]:
        trimmed: list[RecentTurn] = []
        for turn in turns[-self._max_recent_turns :]:
            trimmed.append(
                RecentTurn(
                    turn_id=turn.turn_id,
                    role=turn.role,
                    text=self._truncate(str(turn.text or "")),
                    normalized_text=self._truncate(str(turn.normalized_text or "")),
                    intent=turn.intent,
                    skill_id=turn.skill_id,
                    timestamp=turn.timestamp,
                    references=dict(turn.references or {}),
                )
            )
        return trimmed

    def _bounded_entity_hints(self, entities: list[TrackedEntity]) -> list[TrackedEntity]:
        ranked = sorted(
            [item for item in entities if str(item.display_name or "").strip()],
            key=lambda item: (
                float(item.salience),
                self._sort_timestamp(item.last_confirmed_at),
            ),
            reverse=True,
        )
        hints: list[TrackedEntity] = []
        for entity in ranked[: self._max_entity_hints]:
            hints.append(
                TrackedEntity(
                    domain=str(entity.domain or "").strip().lower(),
                    entity_type=str(entity.entity_type or "").strip().lower(),
                    entity_id=entity.entity_id,
                    display_name=self._truncate(str(entity.display_name or "")),
                    aliases=[self._truncate(str(alias or "")) for alias in entity.aliases if str(alias or "").strip()],
                    salience=float(entity.salience),
                    last_confirmed_at=entity.last_confirmed_at,
                    resolution_hints=dict(entity.resolution_hints or {}),
                )
            )
        return hints

    def _bounded_memory(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        bounded: list[dict[str, Any]] = []
        for row in rows[-self._max_memory_entries :]:
            if not isinstance(row, dict):
                continue
            bounded.append(
                {
                    "timestamp": row.get("timestamp"),
                    "session_id": row.get("session_id"),
                    "user_id": row.get("user_id"),
                    "intent": row.get("intent"),
                    "route": row.get("route"),
                    "request_text": self._truncate(str(row.get("request_text") or "")),
                    "response_summary": self._truncate(str(row.get("response_summary") or "")),
                    "metadata": dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), dict) else {},
                }
            )
        return bounded

    @staticmethod
    def _alias_map_for_entities(entities: list[TrackedEntity]) -> dict[str, str]:
        alias_map: dict[str, str] = {}
        for entity in entities:
            display = str(entity.display_name or "").strip()
            if not display:
                continue
            for alias in entity.aliases:
                cleaned = str(alias or "").strip().lower()
                if cleaned:
                    alias_map[cleaned] = display
        return alias_map

    def _truncate(self, value: str) -> str:
        cleaned = " ".join(str(value or "").split())
        if len(cleaned) <= self._max_text_chars:
            return cleaned
        return f"{cleaned[: max(0, self._max_text_chars - 3)]}..."

    @staticmethod
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
