from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.context.serialization import deserialize_session_context, serialize_session_context
from app.context.types import RecentTurn
from app.core.session_store import SessionRecord


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RecentTurnUpdate:
    updated: bool
    appended_count: int
    pruned_count: int
    total_turns: int
    total_chars: int


class SessionContextManager:
    def __init__(
        self,
        *,
        max_recent_turns: int = 24,
        max_recent_chars: int = 6000,
        max_single_turn_chars: int = 1200,
    ) -> None:
        self._max_recent_turns = max(2, int(max_recent_turns))
        self._max_recent_chars = max(512, int(max_recent_chars))
        self._max_single_turn_chars = max(128, int(max_single_turn_chars))

    def record_exchange(
        self,
        *,
        session: SessionRecord,
        user_text: str,
        assistant_text: str,
        intent: str | None,
        route: str | None,
        skill_id: str | None,
        result_status: str | None = None,
    ) -> RecentTurnUpdate:
        user_clean = self._truncate_text(user_text)
        assistant_clean = self._truncate_text(assistant_text)
        if not user_clean and not assistant_clean:
            return RecentTurnUpdate(
                updated=False,
                appended_count=0,
                pruned_count=0,
                total_turns=0,
                total_chars=0,
            )

        state = deserialize_session_context(session.context_reference)
        counter = self._turn_counter(state.context_annotations)

        appended: list[RecentTurn] = []
        if user_clean:
            counter += 1
            appended.append(
                RecentTurn(
                    turn_id=f"turn-{counter}",
                    role="user",
                    text=user_clean,
                    normalized_text=self._normalize_text(user_clean),
                    intent=self._clean_optional(intent),
                    skill_id=self._clean_optional(skill_id),
                    timestamp=_utc_now(),
                    references={
                        "route": self._clean_optional(route),
                        "phase": "request",
                    },
                )
            )
        if assistant_clean:
            counter += 1
            assistant_references: dict[str, Any] = {
                "route": self._clean_optional(route),
                "phase": "response",
            }
            status = self._clean_optional(result_status)
            if status:
                assistant_references["status"] = status
            appended.append(
                RecentTurn(
                    turn_id=f"turn-{counter}",
                    role="assistant",
                    text=assistant_clean,
                    normalized_text=self._normalize_text(assistant_clean),
                    intent=self._clean_optional(intent),
                    skill_id=self._clean_optional(skill_id),
                    timestamp=_utc_now(),
                    references=assistant_references,
                )
            )

        turns = list(state.recent_turns)
        turns.extend(appended)
        pruned_count = 0
        turns, removed = self._prune_by_count(turns)
        pruned_count += removed
        turns, removed = self._prune_by_chars(turns)
        pruned_count += removed

        state.recent_turns = turns
        state.context_annotations["recent_turn_counter"] = counter

        serialized = serialize_session_context(state)
        merged = dict(session.context_reference)
        merged.update(serialized)
        changed = merged != session.context_reference
        if changed:
            session.context_reference = merged

        return RecentTurnUpdate(
            updated=changed,
            appended_count=len(appended),
            pruned_count=pruned_count,
            total_turns=len(turns),
            total_chars=self._turns_chars(turns),
        )

    def _prune_by_count(self, turns: list[RecentTurn]) -> tuple[list[RecentTurn], int]:
        if len(turns) <= self._max_recent_turns:
            return turns, 0
        removed = len(turns) - self._max_recent_turns
        return turns[-self._max_recent_turns :], removed

    def _prune_by_chars(self, turns: list[RecentTurn]) -> tuple[list[RecentTurn], int]:
        removed = 0
        working = list(turns)
        while len(working) > 1 and self._turns_chars(working) > self._max_recent_chars:
            working.pop(0)
            removed += 1
        return working, removed

    @staticmethod
    def _turn_counter(context_annotations: dict[str, Any]) -> int:
        raw = context_annotations.get("recent_turn_counter")
        if isinstance(raw, int):
            return max(0, raw)
        if isinstance(raw, float):
            return max(0, int(raw))
        if isinstance(raw, str):
            cleaned = raw.strip()
            if cleaned:
                try:
                    return max(0, int(cleaned))
                except ValueError:
                    return 0
        return 0

    @staticmethod
    def _turns_chars(turns: list[RecentTurn]) -> int:
        total = 0
        for turn in turns:
            total += len(turn.text)
            total += len(turn.normalized_text)
        return total

    @staticmethod
    def _normalize_text(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip().lower()
        return cleaned

    def _truncate_text(self, value: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        if not cleaned:
            return ""
        if len(cleaned) <= self._max_single_turn_chars:
            return cleaned
        return cleaned[: self._max_single_turn_chars]

    @staticmethod
    def _clean_optional(value: Any) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

