from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContextBudgetSnapshot:
    max_chars: int
    used_chars: int
    remaining_chars: int
    max_tokens_estimate: int
    used_tokens_estimate: int
    remaining_tokens_estimate: int
    trimmed: bool
    strategy: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_chars": self.max_chars,
            "used_chars": self.used_chars,
            "remaining_chars": self.remaining_chars,
            "max_tokens_estimate": self.max_tokens_estimate,
            "used_tokens_estimate": self.used_tokens_estimate,
            "remaining_tokens_estimate": self.remaining_tokens_estimate,
            "trimmed": self.trimmed,
            "strategy": self.strategy,
        }


class ContextBudget:
    """Lightweight v1 scaffold for future prompt/context budgeting."""

    def __init__(self, max_chars: int = 2400, strategy: str = "truncate_tail") -> None:
        self._max_chars = max(256, int(max_chars))
        self._strategy = strategy
        self._max_tokens_estimate = max(64, self._max_chars // 4)

    def snapshot(
        self,
        *,
        goal_text: str,
        context: dict[str, Any] | None = None,
        supplemental_sections: list[str] | None = None,
    ) -> ContextBudgetSnapshot:
        serialized_context = self._serialize_context(context or {})
        supplement = "\n".join(item for item in (supplemental_sections or []) if item)
        combined = f"{goal_text}\n{serialized_context}\n{supplement}".strip()
        used_chars = len(combined)
        trimmed = used_chars > self._max_chars
        remaining = max(0, self._max_chars - min(used_chars, self._max_chars))
        used_tokens = self._estimate_tokens(used_chars)
        remaining_tokens = max(0, self._max_tokens_estimate - min(used_tokens, self._max_tokens_estimate))
        return ContextBudgetSnapshot(
            max_chars=self._max_chars,
            used_chars=used_chars,
            remaining_chars=remaining,
            max_tokens_estimate=self._max_tokens_estimate,
            used_tokens_estimate=used_tokens,
            remaining_tokens_estimate=remaining_tokens,
            trimmed=trimmed,
            strategy=self._strategy,
        )

    @staticmethod
    def _serialize_context(context: dict[str, Any]) -> str:
        if not context:
            return "{}"
        try:
            return json.dumps(context, sort_keys=True, ensure_ascii=True)
        except TypeError:
            safe_context = {str(key): str(value) for key, value in context.items()}
            return json.dumps(safe_context, sort_keys=True, ensure_ascii=True)

    @staticmethod
    def _estimate_tokens(char_count: int) -> int:
        return max(0, (int(char_count) + 3) // 4)
