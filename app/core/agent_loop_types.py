from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentLoopState(str, Enum):
    READY = "READY"
    THINKING = "THINKING"
    ACTING = "ACTING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AgentLoopActionType(str, Enum):
    EXECUTE_COMMAND = "execute_command"
    REQUEST_APPROVAL = "request_approval"
    REQUEST_USER_INPUT = "request_user_input"
    COMPLETE = "complete"
    FAIL = "fail"


@dataclass(frozen=True)
class AgentLoopLimits:
    max_steps: int = 8
    max_failures: int = 2


@dataclass(frozen=True)
class PlannerActionCandidate:
    action_type: AgentLoopActionType
    score: float
    rationale: str
    signals: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "score": self.score,
            "rationale": self.rationale,
            "signals": dict(self.signals),
        }


@dataclass(frozen=True)
class PlannerDecision:
    step_number: int
    action_type: AgentLoopActionType
    rationale: str
    command_text: str = ""
    target: str | None = None
    subgoal: str = ""
    preconditions: list[str] = field(default_factory=list)
    expected_outcome: str = ""
    uncertainty: float = 0.0
    fallback_action: str | None = None
    candidate_actions: list[PlannerActionCandidate] = field(default_factory=list)
    selected_candidate_score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionOutcome:
    status: str
    success: bool
    summary: str
    result: dict[str, Any] = field(default_factory=dict)
    classification: dict[str, Any] = field(default_factory=dict)
    intent: str | None = None
    tool_name: str | None = None


@dataclass(frozen=True)
class EvaluatorOutcome:
    next_state: AgentLoopState
    continue_loop: bool
    terminal_status: str | None = None
    message: str | None = None
    failure_class: str | None = None
    next_action_hint: str | None = None


@dataclass(frozen=True)
class AgentLoopTraceEntry:
    step_number: int
    state_before: AgentLoopState
    chosen_action: str
    rationale: str
    subgoal: str | None
    preconditions: list[str]
    expected_outcome: str | None
    uncertainty: float | None
    fallback_action: str | None
    candidate_actions: list[dict[str, Any]]
    selected_candidate_score: float | None
    command_text: str | None
    target: str | None
    tool_or_skill: str | None
    result_status: str
    result_summary: str
    failure_class: str | None
    next_action_hint: str | None
    state_after: AgentLoopState

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_number": self.step_number,
            "state_before": self.state_before.value,
            "chosen_action": self.chosen_action,
            "rationale": self.rationale,
            "subgoal": self.subgoal,
            "preconditions": list(self.preconditions),
            "expected_outcome": self.expected_outcome,
            "uncertainty": self.uncertainty,
            "fallback_action": self.fallback_action,
            "candidate_actions": [dict(item) for item in self.candidate_actions],
            "selected_candidate_score": self.selected_candidate_score,
            "command_text": self.command_text,
            "target": self.target,
            "tool_or_skill": self.tool_or_skill,
            "result_status": self.result_status,
            "result_summary": self.result_summary,
            "failure_class": self.failure_class,
            "next_action_hint": self.next_action_hint,
            "state_after": self.state_after.value,
        }
