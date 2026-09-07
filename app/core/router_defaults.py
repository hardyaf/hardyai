from __future__ import annotations


class RouterDefaults:
    """Cohesive bounded defaults shared by the router composition surface."""

    CONVERSATIONAL_CONFIDENCE = 0.70
    LOW_CONFIDENCE_FLOOR = 0.55
    HIGH_RISK_CONFIDENCE = 0.80
    STICKY_FOLLOWUP_TURNS = 2
    PENDING_INTERACTION_TTL_SECONDS = 1800.0
    RECENT_TURNS_MAX_ENTRIES = 24
    RECENT_TURNS_MAX_CHARS = 6000
    SUMMARY_UPDATE_EVERY_TURNS = 6
    SUMMARY_BUDGET_CHAR_THRESHOLD = 5200
    SUMMARY_MAX_CHARS = 900
    NON_BLOCKING_AMBIGUITY_FLAGS = frozenset(
        {
            "short",
            "resolved_via_main_repair",
            "list_reference_resolved_from_context",
            "switch_reference_resolved_from_context",
            "main_sticky_followup",
        }
    )
