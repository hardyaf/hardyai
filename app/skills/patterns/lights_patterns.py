from __future__ import annotations

import re


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def extract_all_lights_action(text: str) -> str | None:
    lowered = _normalized(text)
    if not lowered:
        return None

    direct_match = re.match(
        r"^(?:turn|switch)\s+(?:(?P<action_first>on|off)\s+)?"
        r"(?:(?:all|every)\s+)?(?:the\s+)?lights?"
        r"(?:\s+(?P<action_last>on|off))?$",
        lowered,
    )
    if direct_match:
        action = direct_match.group("action_first") or direct_match.group("action_last")
        if action in {"on", "off"}:
            return action

    patterns = [
        r"\bturn\b.*\ball\b.*\blights?\b.*\bon\b",
        r"\bturn\b.*\bon\b.*\ball\b.*\blights?\b",
        r"\bswitch\b.*\ball\b.*\blights?\b.*\bon\b",
        r"\bturn\b.*\ball\b.*\blights?\b.*\boff\b",
        r"\bturn\b.*\boff\b.*\ball\b.*\blights?\b",
        r"\bswitch\b.*\ball\b.*\blights?\b.*\boff\b",
        r"\bevery\b.*\blight\b.*\bon\b",
        r"\bevery\b.*\blight\b.*\boff\b",
        r"\bwhole house\b.*\blights?\b.*\bon\b",
        r"\bwhole house\b.*\blights?\b.*\boff\b",
    ]
    if any(re.search(pattern, lowered) for pattern in patterns):
        if " off" in lowered or lowered.endswith("off"):
            return "off"
        if " on" in lowered or lowered.endswith("on"):
            return "on"
    return None


def extract_switch_action(text: str) -> tuple[str, str] | None:
    lowered = _normalized(text)
    if not lowered:
        return None

    switch_match = re.match(
        r"^(?:turn|switch)\s+(?P<action>on|off)\s+(?:the\s+)?(?P<switch>.+)$",
        lowered,
    )
    if not switch_match:
        switch_match = re.match(
            r"^(?:turn|switch)\s+(?:the\s+)?(?P<switch>.+)\s+(?P<action>on|off)$",
            lowered,
        )
    if not switch_match:
        return None

    switch_name = str(switch_match.group("switch") or "").strip()
    action = str(switch_match.group("action") or "").strip()
    if not switch_name or action not in {"on", "off"}:
        return None
    return switch_name, action
