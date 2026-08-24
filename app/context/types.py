from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


CURRENT_SESSION_CONTEXT_VERSION = 1


@dataclass
class RecentTurn:
    turn_id: str | None = None
    role: str = ""
    text: str = ""
    normalized_text: str = ""
    intent: str | None = None
    skill_id: str | None = None
    timestamp: str | None = None
    references: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "role": self.role,
            "text": self.text,
            "normalized_text": self.normalized_text,
            "intent": self.intent,
            "skill_id": self.skill_id,
            "timestamp": self.timestamp,
            "references": dict(self.references),
        }


@dataclass
class PendingInteraction:
    kind: str = "clarification"
    intent: str | None = None
    skill_id: str | None = None
    status: str = "pending"
    question: str | None = None
    expected_fields: list[str] = field(default_factory=list)
    candidate_entities: list[dict[str, Any]] = field(default_factory=list)
    proposed_action: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    expires_at: str | None = None
    origin_turn_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "intent": self.intent,
            "skill_id": self.skill_id,
            "status": self.status,
            "question": self.question,
            "expected_fields": list(self.expected_fields),
            "candidate_entities": list(self.candidate_entities),
            "proposed_action": dict(self.proposed_action),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "origin_turn_id": self.origin_turn_id,
            "metadata": dict(self.metadata),
        }


@dataclass
class TrackedEntity:
    domain: str = ""
    entity_type: str = ""
    entity_id: str | None = None
    display_name: str = ""
    aliases: list[str] = field(default_factory=list)
    salience: float = 0.0
    last_confirmed_at: str | None = None
    resolution_hints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "salience": float(self.salience),
            "last_confirmed_at": self.last_confirmed_at,
            "resolution_hints": dict(self.resolution_hints),
        }


@dataclass
class EntityRegistry:
    entities: list[TrackedEntity] = field(default_factory=list)
    alias_map: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [item.to_dict() for item in self.entities],
            "alias_map": dict(self.alias_map),
        }


@dataclass
class SessionSummary:
    summary_text: str = ""
    active_goals: list[str] = field(default_factory=list)
    resolved_decisions: list[str] = field(default_factory=list)
    open_threads: list[str] = field(default_factory=list)
    important_entities: list[str] = field(default_factory=list)
    last_updated_at: str | None = None
    source_turn_range: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_text": self.summary_text,
            "active_goals": list(self.active_goals),
            "resolved_decisions": list(self.resolved_decisions),
            "open_threads": list(self.open_threads),
            "important_entities": list(self.important_entities),
            "last_updated_at": self.last_updated_at,
            "source_turn_range": list(self.source_turn_range),
        }


@dataclass
class SessionContextState:
    version: int = CURRENT_SESSION_CONTEXT_VERSION
    active_agent_id: str = "jarvis"
    active_skill_id: str | None = None
    recent_turns: list[RecentTurn] = field(default_factory=list)
    pending_interaction: PendingInteraction | None = None
    session_summary: SessionSummary = field(default_factory=SessionSummary)
    entity_registry: EntityRegistry = field(default_factory=EntityRegistry)
    focus_stack: list[str] = field(default_factory=list)
    context_annotations: dict[str, Any] = field(default_factory=dict)
    channel_runtime: dict[str, Any] = field(default_factory=dict)
    main_agent_token_session: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_version": int(self.version),
            "active_agent_id": self.active_agent_id,
            "active_skill_id": self.active_skill_id,
            "recent_turns": [item.to_dict() for item in self.recent_turns],
            "pending_interaction": self.pending_interaction.to_dict()
            if self.pending_interaction is not None
            else None,
            "session_summary": self.session_summary.to_dict(),
            "entity_registry": self.entity_registry.to_dict(),
            "focus_stack": list(self.focus_stack),
            "context_annotations": dict(self.context_annotations),
            "channel_runtime": dict(self.channel_runtime),
            "main_agent_token_session": dict(self.main_agent_token_session),
        }


@dataclass
class WorkingContextPacket:
    session_state: SessionContextState
    pending_interaction: PendingInteraction | None
    recent_turns: list[RecentTurn]
    session_summary: SessionSummary
    relevant_memory: list[dict[str, Any]] = field(default_factory=list)
    entity_hints: list[TrackedEntity] = field(default_factory=list)
    active_skill_context: dict[str, Any] = field(default_factory=dict)
    channel_runtime: dict[str, Any] = field(default_factory=dict)
    budget_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_state": self.session_state.to_dict(),
            "pending_interaction": self.pending_interaction.to_dict()
            if self.pending_interaction is not None
            else None,
            "recent_turns": [item.to_dict() for item in self.recent_turns],
            "session_summary": self.session_summary.to_dict(),
            "relevant_memory": list(self.relevant_memory),
            "entity_hints": [item.to_dict() for item in self.entity_hints],
            "active_skill_context": dict(self.active_skill_context),
            "channel_runtime": dict(self.channel_runtime),
            "budget_metadata": dict(self.budget_metadata),
        }

