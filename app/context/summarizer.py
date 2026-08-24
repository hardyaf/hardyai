from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.context.serialization import deserialize_session_context, serialize_session_context
from app.context.types import PendingInteraction, RecentTurn, SessionSummary, TrackedEntity

if TYPE_CHECKING:
    from app.core.session_store import SessionRecord


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SessionSummaryUpdate:
    updated: bool
    trigger: str | None
    turn_counter: int
    recent_turns_count: int
    recent_chars: int
    summary_chars: int


class SessionSummaryManager:
    def __init__(
        self,
        *,
        update_every_turns: int = 6,
        budget_char_threshold: int = 5200,
        max_summary_chars: int = 900,
        max_items_per_facet: int = 6,
        max_text_item_chars: int = 120,
    ) -> None:
        self._update_every_turns = max(1, int(update_every_turns))
        self._budget_char_threshold = max(256, int(budget_char_threshold))
        self._max_summary_chars = max(128, int(max_summary_chars))
        self._max_items_per_facet = max(2, int(max_items_per_facet))
        self._max_text_item_chars = max(24, int(max_text_item_chars))

    def maybe_refresh(
        self,
        *,
        session: "SessionRecord",
        intent: str | None = None,
        route: str | None = None,
        result_status: str | None = None,
        force: bool = False,
    ) -> SessionSummaryUpdate:
        state = deserialize_session_context(session.context_reference)
        recent_turns = list(state.recent_turns)
        turn_counter = self._turn_counter(state.context_annotations, recent_turns)
        recent_chars = self._recent_chars(recent_turns)
        focus_key = self._focus_key(
            active_skill_id=state.active_skill_id,
            intent=intent,
            route=route,
        )
        status = self._clean_optional(result_status)
        trigger = self._choose_trigger(
            context_annotations=state.context_annotations,
            turn_counter=turn_counter,
            recent_chars=recent_chars,
            focus_key=focus_key,
            result_status=status,
            force=force,
        )
        if trigger is None:
            return SessionSummaryUpdate(
                updated=False,
                trigger=None,
                turn_counter=turn_counter,
                recent_turns_count=len(recent_turns),
                recent_chars=recent_chars,
                summary_chars=len(str(state.session_summary.summary_text or "")),
            )

        summary = self._build_summary(
            recent_turns=recent_turns,
            pending=state.pending_interaction,
            entities=state.entity_registry.entities,
            intent=intent,
            route=route,
        )
        state.session_summary = summary
        state.context_annotations["summary_last_turn_counter"] = turn_counter
        state.context_annotations["summary_last_trigger"] = trigger
        state.context_annotations["summary_last_updated_at"] = summary.last_updated_at
        if focus_key:
            state.context_annotations["summary_last_focus_key"] = focus_key

        serialized = serialize_session_context(state)
        merged = dict(session.context_reference)
        merged.update(serialized)
        changed = merged != session.context_reference
        if changed:
            session.context_reference = merged

        return SessionSummaryUpdate(
            updated=changed,
            trigger=trigger,
            turn_counter=turn_counter,
            recent_turns_count=len(recent_turns),
            recent_chars=recent_chars,
            summary_chars=len(summary.summary_text),
        )

    def _build_summary(
        self,
        *,
        recent_turns: list[RecentTurn],
        pending: PendingInteraction | None,
        entities: list[TrackedEntity],
        intent: str | None,
        route: str | None,
    ) -> SessionSummary:
        active_goals = self._active_goals(recent_turns=recent_turns, pending=pending, intent=intent)
        resolved_decisions = self._resolved_decisions(recent_turns=recent_turns)
        open_threads = self._open_threads(pending=pending)
        important_entities = self._important_entities(entities=entities)
        summary_text = self._compose_summary_text(
            active_goals=active_goals,
            resolved_decisions=resolved_decisions,
            open_threads=open_threads,
            important_entities=important_entities,
            route=route,
        )
        source_turn_range = self._source_turn_range(recent_turns=recent_turns)
        return SessionSummary(
            summary_text=summary_text,
            active_goals=active_goals,
            resolved_decisions=resolved_decisions,
            open_threads=open_threads,
            important_entities=important_entities,
            last_updated_at=_utc_now(),
            source_turn_range=source_turn_range,
        )

    def _active_goals(
        self,
        *,
        recent_turns: list[RecentTurn],
        pending: PendingInteraction | None,
        intent: str | None,
    ) -> list[str]:
        goals: list[str] = []
        if pending is not None:
            pending_intent = self._clean_optional(pending.intent)
            if pending_intent:
                goals.append(f"continue {pending_intent}")

        if intent:
            goals.append(f"current_intent:{intent}")

        latest_user = self._latest_turn_text(recent_turns=recent_turns, role="user")
        if latest_user:
            goals.append(f"latest_user:{self._truncate(latest_user, self._max_text_item_chars)}")

        return self._dedupe(values=goals, limit=self._max_items_per_facet)

    def _resolved_decisions(self, *, recent_turns: list[RecentTurn]) -> list[str]:
        resolved_statuses = {"ok", "partial", "executed", "cancelled", "completed"}
        values: list[str] = []
        for turn in reversed(recent_turns):
            if turn.role != "assistant":
                continue
            status = ""
            if isinstance(turn.references, dict):
                status = str(turn.references.get("status") or "").strip().lower()
            if status not in resolved_statuses:
                continue
            intent_value = str(turn.intent or "").strip().lower() or "unknown"
            values.append(f"{intent_value}:{status}")
            if len(values) >= self._max_items_per_facet:
                break
        return self._dedupe(values=values, limit=self._max_items_per_facet)

    def _open_threads(self, *, pending: PendingInteraction | None) -> list[str]:
        if pending is None:
            return []
        status = str(pending.status or "").strip().lower()
        if status in {"completed", "cancelled"}:
            return []
        pending_intent = self._clean_optional(pending.intent) or "unknown_intent"
        pending_kind = self._clean_optional(pending.kind) or "followup"
        missing = [str(item).strip() for item in pending.expected_fields if str(item).strip()]
        if missing:
            joined = ", ".join(missing[:2])
            return [f"{pending_intent}:{pending_kind}:awaiting({joined})"]
        return [f"{pending_intent}:{pending_kind}:pending"]

    def _important_entities(self, *, entities: list[TrackedEntity]) -> list[str]:
        ranked = sorted(
            [item for item in entities if str(item.display_name or "").strip()],
            key=lambda item: (
                float(item.salience),
                self._sort_timestamp(item.last_confirmed_at),
            ),
            reverse=True,
        )
        values: list[str] = []
        for entity in ranked:
            domain = str(entity.domain or "").strip().lower() or "unknown"
            display = str(entity.display_name or "").strip()
            values.append(f"{domain}:{self._truncate(display, self._max_text_item_chars)}")
            if len(values) >= self._max_items_per_facet:
                break
        return self._dedupe(values=values, limit=self._max_items_per_facet)

    def _compose_summary_text(
        self,
        *,
        active_goals: list[str],
        resolved_decisions: list[str],
        open_threads: list[str],
        important_entities: list[str],
        route: str | None,
    ) -> str:
        parts: list[str] = []
        route_value = self._clean_optional(route)
        if route_value:
            parts.append(f"route={route_value}")
        if active_goals:
            parts.append(f"goals={', '.join(active_goals[:2])}")
        if resolved_decisions:
            parts.append(f"resolved={', '.join(resolved_decisions[:2])}")
        if open_threads:
            parts.append(f"open={', '.join(open_threads[:2])}")
        if important_entities:
            parts.append(f"entities={', '.join(important_entities[:3])}")
        if not parts:
            parts.append("no_context_highlights")
        return self._truncate(" | ".join(parts), self._max_summary_chars)

    @staticmethod
    def _source_turn_range(*, recent_turns: list[RecentTurn]) -> list[int]:
        indexes: list[int] = []
        for turn in recent_turns:
            if not isinstance(turn.turn_id, str):
                continue
            if not turn.turn_id.startswith("turn-"):
                continue
            try:
                indexes.append(int(turn.turn_id.split("-", 1)[1]))
            except ValueError:
                continue
        if not indexes:
            return []
        low = min(indexes)
        high = max(indexes)
        if low == high:
            return [low]
        return [low, high]

    def _choose_trigger(
        self,
        *,
        context_annotations: dict[str, Any],
        turn_counter: int,
        recent_chars: int,
        focus_key: str | None,
        result_status: str | None,
        force: bool,
    ) -> str | None:
        if force:
            return "forced"

        if self._is_task_completion(result_status):
            return "task_completed"

        if self._is_focus_changed(context_annotations=context_annotations, focus_key=focus_key):
            return "focus_changed"

        if recent_chars >= self._budget_char_threshold:
            return "budget_threshold"

        last_turn_counter = self._as_int(context_annotations.get("summary_last_turn_counter"))
        if turn_counter <= 0:
            return None
        if last_turn_counter <= 0:
            if turn_counter >= self._update_every_turns:
                return "turn_interval"
            return None
        if (turn_counter - last_turn_counter) >= self._update_every_turns:
            return "turn_interval"
        return None

    @staticmethod
    def _is_task_completion(result_status: str | None) -> bool:
        normalized = str(result_status or "").strip().lower()
        return normalized in {"ok", "partial", "executed", "cancelled", "completed"}

    @staticmethod
    def _is_focus_changed(*, context_annotations: dict[str, Any], focus_key: str | None) -> bool:
        if not focus_key:
            return False
        previous = str(context_annotations.get("summary_last_focus_key") or "").strip().lower()
        if not previous:
            return False
        return previous != focus_key

    @staticmethod
    def _focus_key(*, active_skill_id: str | None, intent: str | None, route: str | None) -> str | None:
        skill = str(active_skill_id or "").strip().lower()
        if skill:
            return skill
        intent_value = str(intent or "").strip().lower()
        route_value = str(route or "").strip().lower()
        if intent_value and route_value:
            return f"{intent_value}@{route_value}"
        if intent_value:
            return intent_value
        return None

    @staticmethod
    def _turn_counter(context_annotations: dict[str, Any], recent_turns: list[RecentTurn]) -> int:
        annotated = SessionSummaryManager._as_int(context_annotations.get("recent_turn_counter"))
        if annotated > 0:
            return annotated
        fallback = 0
        for turn in recent_turns:
            if not isinstance(turn.turn_id, str):
                continue
            if not turn.turn_id.startswith("turn-"):
                continue
            try:
                fallback = max(fallback, int(turn.turn_id.split("-", 1)[1]))
            except ValueError:
                continue
        return fallback

    @staticmethod
    def _recent_chars(recent_turns: list[RecentTurn]) -> int:
        total = 0
        for turn in recent_turns:
            total += len(str(turn.text or ""))
            total += len(str(turn.normalized_text or ""))
        return total

    @staticmethod
    def _latest_turn_text(*, recent_turns: list[RecentTurn], role: str) -> str | None:
        for turn in reversed(recent_turns):
            if turn.role != role:
                continue
            text = str(turn.text or "").strip()
            if text:
                return text
        return None

    @staticmethod
    def _dedupe(*, values: list[str], limit: int) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in values:
            cleaned = str(item).strip()
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if lowered in seen:
                continue
            ordered.append(cleaned)
            seen.add(lowered)
            if len(ordered) >= limit:
                break
        return ordered

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        cleaned = str(value or "").strip()
        if len(cleaned) <= limit:
            return cleaned
        return f"{cleaned[: max(0, limit - 3)]}..."

    @staticmethod
    def _clean_optional(value: Any) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @staticmethod
    def _as_int(value: Any) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                try:
                    return int(cleaned)
                except ValueError:
                    return 0
        return 0

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
