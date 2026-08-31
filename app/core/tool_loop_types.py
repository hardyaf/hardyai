from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.skills.tool_contracts import (
    FrozenDict,
    ToolContractError,
    ToolDescriptor,
    canonical_json,
    thaw_json,
)


_SKILL_ID_RE = re.compile(r"^skill\.[a-z][a-z0-9_.-]{1,95}$")
_TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}\.[a-z][a-z0-9_]{0,63}$")
_CALL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_OPAQUE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_JSON_POINTER_RE = re.compile(r"^(?:/(?:[^~/]|~0|~1)*)+$")
_MAX_MODEL_OBJECT_BYTES = 65_536
_MAX_MESSAGE_CHARS = 8_000
_MAX_QUESTION_CHARS = 2_000
_MAX_MISSING_FIELDS = 32
_MAX_PROVENANCE_CLAIMS = 32


class ToolLoopContractError(ValueError):
    """A content-free failure raised at the model/tool-loop boundary."""

    def __init__(self, code: str) -> None:
        normalized = str(code or "tool_loop_contract_invalid").strip().casefold()
        super().__init__(normalized)
        self.code = normalized


def _bounded_json(value: Any, *, code: str) -> Any:
    try:
        serialized = canonical_json(value)
    except ToolContractError as exc:
        raise ToolLoopContractError(code) from exc
    if len(serialized.encode("utf-8")) > _MAX_MODEL_OBJECT_BYTES:
        raise ToolLoopContractError(f"{code}_too_large")
    return value


def _text(value: Any, *, code: str, max_chars: int) -> str:
    if not isinstance(value, str):
        raise ToolLoopContractError(code)
    cleaned = value.strip()
    if not cleaned or len(cleaned) > max_chars:
        raise ToolLoopContractError(code)
    return cleaned


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolLoopContractError("commitment_confidence_invalid")
    normalized = float(value)
    if not 0.0 <= normalized <= 1.0:
        raise ToolLoopContractError("commitment_confidence_invalid")
    return normalized


def _string_tuple(
    value: Any,
    *,
    code: str,
    maximum: int,
    pattern: re.Pattern[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ToolLoopContractError(code)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            raise ToolLoopContractError(code)
        item = raw.strip().casefold()
        if not item or item in seen or (pattern is not None and not pattern.fullmatch(item)):
            raise ToolLoopContractError(code)
        normalized.append(item)
        seen.add(item)
    return tuple(normalized)


@dataclass(frozen=True)
class MainActionCommitment:
    mode: str
    confidence: float
    reason_code: str
    message: str | None = None
    question: str | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> MainActionCommitment:
        if not isinstance(raw, Mapping):
            raise ToolLoopContractError("commitment_not_object")
        _bounded_json(raw, code="commitment_invalid_json")
        mode = str(raw.get("mode") or "").strip().casefold()
        shapes = {
            "conversation": ({"mode", "confidence", "reason_code", "message"}, {
                "informational",
                "social",
                "non_actionable",
            }),
            "clarify_action": ({"mode", "confidence", "reason_code", "question"}, {
                "missing_referent",
                "ambiguous_goal",
            }),
            "execute_action": ({"mode", "confidence", "reason_code"}, {"plausible_action"}),
        }
        if mode not in shapes:
            raise ToolLoopContractError("commitment_mode_invalid")
        expected, reason_codes = shapes[mode]
        if set(raw) != expected:
            raise ToolLoopContractError("commitment_shape_invalid")
        reason_code = str(raw.get("reason_code") or "").strip().casefold()
        if reason_code not in reason_codes:
            raise ToolLoopContractError("commitment_reason_code_invalid")
        message = None
        question = None
        if mode == "conversation":
            message = _text(raw.get("message"), code="commitment_message_invalid", max_chars=_MAX_MESSAGE_CHARS)
        elif mode == "clarify_action":
            question = _text(raw.get("question"), code="commitment_question_invalid", max_chars=_MAX_QUESTION_CHARS)
        return cls(
            mode=mode,
            confidence=_confidence(raw.get("confidence")),
            reason_code=reason_code,
            message=message,
            question=question,
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "mode": self.mode,
            "confidence": self.confidence,
            "reason_code": self.reason_code,
        }
        if self.message is not None:
            value["message"] = self.message
        if self.question is not None:
            value["question"] = self.question
        return value


@dataclass(frozen=True)
class SkillSelection:
    mode: str
    selected_skill_ids: tuple[str, ...]
    reason_code: str | None = None

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        allowed_skill_ids: set[str] | frozenset[str],
        max_selected_skills: int,
    ) -> SkillSelection:
        if not isinstance(raw, Mapping):
            raise ToolLoopContractError("skill_selection_not_object")
        _bounded_json(raw, code="skill_selection_invalid_json")
        mode = str(raw.get("mode") or "").strip().casefold()
        expected = {"mode", "selected_skill_ids"} if mode == "select" else {
            "mode",
            "selected_skill_ids",
            "reason_code",
        }
        if mode not in {"select", "no_match"} or set(raw) != expected:
            raise ToolLoopContractError("skill_selection_shape_invalid")
        selected = _string_tuple(
            raw.get("selected_skill_ids"),
            code="skill_selection_ids_invalid",
            maximum=max(1, int(max_selected_skills)),
            pattern=_SKILL_ID_RE,
        )
        allowed = {str(item).strip().casefold() for item in allowed_skill_ids}
        if any(item not in allowed for item in selected):
            raise ToolLoopContractError("skill_selection_unauthorized")
        if mode == "select":
            if not selected:
                raise ToolLoopContractError("skill_selection_empty")
            return cls(mode=mode, selected_skill_ids=selected)
        reason_code = str(raw.get("reason_code") or "").strip().casefold()
        if selected or reason_code not in {"no_relevant_skill", "needs_more_context"}:
            raise ToolLoopContractError("skill_selection_no_match_invalid")
        return cls(mode=mode, selected_skill_ids=(), reason_code=reason_code)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "mode": self.mode,
            "selected_skill_ids": list(self.selected_skill_ids),
        }
        if self.reason_code is not None:
            result["reason_code"] = self.reason_code
        return result


def _decode_pointer(pointer: str) -> tuple[str, ...]:
    if not _JSON_POINTER_RE.fullmatch(pointer):
        raise ToolLoopContractError("provenance_pointer_invalid")
    return tuple(segment.replace("~1", "/").replace("~0", "~") for segment in pointer[1:].split("/"))


def _validate_provenance_claims(raw: Any) -> tuple[FrozenDict, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > _MAX_PROVENANCE_CLAIMS:
        raise ToolLoopContractError("provenance_claims_invalid")
    claims: list[FrozenDict] = []
    destinations: list[tuple[str, ...]] = []
    for value in raw:
        if not isinstance(value, Mapping):
            raise ToolLoopContractError("provenance_claim_invalid")
        kind = str(value.get("kind") or "").strip().casefold()
        if kind == "request_derived":
            expected = {"kind", "destination_pointer", "derivation"}
            derivations = {"interpret", "normalize", "extract", "summarize"}
        elif kind == "observation_derived":
            expected = {
                "kind",
                "destination_pointer",
                "source_observation_ref",
                "source_pointer",
                "derivation",
            }
            derivations = {"copy", "extract", "summarize"}
        else:
            raise ToolLoopContractError("provenance_kind_invalid")
        if set(value) != expected:
            raise ToolLoopContractError("provenance_claim_shape_invalid")
        destination = _decode_pointer(str(value.get("destination_pointer") or ""))
        if any(
            destination[: len(existing)] == existing or existing[: len(destination)] == destination
            for existing in destinations
        ):
            raise ToolLoopContractError("provenance_destination_overlap")
        destination_value = "/" + "/".join(
            segment.replace("~", "~0").replace("/", "~1") for segment in destination
        )
        normalized: dict[str, Any] = {
            "kind": kind,
            "destination_pointer": destination_value,
            "derivation": str(value.get("derivation") or "").strip().casefold(),
        }
        if normalized["derivation"] not in derivations:
            raise ToolLoopContractError("provenance_derivation_invalid")
        if kind == "observation_derived":
            observation_ref = str(value.get("source_observation_ref") or "").strip()
            if not _OPAQUE_REF_RE.fullmatch(observation_ref):
                raise ToolLoopContractError("provenance_observation_ref_invalid")
            normalized["source_observation_ref"] = observation_ref
            normalized["source_pointer"] = "/" + "/".join(
                segment.replace("~", "~0").replace("/", "~1")
                for segment in _decode_pointer(str(value.get("source_pointer") or ""))
            )
        _bounded_json(normalized, code="provenance_claim_invalid_json")
        claims.append(FrozenDict.from_mapping(normalized))
        destinations.append(destination)
    return tuple(claims)


@dataclass(frozen=True)
class ModelStep:
    mode: str
    tool_id: str | None = None
    call_id: str | None = None
    arguments: FrozenDict | None = None
    provenance_claims: tuple[FrozenDict, ...] = ()
    missing_fields: tuple[str, ...] = ()
    message: str | None = None
    question: str | None = None

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        allowed_tool_ids: set[str] | frozenset[str],
    ) -> ModelStep:
        if not isinstance(raw, Mapping):
            raise ToolLoopContractError("model_step_not_object")
        _bounded_json(raw, code="model_step_invalid_json")
        mode = str(raw.get("mode") or "").strip().casefold()
        if mode == "respond":
            if set(raw) != {"mode", "message"}:
                raise ToolLoopContractError("model_step_shape_invalid")
            return cls(
                mode=mode,
                message=_text(raw.get("message"), code="model_step_message_invalid", max_chars=_MAX_MESSAGE_CHARS),
            )
        if mode == "clarify":
            if set(raw) != {"mode", "tool_id", "arguments", "missing_fields", "question"}:
                raise ToolLoopContractError("model_step_shape_invalid")
        elif mode == "call_tool":
            if set(raw) not in (
                {"mode", "tool_id", "call_id", "arguments"},
                {"mode", "tool_id", "call_id", "arguments", "provenance_claims"},
            ):
                raise ToolLoopContractError("model_step_shape_invalid")
        else:
            raise ToolLoopContractError("model_step_mode_invalid")

        tool_id = str(raw.get("tool_id") or "").strip().casefold()
        if not _TOOL_ID_RE.fullmatch(tool_id) or tool_id not in {
            str(item).strip().casefold() for item in allowed_tool_ids
        }:
            raise ToolLoopContractError("model_step_tool_unauthorized")
        arguments = raw.get("arguments")
        if not isinstance(arguments, Mapping):
            raise ToolLoopContractError("model_step_arguments_invalid")
        _bounded_json(arguments, code="model_step_arguments_invalid")
        frozen_arguments = FrozenDict.from_mapping(arguments)
        if mode == "clarify":
            missing = _string_tuple(
                raw.get("missing_fields"),
                code="model_step_missing_fields_invalid",
                maximum=_MAX_MISSING_FIELDS,
            )
            if not missing:
                raise ToolLoopContractError("model_step_missing_fields_invalid")
            return cls(
                mode=mode,
                tool_id=tool_id,
                arguments=frozen_arguments,
                missing_fields=missing,
                question=_text(raw.get("question"), code="model_step_question_invalid", max_chars=_MAX_QUESTION_CHARS),
            )
        call_id = str(raw.get("call_id") or "").strip()
        if not _CALL_ID_RE.fullmatch(call_id):
            raise ToolLoopContractError("model_step_call_id_invalid")
        return cls(
            mode=mode,
            tool_id=tool_id,
            call_id=call_id,
            arguments=frozen_arguments,
            provenance_claims=_validate_provenance_claims(raw.get("provenance_claims")),
        )


@dataclass(frozen=True)
class ToolObservation:
    status: str
    observation_ref: str
    payload: FrozenDict
    safe_message: str
    missing_fields: tuple[str, ...]
    retryable: bool
    committed_effect: bool
    receipt_refs: tuple[str, ...]
    review_refs: tuple[str, ...]
    job_refs: tuple[str, ...]
    untrusted: bool

    def to_model_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "observation_ref": self.observation_ref,
            "payload": thaw_json(self.payload),
            "safe_message": self.safe_message,
            "missing_fields": list(self.missing_fields),
            "retryable": self.retryable,
            "committed_effect": self.committed_effect,
            "receipt_refs": list(self.receipt_refs),
            "review_refs": list(self.review_refs),
            "job_refs": list(self.job_refs),
            "untrusted": self.untrusted,
        }


@dataclass(frozen=True)
class RequestTemporalContext:
    now_utc: str
    timezone: str
    local_date: str

    @classmethod
    def create(cls, *, now: datetime, timezone_name: str) -> RequestTemporalContext:
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ToolLoopContractError("temporal_now_not_aware")
        normalized_now = now.astimezone(UTC)
        cleaned_timezone = str(timezone_name or "").strip()
        try:
            zone = ZoneInfo(cleaned_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ToolLoopContractError("temporal_timezone_invalid") from exc
        local_day: date = normalized_now.astimezone(zone).date()
        return cls(
            now_utc=normalized_now.isoformat(timespec="seconds"),
            timezone=cleaned_timezone,
            local_date=local_day.isoformat(),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "now_utc": self.now_utc,
            "timezone": self.timezone,
            "local_date": self.local_date,
        }


def validate_descriptor_payload(
    descriptor: ToolDescriptor,
    value: Mapping[str, Any],
    *,
    observation: bool = False,
    partial: bool = False,
) -> FrozenDict:
    """Reuse the P2 closed-schema validator for arguments and observations."""

    if not isinstance(value, Mapping):
        raise ToolLoopContractError("tool_payload_not_object")
    storage = descriptor.to_storage_dict()
    schema_key = "observation_schema" if observation else "input_schema"
    schema = thaw_json(getattr(descriptor, schema_key))
    if partial:
        schema["required"] = []
    storage["input_schema"] = schema
    try:
        validator = ToolDescriptor.from_mapping(storage, skill_id=descriptor.skill_id)
        return validator.validate_arguments(value)
    except ToolContractError as exc:
        raise ToolLoopContractError(
            "tool_observation_schema_invalid" if observation else "tool_arguments_schema_invalid"
        ) from exc


def partial_arguments_hash(value: Mapping[str, Any]) -> str:
    try:
        serialized = canonical_json(value)
    except ToolContractError as exc:
        raise ToolLoopContractError("partial_arguments_invalid") from exc
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
