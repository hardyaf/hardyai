from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.types import Intent


_INFORMATION_REQUEST_PATTERN = re.compile(
    r"^(?:what|why|who|where|when|which|how)\b",
    flags=re.IGNORECASE,
)
_EXPLANATION_REQUEST_PATTERN = re.compile(
    r"^(?:tell me|explain|describe|compare|recommend|suggest)\b",
    flags=re.IGNORECASE,
)
_RESEARCH_REQUEST_PATTERN = re.compile(
    r"^(?:google|search(?: the)? web(?: for)?|look (?:it )?up|"
    r"find (?:current|latest|recent) (?:information|news|sources?))\b",
    flags=re.IGNORECASE,
)
_AUXILIARY_QUESTION_PATTERN = re.compile(
    r"^(?:do|does|did|are|is|can|could|would|will|should|has|have)\b",
    flags=re.IGNORECASE,
)
_ACTION_REQUEST_PATTERN = re.compile(
    r"\b(?:add|book|cancel|change|create|delete|make|mark|move|record|remove|"
    r"remind|rename|save|schedule|set|send|switch|turn|update|write)\b",
    flags=re.IGNORECASE,
)
_KNOWLEDGE_NOUN_PATTERN = re.compile(
    r"\b(?:recipe|meaning|difference between|best .+ movie|facts? about)\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class ConversationLaneDecision:
    route_to_conversation: bool
    reason: str
    confidence: float


class ConversationLanePolicy:
    """Separates informational conversation from semantic action repair.

    Micro remains authoritative for known tool intents. This policy only promotes an
    ``unknown`` result when the user text has an informational shape or the session
    resolver has already established that it is a conversational follow-up.
    """

    def decide(
        self,
        *,
        text: str,
        intent: Intent,
        contextual_followup: dict[str, Any] | None = None,
    ) -> ConversationLaneDecision:
        if intent == Intent.CONVERSATIONAL:
            return ConversationLaneDecision(True, "micro_conversation_intent", 0.98)
        if intent != Intent.UNKNOWN:
            return ConversationLaneDecision(False, "known_non_conversation_intent", 0.99)

        if isinstance(contextual_followup, dict) and bool(contextual_followup.get("resolved")):
            return ConversationLaneDecision(True, "resolved_conversation_followup", 0.92)

        cleaned = self._normalize(text)
        if not cleaned:
            return ConversationLaneDecision(False, "empty_text", 0.99)
        if _INFORMATION_REQUEST_PATTERN.match(cleaned):
            return ConversationLaneDecision(True, "informational_question", 0.9)
        if _EXPLANATION_REQUEST_PATTERN.match(cleaned):
            return ConversationLaneDecision(True, "explanation_request", 0.88)
        if _RESEARCH_REQUEST_PATTERN.match(cleaned):
            return ConversationLaneDecision(True, "explicit_research_request", 0.94)
        if _AUXILIARY_QUESTION_PATTERN.match(cleaned) and not _ACTION_REQUEST_PATTERN.search(cleaned):
            return ConversationLaneDecision(True, "auxiliary_information_question", 0.84)
        if _KNOWLEDGE_NOUN_PATTERN.search(cleaned):
            return ConversationLaneDecision(True, "knowledge_request", 0.84)
        return ConversationLaneDecision(False, "unknown_may_be_action", 0.72)

    @staticmethod
    def _normalize(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "").strip())
        cleaned = re.sub(
            r"^(?:(?:hi|hello|hey|yo)\s+)?jarvis[:,]?\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"^(?:please\s+)+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"^(?:can|could|would)\s+you\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        return cleaned.strip(" .!?")
