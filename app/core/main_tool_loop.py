from __future__ import annotations

import hashlib
import ipaddress
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

from app.core.tool_loop_types import (
    CrossToolTransferBinding,
    ModelStep,
    ProvenanceEvaluation,
    RequestTemporalContext,
    SkillSelection,
    ToolLoopContractError,
    ToolObservation,
    validate_descriptor_payload,
)
from app.reviews.service import action_channel_binding_hash, action_request_binding_hash
from app.skills.tool_contracts import (
    FrozenDict,
    ToolContractError,
    ToolDescriptor,
    canonical_json,
    thaw_json,
    tool_operation_id,
)


class MainToolModel(Protocol):
    def select_skills(
        self,
        text: str,
        discovery_cards: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None: ...

    def next_tool_step(
        self,
        text: str,
        selected_tools: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        temporal_contexts: dict[str, dict[str, str]],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None: ...


UtcClock = Callable[[], datetime]
MonotonicClock = Callable[[], float]
ShadowObservationProvider = Callable[..., dict[str, Any] | None]
ApprovalBindingProvider = Callable[[dict[str, Any]], dict[str, str] | None]
_ANSWER_URL_PATTERN = re.compile(r"https?://[^\s)>\]}]+", flags=re.IGNORECASE)
_CROSS_DOMAIN_SENSITIVITY_MATRIX = {
    "normal": frozenset(
        {"normal", "private", "financial", "identity", "highly_restricted"}
    ),
    "private": frozenset({"private", "highly_restricted"}),
    "financial": frozenset({"financial", "highly_restricted"}),
    "identity": frozenset({"identity", "highly_restricted"}),
    "highly_restricted": frozenset(),
}


@dataclass(frozen=True)
class MainToolLoopLimits:
    max_selected_skills: int = 3
    max_steps: int = 8
    max_failures: int = 2
    max_identical_read_calls: int = 2
    max_observation_chars: int = 8_000
    max_total_observation_chars: int = 24_000
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        for field_name in (
            "max_selected_skills",
            "max_steps",
            "max_failures",
            "max_identical_read_calls",
            "max_observation_chars",
            "max_total_observation_chars",
            "timeout_seconds",
        ):
            if int(getattr(self, field_name)) < 1:
                raise ValueError(f"{field_name}_invalid")
        if self.max_selected_skills > 3:
            raise ValueError("max_selected_skills_exceeds_contract")


class MainToolLoop:
    """Bounded model-directed orchestration over P2's authorized semantic tools."""

    def __init__(
        self,
        *,
        model: MainToolModel | None,
        authorized_executor: Any,
        skill_registry: Any | None,
        domain_context: Any,
        pending_interactions: Any | None,
        event_log: Any | None,
        execution_mode: str,
        limits: MainToolLoopLimits | None = None,
        utc_clock: UtcClock | None = None,
        monotonic_clock: MonotonicClock | None = None,
        shadow_observation_provider: ShadowObservationProvider | None = None,
        action_approval_service: Any | None = None,
        approval_binding_provider: ApprovalBindingProvider | None = None,
    ) -> None:
        normalized_mode = str(execution_mode or "off").strip().casefold()
        if normalized_mode not in {"off", "shadow", "active"}:
            raise ValueError("main_tool_execution_mode_invalid")
        self._model = model
        self._authorized_executor = authorized_executor
        self._skill_registry = skill_registry
        self._domain_context = domain_context
        self._pending_interactions = pending_interactions
        self._event_log = event_log
        self._mode = normalized_mode
        self._limits = limits or MainToolLoopLimits()
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._shadow_observation_provider = shadow_observation_provider
        self._action_approval_service = action_approval_service
        self._approval_binding_provider = approval_binding_provider

    @property
    def mode(self) -> str:
        return self._mode

    @staticmethod
    def binding_hash(
        *,
        user_id: str,
        agent_id: str,
        source_interface: str,
        request_context: dict[str, Any],
    ) -> str:
        return MainToolLoop._binding_hash(
            user_id=user_id,
            agent_id=agent_id,
            source_interface=source_interface,
            context=request_context,
        )

    def run(
        self,
        *,
        text: str,
        request_id: str,
        session: Any,
        user_id: str,
        agent_id: str,
        source_interface: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        if self._mode == "off":
            return self._outcome(
                status="unavailable",
                message="Typed tool execution is disabled.",
                stop_reason="mode_off",
            )
        if self._model is None:
            return self._outcome(
                status="safe_stop",
                message="I could not safely select a capability for that request.",
                stop_reason="model_unavailable",
            )
        started = self._monotonic_clock()
        context = self._execution_context(
            request_context=request_context,
            session=session,
            user_id=user_id,
            agent_id=agent_id,
            source_interface=source_interface,
        )
        cards = self._authorized_executor.discovery_cards(
            user_id=user_id,
            agent_id=agent_id,
            source_interface=source_interface,
            request_context=context,
            max_skills=32,
        )
        safe_cards = [self._safe_card(card) for card in cards if isinstance(card, dict)]
        safe_cards = [card for card in safe_cards if card is not None]
        allowed_skill_ids = {
            str(card.get("skill_id") or "").strip().casefold() for card in safe_cards
        }
        steps = 0
        failures = 0
        if not allowed_skill_ids:
            return self._outcome(
                status="unavailable",
                message="No currently authorized skill matches that request.",
                stop_reason="no_relevant_skill",
                selected_skill_ids=[],
                steps=steps,
                failures=failures,
                elapsed_ms=self._elapsed_ms(started),
            )
        selection: SkillSelection | None = None
        while selection is None and failures < self._limits.max_failures:
            if self._deadline_reached(started) or steps >= self._limits.max_steps:
                return self._limit_stop(steps=steps, failures=failures, started=started)
            steps += 1
            raw_selection = self._model.select_skills(
                text,
                safe_cards,
                self._model_context(context, correction=failures > 0),
            )
            try:
                selection = SkillSelection.from_mapping(
                    raw_selection if isinstance(raw_selection, Mapping) else {},
                    allowed_skill_ids=allowed_skill_ids,
                    max_selected_skills=self._limits.max_selected_skills,
                )
            except ToolLoopContractError:
                failures += 1
        if selection is None:
            return self._outcome(
                status="safe_stop",
                message="I could not safely match that request to an available skill.",
                stop_reason="invalid_skill_selection",
                steps=steps,
                failures=failures,
                elapsed_ms=self._elapsed_ms(started),
            )
        recovered_followup = False
        if selection.mode == "no_match" and selection.reason_code == "needs_more_context":
            followup = context.get("main_tool_followup")
            followup_ids = followup.get("skill_ids") if isinstance(followup, dict) else None
            eligible_followups = {
                str(item or "").strip().casefold()
                for item in followup_ids or []
                if str(item or "").strip().casefold() in allowed_skill_ids
            }
            if len(eligible_followups) == 1:
                selection = SkillSelection.from_mapping(
                    {
                        "mode": "select",
                        "selected_skill_ids": sorted(eligible_followups),
                    },
                    allowed_skill_ids=allowed_skill_ids,
                    max_selected_skills=self._limits.max_selected_skills,
                )
                recovered_followup = True
        if selection.mode == "no_match":
            return self._outcome(
                status="unavailable",
                message="No currently authorized skill matches that request.",
                stop_reason=selection.reason_code or "no_relevant_skill",
                selected_skill_ids=[],
                steps=steps,
                failures=failures,
                elapsed_ms=self._elapsed_ms(started),
            )

        projections = self._authorized_executor.effective_tools(
            list(selection.selected_skill_ids),
            context,
        )
        descriptors = self._resolve_effective_descriptors(
            projections=projections,
            user_id=user_id,
            agent_id=agent_id,
        )
        temporal_contexts = self._temporal_contexts(
            descriptors=descriptors,
            request_context=context,
        )
        descriptors = {
            tool_id: descriptor
            for tool_id, descriptor in descriptors.items()
            if tool_id in temporal_contexts
        }
        projections = [
            projection
            for projection in projections
            if str(projection.get("tool_id") or "").strip().casefold() in descriptors
        ]
        if not projections:
            return self._outcome(
                status="unavailable",
                message="The selected capability is not enabled and authorized in this context.",
                stop_reason="no_effective_tools",
                selected_skill_ids=list(selection.selected_skill_ids),
                steps=steps,
                failures=failures,
                elapsed_ms=self._elapsed_ms(started),
            )

        initial_step = (
            self._content_free_continuation_step(
                marker=context.get("main_tool_followup"),
                selected_skill_ids=set(selection.selected_skill_ids),
                descriptors=descriptors,
            )
            if recovered_followup
            or str(context.get("main_action_reason_code") or "").strip().casefold()
            == "continuation_action"
            else None
        )

        outcome = self._run_steps(
            text=text,
            request_id=request_id,
            session=session,
            user_id=user_id,
            agent_id=agent_id,
            source_interface=source_interface,
            context=context,
            selection=selection,
            projections=projections,
            descriptors=descriptors,
            temporal_contexts=temporal_contexts,
            started=started,
            initial_steps=steps,
            initial_failures=failures,
            initial_step=initial_step,
        )
        return outcome

    def resume(
        self,
        *,
        text: str,
        pending: dict[str, Any],
        session: Any,
        user_id: str,
        agent_id: str,
        source_interface: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        if self._mode != "active" or self._model is None:
            return self._outcome(
                status="safe_stop",
                message="That pending action cannot be resumed in the current mode.",
                stop_reason="pending_mode_invalid",
            )
        metadata = pending.get("metadata") if isinstance(pending, dict) else None
        if not isinstance(metadata, dict) or metadata.get("pending_type") != "typed_tool_call_v1":
            return self._outcome(
                status="safe_stop",
                message="That pending action is no longer valid.",
                stop_reason="pending_contract_invalid",
            )
        context = self._execution_context(
            request_context=request_context,
            session=session,
            user_id=user_id,
            agent_id=agent_id,
            source_interface=source_interface,
        )
        if metadata.get("binding_hash") != self._binding_hash(
            user_id=user_id,
            agent_id=agent_id,
            source_interface=source_interface,
            context=context,
        ):
            return self._outcome(
                status="denied",
                message="That pending action is bound to a different request context.",
                stop_reason="pending_binding_changed",
            )
        skill_id = str(metadata.get("skill_id") or "").strip().casefold()
        tool_id = str(metadata.get("tool_id") or "").strip().casefold()
        root_request_id = str(metadata.get("root_request_id") or "").strip()
        call_ordinal = int(metadata.get("reserved_call_ordinal") or 0)
        if not skill_id or not tool_id or not root_request_id or call_ordinal < 1:
            return self._outcome(
                status="safe_stop",
                message="That pending action is incomplete and cannot be resumed.",
                stop_reason="pending_identity_invalid",
            )
        projections = self._authorized_executor.effective_tools([skill_id], context)
        descriptors = self._resolve_effective_descriptors(
            projections=projections,
            user_id=user_id,
            agent_id=agent_id,
        )
        descriptor = descriptors.get(tool_id)
        if descriptor is None or descriptor.contract_version != int(metadata.get("contract_version") or 0):
            return self._outcome(
                status="denied",
                message="That tool is no longer authorized with the same contract.",
                stop_reason="pending_tool_changed",
            )
        temporal = self._temporal_contexts(
            descriptors={tool_id: descriptor},
            request_context=context,
        )
        if tool_id not in temporal:
            return self._outcome(
                status="unavailable",
                message="The required timezone configuration is unavailable.",
                stop_reason="pending_temporal_context_unavailable",
            )
        raw_step = self._model.next_tool_step(
            text,
            [projection for projection in projections if projection.get("tool_id") == tool_id],
            [],
            {tool_id: temporal[tool_id].to_dict()},
            self._model_context(context, pending=metadata),
        )
        try:
            step = ModelStep.from_mapping(
                raw_step if isinstance(raw_step, Mapping) else {},
                allowed_tool_ids={tool_id},
            )
        except ToolLoopContractError:
            return self._outcome(
                status="safe_stop",
                message="I could not safely reconstruct that pending tool call.",
                stop_reason="pending_step_invalid",
            )
        if step.mode == "respond":
            return self._outcome(
                status="responded",
                message=step.message or "The pending action was not executed.",
                stop_reason="pending_responded",
                persistence=str(metadata.get("persistence") or "no_store"),
            )
        if step.tool_id != tool_id:
            return self._outcome(
                status="denied",
                message="A clarification cannot change the selected tool.",
                stop_reason="pending_tool_smuggling",
            )
        arguments = thaw_json(step.arguments or {})
        policy = str(metadata.get("persistence") or descriptor.persistence)
        stored_entities = pending.get("entities") if isinstance(pending.get("entities"), dict) else {}
        if policy == "no_store":
            merged = arguments
        else:
            expected = {str(item).strip() for item in pending.get("missing_fields") or []}
            existing = dict(stored_entities)
            if any(key not in expected and key not in existing for key in arguments):
                return self._outcome(
                    status="denied",
                    message="The clarification supplied an unexpected field.",
                    stop_reason="pending_unexpected_field",
                    persistence=policy,
                )
            for key, value in arguments.items():
                if key in existing and canonical_json(existing[key]) != canonical_json(value):
                    return self._outcome(
                        status="denied",
                        message="The clarification attempted to change a bound value.",
                        stop_reason="pending_bound_value_changed",
                        persistence=policy,
                    )
                existing[key] = value
            merged = existing
        if step.mode == "clarify":
            return self._store_clarification(
                session=session,
                descriptor=descriptor,
                request_id=root_request_id,
                call_ordinal=call_ordinal,
                user_id=user_id,
                agent_id=agent_id,
                source_interface=source_interface,
                context=context,
                arguments=merged,
                missing_fields=list(step.missing_fields),
                question=step.question or "Please restate every required value.",
                selected_skill_ids=[skill_id],
            )
        try:
            validated = validate_descriptor_payload(descriptor, merged)
        except ToolLoopContractError:
            return self._outcome(
                status="safe_stop",
                message="The supplied values do not satisfy the pending tool contract.",
                stop_reason="pending_arguments_invalid",
                persistence=policy,
            )
        if self._pending_interactions is not None:
            self._pending_interactions.clear(
                session=session,
                reason="main_tool_loop_pending_completed",
            )
        operation_ids: list[str] = []
        receipt_refs: list[str] = []
        dispatched = self._dispatch_call(
            descriptor=descriptor,
            arguments=validated,
            request_id=root_request_id,
            call_ordinal=call_ordinal,
            session=session,
            user_id=user_id,
            agent_id=agent_id,
            source_interface=source_interface,
            context=context,
            observations=[],
            operation_ids=operation_ids,
            receipt_refs=receipt_refs,
        )
        observation = dispatched.get("_observation")
        if isinstance(observation, ToolObservation):
            return self._outcome(
                status=observation.status,
                message=observation.safe_message,
                stop_reason="pending_tool_dispatched",
                selected_skill_ids=[skill_id],
                tool_ids=[tool_id],
                operation_ids=operation_ids,
                receipt_refs=receipt_refs,
                observation_count=1,
                committed_effect_count=1 if observation.committed_effect else 0,
                persistence=policy,
                steps=1,
            )
        return {key: value for key, value in dispatched.items() if not key.startswith("_")}

    def _run_steps(
        self,
        *,
        text: str,
        request_id: str,
        session: Any,
        user_id: str,
        agent_id: str,
        source_interface: str,
        context: dict[str, Any],
        selection: SkillSelection,
        projections: list[dict[str, Any]],
        descriptors: dict[str, ToolDescriptor],
        temporal_contexts: dict[str, RequestTemporalContext],
        started: float,
        initial_steps: int,
        initial_failures: int,
        initial_step: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        steps = initial_steps
        failures = initial_failures
        call_ordinal = 0
        observations: list[ToolObservation] = []
        observation_descriptors: dict[str, ToolDescriptor] = {}
        total_observation_chars = 0
        accepted_effectful: dict[tuple[str, int, str], ToolObservation] = {}
        read_counts: dict[tuple[str, int, str], int] = {}
        operation_ids: list[str] = []
        receipt_refs: list[str] = []
        policies: list[str] = []
        committed_effect_count = 0
        portions: list[dict[str, Any]] = []

        while steps < self._limits.max_steps and failures < self._limits.max_failures:
            if self._deadline_reached(started):
                return self._partial_stop(
                    reason="deadline_exceeded",
                    steps=steps,
                    failures=failures,
                    started=started,
                    selected_skill_ids=list(selection.selected_skill_ids),
                    tool_ids=list(descriptors),
                    observation_count=len(observations),
                    operation_ids=operation_ids,
                    receipt_refs=receipt_refs,
                    policies=policies,
                    committed_effect_count=committed_effect_count,
                    portions=portions,
                )
            steps += 1
            if initial_step is not None:
                raw_step = initial_step
                initial_step = None
            else:
                raw_step = self._model.next_tool_step(
                    text,
                    projections,
                    [
                        {
                            **item.to_model_dict(),
                            **(
                                {"tool_id": observation_descriptors[item.observation_ref].tool_id}
                                if item.observation_ref in observation_descriptors
                                else {}
                            ),
                        }
                        for item in observations
                    ],
                    {tool_id: value.to_dict() for tool_id, value in temporal_contexts.items()},
                    self._model_context(context, correction=failures > 0),
                )
            try:
                step = ModelStep.from_mapping(
                    raw_step if isinstance(raw_step, Mapping) else {},
                    allowed_tool_ids=set(descriptors),
                )
            except ToolLoopContractError as exc:
                rejected_tool_id = (
                    str(raw_step.get("tool_id") or "").strip().casefold()
                    if isinstance(raw_step, Mapping)
                    else ""
                )
                rejected_descriptor = descriptors.get(rejected_tool_id)
                if rejected_descriptor is not None:
                    portions.append(
                        self._rejected_portion(
                            descriptor=rejected_descriptor,
                            reason=exc.code,
                        )
                    )
                failures += 1
                continue

            if step.mode == "respond":
                outcome = self._outcome(
                    status="responded",
                    message=self._sanitize_untrusted_answer_links(
                        step.message or "Completed.",
                        observations=observations,
                    ),
                    stop_reason="model_responded",
                    selected_skill_ids=list(selection.selected_skill_ids),
                    tool_ids=list(descriptors),
                    operation_ids=operation_ids,
                    receipt_refs=receipt_refs,
                    observation_count=len(observations),
                    committed_effect_count=committed_effect_count,
                    persistence=self._most_restrictive_policy(policies),
                    portions=portions,
                    steps=steps,
                    failures=failures,
                    elapsed_ms=self._elapsed_ms(started),
                )
                marker = self._content_free_followup_marker(
                    selected_skill_ids=list(selection.selected_skill_ids),
                    observations=observations,
                    observation_descriptors=observation_descriptors,
                )
                if marker is not None:
                    outcome["_main_tool_followup"] = marker
                return outcome

            descriptor = descriptors[str(step.tool_id)]
            if not self._descriptor_unchanged(
                descriptor=descriptor,
                user_id=user_id,
                agent_id=agent_id,
            ):
                portions.append(
                    self._rejected_portion(
                        descriptor=descriptor,
                        reason="tool_contract_changed",
                    )
                )
                return self._partial_stop(
                    reason="tool_contract_changed",
                    steps=steps,
                    failures=failures,
                    started=started,
                    selected_skill_ids=list(selection.selected_skill_ids),
                    tool_ids=list(descriptors),
                    observation_count=len(observations),
                    operation_ids=operation_ids,
                    receipt_refs=receipt_refs,
                    policies=policies,
                    committed_effect_count=committed_effect_count,
                    portions=portions,
                )
            policies.append(descriptor.persistence)
            arguments = thaw_json(step.arguments or {})
            if step.mode == "clarify":
                call_ordinal += 1
                return self._store_clarification(
                    session=session,
                    descriptor=descriptor,
                    request_id=request_id,
                    call_ordinal=call_ordinal,
                    user_id=user_id,
                    agent_id=agent_id,
                    source_interface=source_interface,
                    context=context,
                    arguments=arguments,
                    missing_fields=list(step.missing_fields),
                    question=step.question or "Please provide the missing values.",
                    selected_skill_ids=list(selection.selected_skill_ids),
                    steps=steps,
                    failures=failures,
                    elapsed_ms=self._elapsed_ms(started),
                )
            try:
                validated = validate_descriptor_payload(descriptor, arguments)
                provenance = self._validate_p3_provenance(
                    step=step,
                    text=text,
                    observations=observations,
                    destination_descriptor=descriptor,
                    observation_descriptors=observation_descriptors,
                )
                provenance = self._reauthorize_transfer_sources(
                    evaluation=provenance,
                    observation_descriptors=observation_descriptors,
                    user_id=user_id,
                    agent_id=agent_id,
                    source_interface=source_interface,
                    context=context,
                )
            except ToolLoopContractError as exc:
                portions.append(
                    self._rejected_portion(
                        descriptor=descriptor,
                        reason=exc.code,
                    )
                )
                failures += 1
                continue
            policies.append(provenance.effective_persistence)
            args_hash = hashlib.sha256(canonical_json(validated).encode("utf-8")).hexdigest()
            call_key = (descriptor.tool_id, descriptor.contract_version, args_hash)
            if descriptor.effect != "read" and call_key in accepted_effectful:
                observations.append(accepted_effectful[call_key])
                continue
            if descriptor.effect == "read":
                current_count = read_counts.get(call_key, 0)
                if current_count >= self._limits.max_identical_read_calls:
                    return self._partial_stop(
                        reason="identical_read_limit",
                        steps=steps,
                        failures=failures,
                        started=started,
                        selected_skill_ids=list(selection.selected_skill_ids),
                        tool_ids=list(descriptors),
                        observation_count=len(observations),
                        operation_ids=operation_ids,
                        receipt_refs=receipt_refs,
                        policies=policies,
                        committed_effect_count=committed_effect_count,
                        portions=portions,
                    )
                read_counts[call_key] = current_count + 1
            call_ordinal += 1
            call_outcome = self._dispatch_call(
                descriptor=descriptor,
                arguments=validated,
                request_id=request_id,
                call_ordinal=call_ordinal,
                session=session,
                user_id=user_id,
                agent_id=agent_id,
                source_interface=source_interface,
                context=context,
                observations=observations,
                operation_ids=operation_ids,
                receipt_refs=receipt_refs,
                provenance=provenance,
            )
            observation = call_outcome.get("_observation")
            if not isinstance(observation, ToolObservation):
                return {key: value for key, value in call_outcome.items() if not key.startswith("_")}
            observation_chars = len(canonical_json(observation.to_model_dict()))
            total_observation_chars += observation_chars
            observations.append(observation)
            observation_descriptors[observation.observation_ref] = descriptor
            portions.append(self._call_portion(descriptor=descriptor, observation=observation))
            if descriptor.effect != "read" and (
                observation.committed_effect or observation.status == "ok"
            ):
                accepted_effectful[call_key] = observation
            if descriptor.effect != "read" and observation.committed_effect:
                committed_effect_count += 1
            if (
                observation_chars > min(descriptor.max_observation_chars, self._limits.max_observation_chars)
                or total_observation_chars > self._limits.max_total_observation_chars
            ):
                return self._partial_stop(
                    reason="observation_limit",
                    steps=steps,
                    failures=failures,
                    started=started,
                    selected_skill_ids=list(selection.selected_skill_ids),
                    tool_ids=list(descriptors),
                    observation_count=len(observations),
                    operation_ids=operation_ids,
                    receipt_refs=receipt_refs,
                    policies=policies,
                    committed_effect_count=committed_effect_count,
                    portions=portions,
                )
            if observation.status in {"denied", "waiting_for_approval", "queued"}:
                return self._outcome(
                    status=observation.status,
                    message=observation.safe_message,
                    stop_reason=f"tool_{observation.status}",
                    selected_skill_ids=list(selection.selected_skill_ids),
                    tool_ids=list(descriptors),
                    operation_ids=operation_ids,
                    receipt_refs=receipt_refs,
                    observation_count=len(observations),
                    committed_effect_count=sum(1 for item in observations if item.committed_effect),
                    persistence=self._most_restrictive_policy(policies),
                    portions=portions,
                    steps=steps,
                    failures=failures,
                    elapsed_ms=self._elapsed_ms(started),
                )
            if observation.status == "terminal_error":
                failures += 1
            elif observation.status == "retryable_error":
                failures += 1

        return self._partial_stop(
            reason="failure_limit" if failures >= self._limits.max_failures else "step_limit",
            steps=steps,
            failures=failures,
            started=started,
            selected_skill_ids=list(selection.selected_skill_ids),
            tool_ids=list(descriptors),
            observation_count=len(observations),
            operation_ids=operation_ids,
            receipt_refs=receipt_refs,
            policies=policies,
            committed_effect_count=committed_effect_count,
            portions=portions,
        )

    @classmethod
    def _sanitize_untrusted_answer_links(
        cls,
        message: str,
        *,
        observations: list[ToolObservation],
    ) -> str:
        untrusted = [item for item in observations if item.untrusted]
        if not untrusted:
            return str(message or "")
        allowed_urls: set[str] = set()
        for observation in untrusted:
            cls._collect_safe_url_fields(thaw_json(observation.payload), allowed_urls)

        def replace(match: re.Match[str]) -> str:
            raw = match.group(0)
            candidate = raw.rstrip(".,;:!?")
            suffix = raw[len(candidate) :]
            if candidate in allowed_urls:
                return raw
            return "[unverified link removed]" + suffix

        return _ANSWER_URL_PATTERN.sub(replace, str(message or ""))

    @classmethod
    def _collect_safe_url_fields(cls, value: Any, output: set[str]) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).strip().casefold() == "url" and isinstance(child, str):
                    candidate = child.strip()
                    if cls._is_safe_public_answer_url(candidate):
                        output.add(candidate)
                else:
                    cls._collect_safe_url_fields(child, output)
        elif isinstance(value, (list, tuple)):
            for child in value:
                cls._collect_safe_url_fields(child, output)

    @staticmethod
    def _is_safe_public_answer_url(value: str) -> bool:
        if not value or len(value) > 2_048 or any(ord(char) <= 32 for char in value):
            return False
        try:
            parsed = urlsplit(value)
            parsed.port
        except ValueError:
            return False
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        hostname = parsed.hostname.rstrip(".").casefold()
        if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
            return False
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return True
        return address.is_global

    def _dispatch_call(
        self,
        *,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        request_id: str,
        call_ordinal: int,
        session: Any,
        user_id: str,
        agent_id: str,
        source_interface: str,
        context: dict[str, Any],
        observations: list[ToolObservation],
        operation_ids: list[str],
        receipt_refs: list[str],
        provenance: ProvenanceEvaluation | None = None,
    ) -> dict[str, Any]:
        provenance = provenance or ProvenanceEvaluation(
            effective_persistence=descriptor.persistence
        )
        operation_id, _, normalized = tool_operation_id(
            root_request_id=request_id,
            tool_id=descriptor.tool_id,
            contract_version=descriptor.contract_version,
            call_ordinal=call_ordinal,
            arguments=arguments,
        )
        if self._mode == "shadow":
            if self._shadow_observation_provider is None:
                return self._outcome(
                    status="shadow_evaluated",
                    message="Shadow evaluation completed without dispatch.",
                    stop_reason="shadow_dispatch_blocked",
                    tool_ids=[descriptor.tool_id],
                    operation_ids=[],
                    receipt_refs=[],
                    observation_count=len(observations),
                    committed_effect_count=0,
                    persistence=descriptor.persistence,
                    would_call_count=1,
                )
            result = self._shadow_observation_provider(
                tool_id=descriptor.tool_id,
                arguments=thaw_json(normalized),
                call_ordinal=call_ordinal,
            )
        else:
            if descriptor.approval_rule == "denied":
                result = {
                    "status": "policy_denied",
                    "message": "This action is prohibited by its approval policy.",
                    "denial_reason": "tool_approval_policy_denied",
                    "payload": {},
                }
            elif provenance.requires_formal_approval and descriptor.effect == "read":
                result = {
                    "status": "policy_denied",
                    "message": "This cross-domain transfer requires formal action approval.",
                    "denial_reason": "cross_domain_read_approval_unavailable",
                    "payload": {},
                }
            elif provenance.requires_formal_approval or descriptor.approval_rule == "always" or (
                descriptor.approval_rule == "conditional"
                and self._conditional_approval_required(
                    descriptor,
                    normalized,
                    provenance=provenance,
                )
            ):
                result, operation_id = self._pause_for_approval(
                    descriptor=descriptor,
                    arguments=thaw_json(normalized),
                    request_id=request_id,
                    call_ordinal=call_ordinal,
                    session=session,
                    user_id=user_id,
                    agent_id=agent_id,
                    source_interface=source_interface,
                    context=context,
                    default_operation_id=operation_id,
                    provenance=provenance,
                )
            else:
                result = self._authorized_executor.execute_tool(
                    tool_id=descriptor.tool_id,
                    contract_version=descriptor.contract_version,
                    arguments=thaw_json(normalized),
                    source_interface=source_interface,
                    requested_by_user_id=user_id,
                    agent_id=agent_id,
                    request_context=context,
                    request_id=request_id,
                    call_ordinal=call_ordinal,
                )
        observation = self._observation_from_result(
            descriptor=descriptor,
            operation_id=operation_id,
            result=result,
            inherited_untrusted=provenance.untrusted,
        )
        if operation_id not in operation_ids:
            operation_ids.append(operation_id)
        for ref in observation.receipt_refs:
            if ref not in receipt_refs:
                receipt_refs.append(ref)
        return {"_observation": observation}

    @staticmethod
    def _conditional_approval_required(
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        *,
        provenance: ProvenanceEvaluation | None = None,
    ) -> bool:
        for condition in descriptor.approval_conditions:
            if condition == "cross_domain_no_store_transfer":
                return bool(
                    provenance is not None
                    and provenance.has_transfer
                    and provenance.requires_formal_approval
                )
            if condition == "external_recipients_present":
                for key in ("attendees", "guests", "invitees", "recipients"):
                    value = arguments.get(key)
                    if isinstance(value, (list, tuple)) and value:
                        return True
                    if isinstance(value, str) and value.strip():
                        return True
        return False

    def _pause_for_approval(
        self,
        *,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        request_id: str,
        call_ordinal: int,
        session: Any,
        user_id: str,
        agent_id: str,
        source_interface: str,
        context: dict[str, Any],
        default_operation_id: str,
        provenance: ProvenanceEvaluation,
    ) -> tuple[dict[str, Any], str]:
        available_dependencies = context.get("available_runtime_dependencies")
        approval_runtime_available = isinstance(
            available_dependencies, (list, tuple, set, frozenset)
        ) and "action_approval" in {
            str(item or "").strip().casefold() for item in available_dependencies
        }
        if (
            self._action_approval_service is None
            or self._pending_interactions is None
            or self._approval_binding_provider is None
            or not approval_runtime_available
        ):
            return (
                {
                    "status": "policy_denied",
                    "message": "This action requires approval, but approval is not available.",
                    "denial_reason": "action_approval_unavailable",
                    "payload": {},
                },
                default_operation_id,
            )
        prepare = getattr(self._authorized_executor, "prepare_tool_call", None)
        if not callable(prepare):
            return (
                {
                    "status": "policy_denied",
                    "message": "This action could not be bound for approval.",
                    "denial_reason": "action_approval_executor_unavailable",
                    "payload": {},
                },
                default_operation_id,
            )
        prepared = prepare(
            tool_id=descriptor.tool_id,
            contract_version=descriptor.contract_version,
            arguments=arguments,
            source_interface=source_interface,
            requested_by_user_id=user_id,
            agent_id=agent_id,
            request_context=context,
            request_id=request_id,
            call_ordinal=call_ordinal,
        )
        if isinstance(prepared, Mapping):
            return dict(prepared), default_operation_id
        envelope = getattr(prepared, "envelope", None)
        prepared_descriptor = getattr(prepared, "descriptor", None)
        resource_version = str(getattr(prepared, "resource_version", "") or "").strip()
        if envelope is None or not isinstance(prepared_descriptor, ToolDescriptor) or not resource_version:
            return (
                {
                    "status": "policy_denied",
                    "message": "This action could not be bound for approval.",
                    "denial_reason": "action_approval_binding_invalid",
                    "payload": {},
                },
                default_operation_id,
            )
        binding = self._approval_binding_provider(dict(context))
        if not isinstance(binding, Mapping):
            return (
                {
                    "status": "policy_denied",
                    "message": "The protected approval destination is unavailable.",
                    "denial_reason": "action_approval_destination_unavailable",
                    "payload": {},
                },
                envelope.operation_id,
            )
        approver = str(binding.get("approver_principal") or "").strip()
        if not approver:
            return (
                {
                    "status": "policy_denied",
                    "message": "The protected approval identity is unavailable.",
                    "denial_reason": "action_approval_approver_unavailable",
                    "payload": {},
                },
                envelope.operation_id,
            )
        expires_at = (self._utc_clock().astimezone(UTC) + timedelta(hours=1)).isoformat()
        try:
            transfer_manifest = (
                self._transfer_manifest(
                    prepared=prepared,
                    provenance=provenance,
                )
                if provenance.has_transfer
                else None
            )
            created = self._action_approval_service.create_action_proposal(
                envelope=envelope,
                descriptor=prepared_descriptor,
                resource_version=resource_version,
                approver_principal=approver,
                expires_at=expires_at,
                destination_purpose="human_reviews",
                transfer_manifest=transfer_manifest,
            )
        except Exception:  # Fail closed at the durable approval boundary.
            return (
                {
                    "status": "policy_denied",
                    "message": "The approval request could not be durably recorded.",
                    "denial_reason": "action_approval_persistence_failed",
                    "payload": {},
                },
                envelope.operation_id,
            )
        proposal = created.get("proposal") if isinstance(created, Mapping) else None
        review = created.get("review") if isinstance(created, Mapping) else None
        job = created.get("notification_job") if isinstance(created, Mapping) else None
        if not all(isinstance(item, Mapping) for item in (proposal, review, job)):
            return (
                {
                    "status": "policy_denied",
                    "message": "The approval request could not be verified.",
                    "denial_reason": "action_approval_receipt_invalid",
                    "payload": {},
                },
                envelope.operation_id,
            )
        if str(proposal.get("state") or "") != "pending" or str(job.get("status") or "") not in {
            "pending",
            "retry",
            "running",
            "completed",
        }:
            return (
                {
                    "status": "policy_denied",
                    "message": "This exact approval request is no longer pending.",
                    "denial_reason": "action_approval_not_pending",
                    "payload": {},
                },
                envelope.operation_id,
            )
        self._pending_interactions.store_action_approval_pointer(
            session=session,
            tool_id=envelope.tool_id,
            skill_id=envelope.skill_id,
            proposal_id=str(proposal["proposal_id"]),
            review_id=str(review["review_id"]),
            operation_id=envelope.operation_id,
            proposal_hash=str(proposal["proposal_hash"]),
            expires_at=str(proposal["expires_at"]),
            persistence=provenance.effective_persistence,
        )
        return (
            {
                "status": "waiting_for_approval",
                "message": "The exact action is waiting for approval.",
                "payload": {},
                "review_id": str(review["review_id"]),
                "job_id": str(job["job_id"]),
            },
            envelope.operation_id,
        )

    def _observation_from_result(
        self,
        *,
        descriptor: ToolDescriptor,
        operation_id: str,
        result: Any,
        inherited_untrusted: bool = False,
    ) -> ToolObservation:
        if not isinstance(result, Mapping):
            result = {"status": "error", "message": "The tool returned no valid result.", "payload": {}}
        raw_status = str(result.get("status") or "error").strip().casefold()
        status_map = {
            "ok": "ok",
            "needs_input": "needs_input",
            "needs_clarification": "needs_input",
            "waiting_for_approval": "waiting_for_approval",
            "queued": "queued",
            "policy_denied": "denied",
            "denied": "denied",
            "retryable_error": "retryable_error",
            "error": "terminal_error",
            "terminal_error": "terminal_error",
        }
        status = status_map.get(raw_status, "terminal_error")
        payload = result.get("payload")
        if not isinstance(payload, Mapping):
            properties = descriptor.observation_schema.get("properties")
            allowed = set(properties) if isinstance(properties, Mapping) else set()
            payload = {key: value for key, value in result.items() if key in allowed}
        if status in {"waiting_for_approval", "denied"} and not payload:
            validated_payload = FrozenDict.from_mapping({})
        else:
            try:
                validated_payload = validate_descriptor_payload(
                    descriptor,
                    payload,
                    observation=True,
                )
            except ToolLoopContractError:
                status = "terminal_error"
                validated_payload = FrozenDict.from_mapping({})
        message = str(result.get("message") or "").strip()
        if not message:
            message = {
                "ok": "The tool completed.",
                "needs_input": "The tool needs more input.",
                "waiting_for_approval": "The action is waiting for approval.",
                "queued": "The action was queued.",
                "denied": "The tool call was denied.",
                "retryable_error": "The tool encountered a retryable error.",
                "terminal_error": "The tool could not complete safely.",
            }[status]
        message = message[:2_000]
        missing = tuple(
            str(item).strip()
            for item in (result.get("missing_fields") or [])[:32]
            if isinstance(item, str) and str(item).strip()
        )
        receipts = self._opaque_refs(result, "receipt_id", "receipt_ids")
        reviews = self._opaque_refs(result, "review_id", "review_ids")
        jobs = self._opaque_refs(result, "job_id", "job_ids")
        committed = bool(result.get("committed_effect")) or (
            descriptor.effect != "read" and status == "ok"
        )
        observation_ref = "obs_v1_" + hashlib.sha256(
            f"{operation_id}\n{status}\n{canonical_json(validated_payload)}".encode("utf-8")
        ).hexdigest()
        return ToolObservation(
            status=status,
            observation_ref=observation_ref,
            payload=validated_payload,
            safe_message=message,
            missing_fields=missing,
            retryable=status == "retryable_error",
            committed_effect=committed,
            receipt_refs=receipts,
            review_refs=reviews,
            job_refs=jobs,
            untrusted=bool(result.get("untrusted", False)) or bool(inherited_untrusted),
            operation_id=operation_id,
        )

    def _store_clarification(
        self,
        *,
        session: Any,
        descriptor: ToolDescriptor,
        request_id: str,
        call_ordinal: int,
        user_id: str,
        agent_id: str,
        source_interface: str,
        context: dict[str, Any],
        arguments: Mapping[str, Any],
        missing_fields: list[str],
        question: str,
        selected_skill_ids: list[str],
        steps: int = 1,
        failures: int = 0,
        elapsed_ms: int = 0,
    ) -> dict[str, Any]:
        try:
            partial = validate_descriptor_payload(descriptor, arguments, partial=True)
        except ToolLoopContractError:
            return self._outcome(
                status="safe_stop",
                message="The partial tool arguments were invalid.",
                stop_reason="partial_arguments_invalid",
                persistence=descriptor.persistence,
                steps=steps,
                failures=failures,
                elapsed_ms=elapsed_ms,
            )
        required = {
            str(item).strip().casefold()
            for item in descriptor.input_schema.get("required") or ()
            if str(item).strip()
        }
        normalized_missing = []
        for item in missing_fields:
            field_name = str(item).strip().casefold()
            if field_name and field_name in required and field_name not in partial:
                normalized_missing.append(field_name)
        if not normalized_missing:
            return self._outcome(
                status="safe_stop",
                message="The clarification did not identify a valid missing field.",
                stop_reason="clarification_missing_fields_invalid",
                persistence=descriptor.persistence,
                steps=steps,
                failures=failures,
                elapsed_ms=elapsed_ms,
            )
        effective_question = str(question or "").strip()[:2_000]
        if descriptor.persistence == "no_store":
            field_list = ", ".join(normalized_missing)
            effective_question = (
                "For privacy, please restate all required values in one message"
                f" ({field_list})."
            )[:2_000]
        if self._mode == "active" and self._pending_interactions is not None:
            self._pending_interactions.store_tool_call(
                session=session,
                descriptor=descriptor,
                partial_arguments=thaw_json(partial),
                missing_fields=normalized_missing,
                question=effective_question,
                root_request_id=request_id,
                reserved_call_ordinal=call_ordinal,
                binding_hash=self._binding_hash(
                    user_id=user_id,
                    agent_id=agent_id,
                    source_interface=source_interface,
                    context=context,
                ),
                selected_skill_ids=selected_skill_ids,
            )
        return self._outcome(
            status="needs_clarification" if self._mode == "active" else "shadow_evaluated",
            message=effective_question,
            question=effective_question,
            missing_fields=normalized_missing,
            stop_reason="tool_clarification",
            selected_skill_ids=selected_skill_ids,
            tool_ids=[descriptor.tool_id],
            persistence=descriptor.persistence,
            steps=steps,
            failures=failures,
            elapsed_ms=elapsed_ms,
        )

    def _resolve_effective_descriptors(
        self,
        *,
        projections: list[dict[str, Any]],
        user_id: str,
        agent_id: str,
    ) -> dict[str, ToolDescriptor]:
        resolve_tool = getattr(self._skill_registry, "resolve_tool", None)
        if not callable(resolve_tool):
            return {}
        descriptors: dict[str, ToolDescriptor] = {}
        for projection in projections:
            tool_id = str(projection.get("tool_id") or "").strip().casefold()
            if not tool_id or tool_id in descriptors:
                return {}
            try:
                resolved = resolve_tool(tool_id=tool_id, user_id=user_id, agent_id=agent_id)
            except (ToolContractError, TypeError, ValueError):
                return {}
            if not isinstance(resolved, tuple) or len(resolved) != 2:
                return {}
            descriptor = resolved[1]
            if not isinstance(descriptor, ToolDescriptor):
                return {}
            descriptors[tool_id] = descriptor
        return descriptors

    def _descriptor_unchanged(
        self,
        *,
        descriptor: ToolDescriptor,
        user_id: str,
        agent_id: str,
    ) -> bool:
        current = self._resolve_effective_descriptors(
            projections=[{"tool_id": descriptor.tool_id}],
            user_id=user_id,
            agent_id=agent_id,
        ).get(descriptor.tool_id)
        return isinstance(current, ToolDescriptor) and canonical_json(
            current.to_storage_dict()
        ) == canonical_json(descriptor.to_storage_dict())

    def _temporal_contexts(
        self,
        *,
        descriptors: dict[str, ToolDescriptor],
        request_context: dict[str, Any],
    ) -> dict[str, RequestTemporalContext]:
        now = self._utc_clock()
        contexts: dict[str, RequestTemporalContext] = {}
        resolver = getattr(self._domain_context, "resolve_tool_timezone", None)
        for tool_id in descriptors:
            timezone_name = "UTC"
            if callable(resolver):
                timezone_name = resolver(tool_id=tool_id, request_context=request_context)
            if not timezone_name:
                continue
            try:
                contexts[tool_id] = RequestTemporalContext.create(
                    now=now,
                    timezone_name=str(timezone_name),
                )
            except ToolLoopContractError:
                continue
        return contexts

    @staticmethod
    def _validate_p3_provenance(
        *,
        step: ModelStep,
        text: str,
        observations: list[ToolObservation],
        destination_descriptor: ToolDescriptor | None = None,
        observation_descriptors: Mapping[str, ToolDescriptor] | None = None,
    ) -> ProvenanceEvaluation:
        """Validate exact P9 provenance without granting execution authority."""

        observation_by_ref = {item.observation_ref: item for item in observations}
        unique_observations = list(observation_by_ref.values())
        descriptor_by_ref = dict(observation_descriptors or {})
        arguments = thaw_json(step.arguments or {})
        destination_policy = (
            destination_descriptor.persistence
            if destination_descriptor is not None
            else "standard"
        )
        claims_by_destination = {
            str(claim.get("destination_pointer") or ""): claim
            for claim in step.provenance_claims
        }
        for pointer in claims_by_destination:
            if not MainToolLoop._pointer_exists(arguments, pointer):
                raise ToolLoopContractError("provenance_destination_missing")
        if not observations:
            if any(
                claim.get("kind") == "observation_derived"
                for claim in step.provenance_claims
            ):
                raise ToolLoopContractError("provenance_observation_ref_stale")
            return ProvenanceEvaluation(effective_persistence=destination_policy)

        leaf_pointers = MainToolLoop._leaf_pointers(arguments)
        model_derived_destinations: set[str] = set()
        observation_claims: list[tuple[Mapping[str, Any], ToolObservation, ToolDescriptor, Any]] = []
        for claim in step.provenance_claims:
            destination_pointer = str(claim.get("destination_pointer") or "")
            destination_found, destination_value = MainToolLoop._pointer_value(
                arguments,
                destination_pointer,
            )
            if not destination_found:
                raise ToolLoopContractError("provenance_destination_missing")
            if claim.get("kind") == "request_derived":
                if not MainToolLoop._request_value_appears(
                    destination_value,
                    MainToolLoop._normalize_request_text(text),
                ):
                    model_derived_destinations.add(destination_pointer)
                continue
            if destination_descriptor is None:
                raise ToolLoopContractError("observation_transfer_destination_missing")
            source_ref = str(claim.get("source_observation_ref") or "")
            source_observation = observation_by_ref.get(source_ref)
            source_descriptor = descriptor_by_ref.get(source_ref)
            source_pointer = str(claim.get("source_pointer") or "")
            if source_observation is None or source_descriptor is None:
                raise ToolLoopContractError("provenance_observation_ref_stale")
            source_domain = source_descriptor.tool_id.partition(".")[0]
            destination_domain = destination_descriptor.tool_id.partition(".")[0]
            cross_domain = source_domain != destination_domain
            transfer_field = MainToolLoop._matching_transfer_field(
                descriptor=source_descriptor,
                source_pointer=source_pointer,
                cross_domain=cross_domain,
            )
            if transfer_field is None:
                raise ToolLoopContractError("observation_transfer_field_denied")
            if cross_domain and not MainToolLoop._sensitivity_compatible(
                source_descriptor.sensitivity,
                destination_descriptor.sensitivity,
            ):
                raise ToolLoopContractError("observation_transfer_sensitivity_denied")
            source_found, source_value = MainToolLoop._transfer_pointer_value(
                thaw_json(source_observation.payload),
                source_pointer,
            )
            if (
                not source_found
                or (
                    str(claim.get("derivation") or "") == "copy"
                    and not MainToolLoop._transfer_values_match(
                        source_value=source_value,
                        destination_value=destination_value,
                        source_pointer=source_pointer,
                    )
                )
            ):
                raise ToolLoopContractError("observation_transfer_value_mismatch")
            observation_claims.append(
                (claim, source_observation, source_descriptor, transfer_field)
            )
            model_derived_destinations.add(destination_pointer)

        normalized_text = MainToolLoop._normalize_request_text(text)
        for pointer, value in leaf_pointers:
            matching_claims = [
                claim
                for destination, claim in claims_by_destination.items()
                if MainToolLoop._pointer_covers(destination, pointer)
            ]
            if len(matching_claims) > 1:
                raise ToolLoopContractError("provenance_destination_overlap")
            if matching_claims:
                continue
            if MainToolLoop._request_value_appears(value, normalized_text):
                continue
            raise ToolLoopContractError("argument_provenance_unproven")

        destination_domain = (
            destination_descriptor.tool_id.partition(".")[0]
            if destination_descriptor is not None
            else ""
        )
        cross_domain = bool(
            model_derived_destinations
            and destination_descriptor is not None
            and any(
                descriptor.tool_id.partition(".")[0] != destination_domain
                for descriptor in descriptor_by_ref.values()
            )
        )
        inherited_untrusted = bool(model_derived_destinations) and any(
            observation.untrusted for observation in unique_observations
        )
        if not cross_domain:
            return ProvenanceEvaluation(
                effective_persistence=MainToolLoop._most_restrictive_policy(
                    [destination_policy]
                    + (
                        [
                            descriptor.persistence
                            for descriptor in descriptor_by_ref.values()
                        ]
                        if model_derived_destinations
                        else []
                    )
                ),
                untrusted=inherited_untrusted,
            )

        named_by_ref: dict[str, list[tuple[Mapping[str, Any], Any]]] = {}
        for claim, source_observation, _source_descriptor, transfer_field in observation_claims:
            named_by_ref.setdefault(source_observation.observation_ref, []).append(
                (claim, transfer_field)
            )
        bindings: list[CrossToolTransferBinding] = []
        effective_policies = [destination_policy]
        for observation in unique_observations:
            source_descriptor = descriptor_by_ref.get(observation.observation_ref)
            if source_descriptor is None:
                raise ToolLoopContractError("provenance_observation_descriptor_missing")
            source_domain = source_descriptor.tool_id.partition(".")[0]
            source_crosses_domain = source_domain != destination_domain
            if source_crosses_domain and not MainToolLoop._sensitivity_compatible(
                source_descriptor.sensitivity,
                destination_descriptor.sensitivity,
            ):
                raise ToolLoopContractError("observation_transfer_sensitivity_denied")
            named = named_by_ref.get(observation.observation_ref, [])
            if named:
                for claim, transfer_field in named:
                    source_pointer = str(claim.get("source_pointer") or "")
                    if source_crosses_domain and transfer_field.scope != "cross_domain":
                        raise ToolLoopContractError("observation_transfer_field_denied")
                    source_found, source_value = MainToolLoop._transfer_pointer_value(
                        thaw_json(observation.payload),
                        source_pointer,
                    )
                    if not source_found:
                        raise ToolLoopContractError("observation_transfer_value_mismatch")
                    bindings.append(
                        MainToolLoop._transfer_binding(
                            observation=observation,
                            descriptor=source_descriptor,
                            transfer_pattern=transfer_field.pattern,
                            transfer_scope=transfer_field.scope,
                            source_pointer=source_pointer,
                            subtree=source_value,
                        )
                    )
            else:
                cross_fields = [
                    field
                    for field in source_descriptor.transferable_observation_fields
                    if field.scope == "cross_domain"
                ]
                if not cross_fields:
                    raise ToolLoopContractError("observation_exposure_transfer_denied")
                bindings.append(
                    MainToolLoop._transfer_binding(
                        observation=observation,
                        descriptor=source_descriptor,
                        transfer_pattern=cross_fields[0].pattern,
                        transfer_scope=cross_fields[0].scope,
                        source_pointer="",
                        subtree=thaw_json(observation.payload),
                    )
                )
            effective_policies.append(source_descriptor.persistence)

        if not bindings:
            raise ToolLoopContractError("cross_domain_transfer_source_missing")
        destination_values = tuple(
            (
                pointer,
                hashlib.sha256(
                    canonical_json(MainToolLoop._pointer_value(arguments, pointer)[1]).encode(
                        "utf-8"
                    )
                ).hexdigest(),
            )
            for pointer in sorted(model_derived_destinations)
        )
        return ProvenanceEvaluation(
            destination_values=destination_values,
            sources=tuple(bindings),
            effective_persistence=MainToolLoop._most_restrictive_policy(
                effective_policies
            ),
            untrusted=any(binding.untrusted for binding in bindings),
            cross_domain=True,
            requires_formal_approval=any(
                binding.persistence == "no_store" for binding in bindings
            ),
        )

    @staticmethod
    def _normalize_request_text(value: str) -> str:
        return " ".join(str(value or "").casefold().split())

    @staticmethod
    def _pointer_covers(ancestor: str, pointer: str) -> bool:
        return pointer == ancestor or pointer.startswith(ancestor + "/")

    @staticmethod
    def _leaf_pointers(value: Any, pointer: str = "") -> list[tuple[str, Any]]:
        if isinstance(value, Mapping):
            if not value:
                return [(pointer, value)]
            leaves: list[tuple[str, Any]] = []
            for key, child in value.items():
                encoded = str(key).replace("~", "~0").replace("/", "~1")
                leaves.extend(MainToolLoop._leaf_pointers(child, f"{pointer}/{encoded}"))
            return leaves
        if isinstance(value, (list, tuple)):
            if not value:
                return [(pointer, value)]
            leaves = []
            for index, child in enumerate(value):
                leaves.extend(MainToolLoop._leaf_pointers(child, f"{pointer}/{index}"))
            return leaves
        return [(pointer, value)]

    @staticmethod
    def _matching_transfer_field(
        *,
        descriptor: ToolDescriptor,
        source_pointer: str,
        cross_domain: bool,
    ) -> Any | None:
        for field in descriptor.transferable_observation_fields:
            if (
                (not cross_domain or field.scope == "cross_domain")
                and MainToolLoop._pointer_pattern_matches(field.pattern, source_pointer)
            ):
                return field
        return None

    @staticmethod
    def _sensitivity_compatible(source: str, destination: str) -> bool:
        return str(destination) in _CROSS_DOMAIN_SENSITIVITY_MATRIX.get(
            str(source),
            frozenset(),
        )

    @staticmethod
    def _transfer_binding(
        *,
        observation: ToolObservation,
        descriptor: ToolDescriptor,
        transfer_pattern: str,
        transfer_scope: str,
        source_pointer: str,
        subtree: Any,
    ) -> CrossToolTransferBinding:
        return CrossToolTransferBinding(
            observation_ref=observation.observation_ref,
            operation_id=observation.operation_id,
            skill_id=descriptor.skill_id,
            domain=descriptor.tool_id.partition(".")[0],
            tool_id=descriptor.tool_id,
            contract_version=descriptor.contract_version,
            descriptor_hash=hashlib.sha256(
                canonical_json(descriptor.to_storage_dict()).encode("utf-8")
            ).hexdigest(),
            resource_version="",
            transfer_pattern=transfer_pattern,
            transfer_scope=transfer_scope,
            source_pointer=source_pointer,
            subtree_hash=hashlib.sha256(
                canonical_json(subtree).encode("utf-8")
            ).hexdigest(),
            sensitivity=descriptor.sensitivity,
            persistence=descriptor.persistence,
            untrusted=observation.untrusted,
        )

    def _reauthorize_transfer_sources(
        self,
        *,
        evaluation: ProvenanceEvaluation,
        observation_descriptors: Mapping[str, ToolDescriptor],
        user_id: str,
        agent_id: str,
        source_interface: str,
        context: dict[str, Any],
    ) -> ProvenanceEvaluation:
        if not evaluation.has_transfer:
            return evaluation
        authorize = getattr(self._authorized_executor, "authorize_tool_reference", None)
        if not callable(authorize):
            raise ToolLoopContractError("transfer_source_reauthorization_unavailable")
        rebound: list[CrossToolTransferBinding] = []
        authorization_cache: dict[tuple[str, int], Any] = {}
        for binding in evaluation.sources:
            if not binding.operation_id:
                raise ToolLoopContractError("transfer_source_operation_missing")
            key = (binding.tool_id, binding.contract_version)
            current = authorization_cache.get(key)
            if current is None:
                current = authorize(
                    tool_id=binding.tool_id,
                    contract_version=binding.contract_version,
                    source_interface=source_interface,
                    requested_by_user_id=user_id,
                    agent_id=agent_id,
                    request_context=context,
                )
                authorization_cache[key] = current
            if isinstance(current, Mapping):
                raise ToolLoopContractError("transfer_source_unauthorized")
            descriptor = getattr(current, "descriptor", None)
            observed_descriptor = observation_descriptors.get(binding.observation_ref)
            descriptor_hash = str(getattr(current, "descriptor_hash", "") or "")
            resource_version = str(getattr(current, "resource_version", "") or "")
            if (
                not isinstance(descriptor, ToolDescriptor)
                or not isinstance(observed_descriptor, ToolDescriptor)
                or not descriptor_hash
                or not resource_version
                or canonical_json(descriptor.to_storage_dict())
                != canonical_json(observed_descriptor.to_storage_dict())
                or descriptor_hash != binding.descriptor_hash
                or descriptor.tool_id != binding.tool_id
                or descriptor.skill_id != binding.skill_id
                or descriptor.sensitivity != binding.sensitivity
                or descriptor.persistence != binding.persistence
                or not any(
                    field.pattern == binding.transfer_pattern
                    and field.scope == binding.transfer_scope
                    for field in descriptor.transferable_observation_fields
                )
            ):
                raise ToolLoopContractError("transfer_source_contract_changed")
            rebound.append(
                CrossToolTransferBinding(
                    observation_ref=binding.observation_ref,
                    operation_id=binding.operation_id,
                    skill_id=binding.skill_id,
                    domain=binding.domain,
                    tool_id=binding.tool_id,
                    contract_version=binding.contract_version,
                    descriptor_hash=descriptor_hash,
                    resource_version=resource_version,
                    transfer_pattern=binding.transfer_pattern,
                    transfer_scope=binding.transfer_scope,
                    source_pointer=binding.source_pointer,
                    subtree_hash=binding.subtree_hash,
                    sensitivity=binding.sensitivity,
                    persistence=binding.persistence,
                    untrusted=binding.untrusted,
                )
            )
        return ProvenanceEvaluation(
            destination_values=evaluation.destination_values,
            sources=tuple(rebound),
            effective_persistence=evaluation.effective_persistence,
            untrusted=evaluation.untrusted,
            cross_domain=evaluation.cross_domain,
            requires_formal_approval=evaluation.requires_formal_approval,
        )

    @staticmethod
    def _transfer_manifest(
        *,
        prepared: Any,
        provenance: ProvenanceEvaluation,
    ) -> dict[str, Any]:
        envelope = getattr(prepared, "envelope", None)
        descriptor = getattr(prepared, "descriptor", None)
        descriptor_hash = str(getattr(prepared, "descriptor_hash", "") or "")
        resource_version = str(getattr(prepared, "resource_version", "") or "")
        if (
            envelope is None
            or not isinstance(descriptor, ToolDescriptor)
            or not descriptor_hash
            or not resource_version
            or not provenance.has_transfer
        ):
            raise ToolLoopContractError("transfer_manifest_binding_invalid")
        destination_values: list[dict[str, str]] = []
        for pointer, _preparation_hash in provenance.destination_values:
            found, value = MainToolLoop._pointer_value(
                thaw_json(envelope.arguments),
                pointer,
            )
            if not found:
                raise ToolLoopContractError("transfer_destination_changed")
            destination_values.append(
                {
                    "destination_pointer": pointer,
                    "value_hash": hashlib.sha256(
                        canonical_json(value).encode("utf-8")
                    ).hexdigest(),
                }
            )
        return {
            "manifest_version": 1,
            "request_id": envelope.root_request_id,
            "request_hash": action_request_binding_hash(envelope),
            "requester_user_id": envelope.user_id,
            "agent_id": envelope.agent_id,
            "channel_binding_hash": action_channel_binding_hash(envelope),
            "destination": {
                "skill_id": envelope.skill_id,
                "domain": envelope.tool_id.partition(".")[0],
                "tool_id": envelope.tool_id,
                "contract_version": envelope.contract_version,
                "descriptor_hash": descriptor_hash,
                "resource_version": resource_version,
                "arguments_hash": envelope.arguments_hash,
                "sensitivity": descriptor.sensitivity,
                "persistence": descriptor.persistence,
            },
            "destination_values": destination_values,
            "sources": [source.to_manifest_dict() for source in provenance.sources],
        }

    @staticmethod
    def _request_value_appears(value: Any, normalized_text: str) -> bool:
        """Verify explicit scalar or structured argument values against the request text."""

        if isinstance(value, str):
            token = " ".join(value.casefold().split())
            return bool(token and token in normalized_text)
        if isinstance(value, bool):
            return str(value).casefold() in normalized_text.split()
        if value is None:
            return "null" in normalized_text.split()
        if isinstance(value, int):
            token = str(value).casefold()
            number_words = {
                0: "zero",
                1: "one",
                2: "two",
                3: "three",
                4: "four",
                5: "five",
                6: "six",
                7: "seven",
                8: "eight",
                9: "nine",
                10: "ten",
                11: "eleven",
                12: "twelve",
            }
            request_tokens = set(normalized_text.split())
            return bool(
                token
                and (
                    token in request_tokens
                    or number_words.get(value) in request_tokens
                )
            )
        if isinstance(value, float):
            token = str(value).casefold()
            return bool(token and token in normalized_text)
        if isinstance(value, (list, tuple)):
            return bool(value) and all(
                MainToolLoop._request_value_appears(item, normalized_text) for item in value
            )
        if isinstance(value, Mapping):
            return bool(value) and all(
                MainToolLoop._request_value_appears(item, normalized_text)
                for item in value.values()
            )
        return False

    @staticmethod
    def _pointer_exists(value: Any, pointer: str) -> bool:
        return MainToolLoop._pointer_value(value, pointer)[0]

    @staticmethod
    def _pointer_value(value: Any, pointer: str) -> tuple[bool, Any]:
        current = value
        for encoded in str(pointer or "")[1:].split("/"):
            segment = encoded.replace("~1", "/").replace("~0", "~")
            if isinstance(current, Mapping) and segment in current:
                current = current[segment]
                continue
            if isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
                current = current[int(segment)]
                continue
            return False, None
        return True, current

    @staticmethod
    def _transfer_pointer_value(value: Any, pointer: str) -> tuple[bool, Any]:
        """Resolve one allowed transfer pointer, aggregating wildcard array values."""

        encoded_segments = str(pointer or "")[1:].split("/")
        segments = [
            segment.replace("~1", "/").replace("~0", "~")
            for segment in encoded_segments
        ]
        if "*" not in segments:
            return MainToolLoop._pointer_value(value, pointer)
        current = [value]
        for segment in segments:
            following: list[Any] = []
            for item in current:
                if segment == "*" and isinstance(item, list):
                    following.extend(item)
                elif isinstance(item, Mapping) and segment in item:
                    following.append(item[segment])
                elif (
                    isinstance(item, list)
                    and segment.isdigit()
                    and int(segment) < len(item)
                ):
                    following.append(item[int(segment)])
            if not following:
                return False, None
            current = following
        return True, current

    @staticmethod
    def _transfer_values_match(
        *,
        source_value: Any,
        destination_value: Any,
        source_pointer: str,
    ) -> bool:
        if canonical_json(source_value) == canonical_json(destination_value):
            return True
        if not isinstance(source_value, list) and isinstance(destination_value, list):
            return len(destination_value) == 1 and canonical_json(
                source_value
            ) == canonical_json(destination_value[0])
        if "*" not in str(source_pointer or ""):
            return False
        source_values = source_value if isinstance(source_value, list) else [source_value]
        destination_values = (
            destination_value if isinstance(destination_value, list) else [destination_value]
        )
        scalar_types = (str, int, bool)
        if (
            not source_values
            or not destination_values
            or len(destination_values) > len(source_values)
            or any(not isinstance(item, scalar_types) for item in source_values)
            or any(not isinstance(item, scalar_types) for item in destination_values)
        ):
            return False
        source_canonical = {canonical_json(item) for item in source_values}
        destination_canonical = [canonical_json(item) for item in destination_values]
        return len(destination_canonical) == len(set(destination_canonical)) and all(
            item in source_canonical for item in destination_canonical
        )

    @staticmethod
    def _pointer_pattern_matches(pattern: str, pointer: str) -> bool:
        pattern_segments = str(pattern or "")[1:].split("/")
        pointer_segments = str(pointer or "")[1:].split("/")
        if len(pattern_segments) > len(pointer_segments):
            return False
        return all(
            expected == "*" or expected == observed
            for expected, observed in zip(pattern_segments, pointer_segments, strict=False)
        )

    @staticmethod
    def _safe_card(raw: dict[str, Any]) -> dict[str, Any] | None:
        skill_id = str(raw.get("skill_id") or "").strip().casefold()
        title = str(raw.get("title") or raw.get("skill_name") or "").strip()[:160]
        purpose = str(raw.get("purpose") or "").strip()[:500]
        availability = str(raw.get("availability") or "available").strip().casefold()
        tags = [
            str(item).strip().casefold()[:48]
            for item in (raw.get("safe_tags") or raw.get("tags") or [])[:16]
            if isinstance(item, str) and str(item).strip()
        ]
        if not skill_id or not title or not purpose:
            return None
        return {
            "skill_id": skill_id,
            "title": title,
            "purpose": purpose,
            "safe_tags": tags,
            "availability": availability,
        }

    @staticmethod
    def _opaque_refs(result: Mapping[str, Any], singular: str, plural: str) -> tuple[str, ...]:
        values: list[Any] = []
        if result.get(singular) is not None:
            values.append(result.get(singular))
        raw_plural = result.get(plural)
        if isinstance(raw_plural, (list, tuple)):
            values.extend(raw_plural[:64])
        refs: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = str(raw or "").strip()
            if value and len(value) <= 256 and value not in seen:
                refs.append(value)
                seen.add(value)
        return tuple(refs)

    @staticmethod
    def _execution_context(
        *,
        request_context: dict[str, Any],
        session: Any,
        user_id: str,
        agent_id: str,
        source_interface: str,
    ) -> dict[str, Any]:
        session_context = getattr(session, "context_reference", {})
        session_context = session_context if isinstance(session_context, dict) else {}
        return {
            **dict(request_context),
            "requested_by_user_id": user_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "source_interface": source_interface,
            "source": source_interface,
            "session_id": str(getattr(session, "session_id", "") or ""),
            "main_tool_followup": session_context.get("main_tool_followup"),
        }

    @staticmethod
    def _content_free_followup_marker(
        *,
        selected_skill_ids: list[str],
        observations: list[ToolObservation],
        observation_descriptors: Mapping[str, ToolDescriptor],
    ) -> dict[str, Any] | None:
        continuations: list[dict[str, str]] = []
        for observation in observations:
            if observation.status != "ok":
                continue
            descriptor = observation_descriptors.get(observation.observation_ref)
            if descriptor is None or descriptor.effect != "read":
                continue
            input_schema = thaw_json(descriptor.input_schema)
            properties = input_schema.get("properties") if isinstance(input_schema, dict) else None
            payload = thaw_json(observation.payload)
            if not isinstance(properties, dict) or not isinstance(payload, dict):
                continue
            for output_field, value in payload.items():
                output_name = str(output_field or "").strip()
                if not output_name.startswith("next_") or not value:
                    continue
                argument_field = output_name.removeprefix("next_")
                if argument_field not in properties:
                    continue
                continuations.append(
                    {
                        "skill_id": descriptor.skill_id,
                        "tool_id": descriptor.tool_id,
                        "argument_field": argument_field,
                        "argument_literal": "next",
                    }
                )
        unique = {
            (item["skill_id"], item["tool_id"], item["argument_field"]): item
            for item in continuations
        }
        if not unique:
            return None
        skill_ids = [
            str(item).strip().casefold()
            for item in selected_skill_ids[:3]
            if str(item).strip()
        ]
        return {
            "skill_ids": skill_ids,
            "continuations": list(unique.values())[:3],
        }

    @staticmethod
    def _content_free_continuation_step(
        *,
        marker: Any,
        selected_skill_ids: set[str],
        descriptors: Mapping[str, ToolDescriptor],
    ) -> dict[str, Any] | None:
        continuations = marker.get("continuations") if isinstance(marker, dict) else None
        candidates: list[dict[str, Any]] = []
        for item in continuations or []:
            if not isinstance(item, dict):
                continue
            skill_id = str(item.get("skill_id") or "").strip().casefold()
            tool_id = str(item.get("tool_id") or "").strip().casefold()
            argument_field = str(item.get("argument_field") or "").strip()
            argument_literal = str(item.get("argument_literal") or "").strip().casefold()
            descriptor = descriptors.get(tool_id)
            if (
                descriptor is None
                or descriptor.effect != "read"
                or skill_id not in selected_skill_ids
                or descriptor.skill_id != skill_id
                or argument_literal != "next"
            ):
                continue
            schema = thaw_json(descriptor.input_schema)
            properties = schema.get("properties") if isinstance(schema, dict) else None
            if not isinstance(properties, dict) or argument_field not in properties:
                continue
            candidates.append(
                {
                    "mode": "call_tool",
                    "tool_id": tool_id,
                    "call_id": f"session-continuation-{tool_id.replace('.', '-')}",
                    "arguments": {argument_field: argument_literal},
                    "provenance_claims": [
                        {
                            "kind": "request_derived",
                            "destination_pointer": f"/{argument_field}",
                            "derivation": "interpret",
                        }
                    ],
                }
            )
        return candidates[0] if len(candidates) == 1 else None

    def _model_context(
        self,
        context: dict[str, Any],
        *,
        correction: bool = False,
        pending: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        value = {
            "main_tool_execution_mode": self._mode,
            "schema_correction": bool(correction),
            "agent_id": context.get("agent_id"),
            "requested_by_user_id": context.get("requested_by_user_id"),
            "session_summary": context.get("session_summary"),
            "recent_turns": context.get("recent_turns"),
            "main_tool_followup": context.get("main_tool_followup"),
        }
        if isinstance(pending, dict):
            value["pending_tool_call"] = {
                "tool_id": pending.get("tool_id"),
                "present_fields": pending.get("present_fields"),
                "missing_fields": pending.get("missing_fields"),
                "requires_complete_resubmission": pending.get("persistence") == "no_store",
            }
        return value

    @staticmethod
    def _binding_hash(
        *,
        user_id: str,
        agent_id: str,
        source_interface: str,
        context: dict[str, Any],
    ) -> str:
        material = {
            "user_id": str(user_id),
            "agent_id": str(agent_id),
            "source_interface": str(source_interface),
            "channel_scope": str(
                context.get("discord_channel_id")
                or context.get("session_channel")
                or source_interface
            ),
        }
        return "pendingbind_v1_" + hashlib.sha256(
            canonical_json(material).encode("utf-8")
        ).hexdigest()

    def _deadline_reached(self, started: float) -> bool:
        return self._monotonic_clock() - started >= self._limits.timeout_seconds

    def _elapsed_ms(self, started: float) -> int:
        return max(0, int((self._monotonic_clock() - started) * 1000))

    def _limit_stop(self, *, steps: int, failures: int, started: float) -> dict[str, Any]:
        return self._outcome(
            status="safe_stop",
            message="I stopped because the bounded tool-evaluation limit was reached.",
            stop_reason="selection_limit",
            steps=steps,
            failures=failures,
            elapsed_ms=self._elapsed_ms(started),
        )

    def _partial_stop(
        self,
        *,
        reason: str,
        steps: int,
        failures: int,
        started: float,
        selected_skill_ids: list[str],
        tool_ids: list[str],
        observation_count: int,
        operation_ids: list[str],
        receipt_refs: list[str],
        policies: list[str],
        committed_effect_count: int,
        portions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        committed = max(0, int(committed_effect_count))
        completed = sum(1 for item in portions if item.get("state") == "completed")
        if completed:
            message = (
                f"I completed {completed} bounded tool call(s), then stopped safely. "
                "Any committed receipt references are included with this result."
            )
            status = "partial"
        else:
            message = "I could not complete that request safely within the bounded tool limits."
            status = "safe_stop"
        return self._outcome(
            status=status,
            message=message,
            stop_reason=reason,
            selected_skill_ids=selected_skill_ids,
            tool_ids=tool_ids,
            operation_ids=operation_ids,
            receipt_refs=receipt_refs,
            observation_count=observation_count,
            committed_effect_count=committed,
            persistence=self._most_restrictive_policy(policies),
            portions=portions,
            steps=steps,
            failures=failures,
            elapsed_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _call_portion(
        *,
        descriptor: ToolDescriptor,
        observation: ToolObservation,
    ) -> dict[str, Any]:
        if observation.status == "waiting_for_approval":
            state = "pending"
        elif observation.status == "queued" and not observation.committed_effect:
            state = "pending"
        elif observation.status == "denied":
            state = "denied"
        elif observation.status in {"terminal_error", "retryable_error", "needs_input"}:
            state = "failed"
        else:
            state = "completed"
        return {
            "state": state,
            "tool_id": descriptor.tool_id,
            "operation_id": observation.operation_id,
            "status": observation.status,
            "committed_effect": observation.committed_effect,
            "receipt_refs": list(observation.receipt_refs),
            "review_refs": list(observation.review_refs),
            "job_refs": list(observation.job_refs),
        }

    @staticmethod
    def _rejected_portion(
        *,
        descriptor: ToolDescriptor,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "state": "denied",
            "tool_id": descriptor.tool_id,
            "operation_id": "",
            "status": "rejected_before_operation",
            "committed_effect": False,
            "receipt_refs": [],
            "review_refs": [],
            "job_refs": [],
            "reason_code": str(reason or "tool_call_rejected")[:120],
        }

    @staticmethod
    def _most_restrictive_policy(values: list[str]) -> str:
        ranks = {
            "standard": 0,
            "sensitive_domain": 1,
            "redacted": 1,
            "restricted_read": 2,
            "ephemeral": 2,
            "no_store": 2,
        }
        return max(values or ["standard"], key=lambda value: ranks.get(value, 2))

    @staticmethod
    def _outcome(
        *,
        status: str,
        message: str,
        stop_reason: str,
        selected_skill_ids: list[str] | None = None,
        tool_ids: list[str] | None = None,
        operation_ids: list[str] | None = None,
        receipt_refs: list[str] | None = None,
        observation_count: int = 0,
        committed_effect_count: int = 0,
        would_call_count: int = 0,
        persistence: str = "standard",
        portions: list[dict[str, Any]] | None = None,
        question: str | None = None,
        missing_fields: list[str] | None = None,
        steps: int = 0,
        failures: int = 0,
        elapsed_ms: int = 0,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": status,
            "message": str(message)[:8_000],
            "stop_reason": stop_reason,
            "selected_skill_ids": list(selected_skill_ids or []),
            "tool_ids": list(tool_ids or []),
            "operation_ids": list(operation_ids or []),
            "receipt_refs": list(receipt_refs or []),
            "observation_count": max(0, int(observation_count)),
            "committed_effect_count": max(0, int(committed_effect_count)),
            "would_call_count": max(0, int(would_call_count)),
            "persistence": str(persistence or "standard"),
            "portions": {
                state: [
                    dict(item)
                    for item in (portions or [])
                    if item.get("state") == state
                ]
                for state in ("completed", "pending", "denied", "failed")
            },
            "steps": max(0, int(steps)),
            "failures": max(0, int(failures)),
            "elapsed_ms": max(0, int(elapsed_ms)),
        }
        if question:
            result["question"] = str(question)[:2_000]
        if missing_fields:
            result["missing_fields"] = [str(item) for item in missing_fields[:32]]
        return result
