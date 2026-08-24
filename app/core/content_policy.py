from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContentPolicyVerdict:
    allowed: bool
    status: str
    reason: str
    scope: str
    matched_patterns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "status": self.status,
            "reason": self.reason,
            "scope": self.scope,
            "matched_patterns": list(self.matched_patterns),
        }


class MainAgentContentPolicyGate:
    """Scaffold gate for future kid/content policy enforcement."""

    _DEFAULT_BLOCK_PATTERNS = [
        r"\bkill\b",
        r"\bweapon(s)?\b",
        r"\bdrugs?\b",
        r"\bexplicit\b",
        r"\bporn\b",
    ]

    def __init__(
        self,
        *,
        enabled: bool = True,
        enforce_for_children_only: bool = True,
        blocked_patterns: list[str] | None = None,
    ) -> None:
        self._enabled = enabled
        self._enforce_for_children_only = enforce_for_children_only
        self._blocked_patterns = blocked_patterns or list(self._DEFAULT_BLOCK_PATTERNS)

    def evaluate(
        self,
        *,
        goal_text: str,
        command_text: str,
        context: dict[str, Any] | None = None,
    ) -> ContentPolicyVerdict:
        if not self._enabled:
            return ContentPolicyVerdict(
                allowed=True,
                status="allowed",
                reason="content_policy_disabled",
                scope="disabled",
            )

        context = context or {}
        is_child_context = self._is_child_context(context)
        if self._enforce_for_children_only and not is_child_context:
            return ContentPolicyVerdict(
                allowed=True,
                status="allowed",
                reason="children_only_policy_not_applicable",
                scope="non_child_context",
            )

        full_text = f"{goal_text}\n{command_text}".strip().lower()
        matches: list[str] = []
        for pattern in self._blocked_patterns:
            if re.search(pattern, full_text, flags=re.IGNORECASE):
                matches.append(pattern)

        if matches:
            scope = "child_context" if is_child_context else "global_context"
            return ContentPolicyVerdict(
                allowed=False,
                status="blocked",
                reason="command_blocked_by_content_policy",
                scope=scope,
                matched_patterns=matches,
            )

        scope = "child_context" if is_child_context else "global_context"
        return ContentPolicyVerdict(
            allowed=True,
            status="allowed",
            reason="content_policy_pass",
            scope=scope,
        )

    @staticmethod
    def _is_child_context(context: dict[str, Any]) -> bool:
        bool_keys = [
            "is_child",
            "kid_mode",
            "child_mode",
            "for_kids",
        ]
        for key in bool_keys:
            value = context.get(key)
            if isinstance(value, bool) and value:
                return True
            if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
                return True

        profile = str(context.get("content_profile") or "").strip().lower()
        if profile in {"kid", "kids", "child", "children", "family_child"}:
            return True
        return False
