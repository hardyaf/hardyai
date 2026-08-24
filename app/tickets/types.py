from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(value: datetime | None = None) -> str:
    current = value or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def new_id() -> str:
    return str(uuid4())


class TicketStatus(StrEnum):
    CAPTURED = "captured"
    WAITING_CLARIFICATION = "waiting_clarification"
    EXECUTING = "executing"
    VERIFICATION_PENDING = "verification_pending"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    SUPERSEDED = "superseded"
    REMEDIATION_QUEUED = "remediation_queued"
    UNVERIFIABLE = "unverifiable"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class TicketKind(StrEnum):
    ORIGINAL = "original"
    REMEDIATION = "remediation"


class TicketEntryType(StrEnum):
    USER_REQUEST = "user_request"
    USER_CLARIFICATION = "user_clarification"
    ASSISTANT_CLARIFICATION = "assistant_clarification"
    ASSISTANT_RESPONSE = "assistant_response"
    MICRO_DECISION = "micro_decision"
    MAIN_REPAIR_DECISION = "main_repair_decision"
    ROUTING_DECISION = "routing_decision"
    MAIN_PLAN = "main_plan"
    EXECUTION_STARTED = "execution_started"
    OPERATION_RECEIPT = "operation_receipt"
    EXECUTION_COMPLETED = "execution_completed"
    STATE_TRANSITION = "state_transition"
    SOURCE_OBSERVATION = "source_observation"
    REVIEW_RESULT = "review_result"
    REMEDIATION_CREATED = "remediation_created"
    PLANE_SYNC_RESULT = "plane_sync_result"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY = "retry"
    COMPLETED = "completed"
    DEAD_LETTER = "dead_letter"


class JobType(StrEnum):
    TICKET_REVIEW = "ticket_review"
    PLANE_SYNC = "plane_sync"
    DOMAIN_COMMAND = "domain_command"
    TICKET_WATCHDOG = "ticket_watchdog"


class ReviewVerdict(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    SUPERSEDED = "superseded"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class OperationReceipt:
    operation_id: str
    idempotency_key: str
    capability: str
    action: str
    resource_key: str
    status: str
    expected_effect: dict[str, Any]
    validator_name: str
    validator_version: str
    resource_locator: dict[str, Any]
    provider_resource_id: str | None = None
    provider_revision: str | None = None
    committed_at: str | None = None
    execution_observation: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceObservation:
    verifier_name: str
    verifier_version: str
    resource_key: str
    exists: bool | None
    normalized_state: dict[str, Any]
    deterministic_verdict: ReviewVerdict
    observed_at: str
    provider_revision: str | None = None
    evidence_id: str = field(default_factory=new_id)
    later_change_detected: bool = False
    limitations: tuple[str, ...] = ()
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["deterministic_verdict"] = self.deterministic_verdict.value
        payload["limitations"] = list(self.limitations)
        return payload


@dataclass(frozen=True)
class ReviewRepair:
    capability: str
    entities: dict[str, Any]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewDecision:
    verdict: ReviewVerdict
    confidence: float
    summary: str
    evidence_refs: tuple[str, ...]
    mismatches: tuple[dict[str, Any], ...] = ()
    repair: ReviewRepair | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
            "mismatches": [dict(item) for item in self.mismatches],
            "repair": self.repair.to_dict() if self.repair is not None else None,
        }
