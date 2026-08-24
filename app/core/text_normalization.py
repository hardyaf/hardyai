from __future__ import annotations

import re
from typing import Final


_SKILL_ANCHOR_SPELLING_MAP: Final[dict[str, str]] = {
    # Calendar anchors
    "calandar": "calendar",
    "calender": "calendar",
    "calander": "calendar",
    "calandars": "calendars",
    "calenders": "calendars",
    "calanders": "calendars",
    "calandar's": "calendar's",
    "calender's": "calendar's",
    "calander's": "calendar's",
}

_SPELLING_PATTERNS: Final[list[tuple[re.Pattern[str], str]]] = [
    (re.compile(rf"\b{re.escape(source)}\b", flags=re.IGNORECASE), target)
    for source, target in sorted(_SKILL_ANCHOR_SPELLING_MAP.items(), key=lambda item: len(item[0]), reverse=True)
]


def normalize_skill_anchor_spelling(text: str) -> str:
    normalized = str(text or "")
    if not normalized:
        return normalized
    for pattern, replacement in _SPELLING_PATTERNS:
        normalized = pattern.sub(replacement, normalized)
    return normalized

