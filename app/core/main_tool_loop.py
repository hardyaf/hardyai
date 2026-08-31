from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Protocol

from app.core.tool_loop_types import (
    ModelStep,
    RequestTemporalContext,
    SkillSelection,
    ToolLoopContractError,
    ToolObservation,
    validate_descriptor_payload,
)
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

        while steps < self._limits.max_steps and failures < self._limits.max_failures:
            if self._deadline_reached(started):
                return self._partial_stop(
                    reason="deadline_exceeded",
                    steps=steps,
                    failures=failures,
                    started=started,
                    operation_ids=operation_ids,
                    receipt_refs=receipt_refs,
                    policies=policies,
                    committed_effect_count=committed_effect_count,
                )
            steps += 1
            raw_step = self._model.next_tool_step(
                text,
                projections,
                [item.to_model_dict() for item in observations],
                {tool_id: value.to_dict() for tool_id, value in temporal_contexts.items()},
                self._model_context(context, correction=failures > 0),
            )
            try:
                step = ModelStep.from_mapping(
                    raw_step if isinstance(raw_step, Mapping) else {},
                    allowed_tool_ids=set(descriptors),
                )
            except ToolLoopContractError:
                failures += 1
                continue

            if step.mode == "respond":
                return self._outcome(
                    status="responded",
                    message=step.message or "Completed.",
                    stop_reason="model_responded",
                    selected_skill_ids=list(selection.selected_skill_ids),
                    tool_ids=list(descriptors),
                    operation_ids=operation_ids,
                    receipt_refs=receipt_refs,
                    observation_count=len(observations),
                    committed_effect_count=committed_effect_count,
                    persistence=self._most_restrictive_policy(policies),
                    steps=steps,
                    failures=failures,
                    elapsed_ms=self._elapsed_ms(started),
                )

            descriptor = descriptors[str(step.tool_id)]
            if not self._descriptor_unchanged(
                descriptor=descriptor,
                user_id=user_id,
                agent_id=agent_id,
            ):
                return self._partial_stop(
                    reason="tool_contract_changed",
                    steps=steps,
                    failures=failures,
                    started=started,
                    operation_ids=operation_ids,
                    receipt_refs=receipt_refs,
                    policies=policies,
                    committed_effect_count=committed_effect_count,
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
                self._validate_p3_provenance(
                    step=step,
                    text=text,
                    observations=observations,
                    destination_descriptor=descriptor,
                    observation_descriptors=observation_descriptors,
                )
            except ToolLoopContractError:
                failures += 1
                continue
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
                        operation_ids=operation_ids,
                        receipt_refs=receipt_refs,
                        policies=policies,
                        committed_effect_count=committed_effect_count,
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
            )
            observation = call_outcome.get("_observation")
            if not isinstance(observation, ToolObservation):
                return {key: value for key, value in call_outcome.items() if not key.startswith("_")}
            observation_chars = len(canonical_json(observation.to_model_dict()))
            total_observation_chars += observation_chars
            if (
                observation_chars > min(descriptor.max_observation_chars, self._limits.max_observation_chars)
                or total_observation_chars > self._limits.max_total_observation_chars
            ):
                return self._partial_stop(
                    reason="observation_limit",
                    steps=steps,
                    failures=failures,
                    started=started,
                    operation_ids=operation_ids,
                    receipt_refs=receipt_refs,
                    policies=policies,
                    committed_effect_count=committed_effect_count,
                )
            observations.append(observation)
            observation_descriptors[observation.observation_ref] = descriptor
            if descriptor.effect != "read" and (
                observation.committed_effect or observation.status == "ok"
            ):
                accepted_effectful[call_key] = observation
            if descriptor.effect != "read" and observation.committed_effect:
                committed_effect_count += 1
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
            operation_ids=operation_ids,
            receipt_refs=receipt_refs,
            policies=policies,
            committed_effect_count=committed_effect_count,
        )

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
    ) -> dict[str, Any]:
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
        )
        if observation.status == "denied":
            return self._outcome(
                status="denied",
                message=observation.safe_message,
                stop_reason="tool_denied",
                tool_ids=[descriptor.tool_id],
                operation_ids=operation_ids,
                receipt_refs=receipt_refs,
                observation_count=len(observations) + 1,
                committed_effect_count=sum(1 for item in observations if item.committed_effect),
                persistence=descriptor.persistence,
            )
        if observation.committed_effect or descriptor.effect == "read":
            operation_ids.append(operation_id)
        for ref in observation.receipt_refs:
            if ref not in receipt_refs:
                receipt_refs.append(ref)
        return {"_observation": observation}

    def _observation_from_result(
        self,
        *,
        descriptor: ToolDescriptor,
        operation_id: str,
        result: Any,
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
            descriptor.effect != "read" and status in {"ok", "queued"}
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
            untrusted=bool(result.get("untrusted", False)),
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
    ) -> None:
        observation_by_ref = {item.observation_ref: item for item in observations}
        descriptor_by_ref = dict(observation_descriptors or {})
        for claim in step.provenance_claims:
            if claim.get("kind") != "observation_derived":
                continue
            if destination_descriptor is None or str(claim.get("derivation") or "") != "copy":
                raise ToolLoopContractError("observation_transfer_not_available_until_p9")
            source_ref = str(claim.get("source_observation_ref") or "")
            source_observation = observation_by_ref.get(source_ref)
            source_descriptor = descriptor_by_ref.get(source_ref)
            if (
                source_observation is None
                or source_descriptor is None
                or source_observation.untrusted
                or source_descriptor.skill_id != destination_descriptor.skill_id
            ):
                raise ToolLoopContractError("observation_transfer_not_available_until_p9")
            source_pointer = str(claim.get("source_pointer") or "")
            if not any(
                field.scope == "same_domain"
                and MainToolLoop._pointer_pattern_matches(field.pattern, source_pointer)
                for field in source_descriptor.transferable_observation_fields
            ):
                raise ToolLoopContractError("observation_transfer_field_denied")
            source_found, source_value = MainToolLoop._pointer_value(
                thaw_json(source_observation.payload),
                source_pointer,
            )
            destination_found, destination_value = MainToolLoop._pointer_value(
                thaw_json(step.arguments or {}),
                str(claim.get("destination_pointer") or ""),
            )
            if (
                not source_found
                or not destination_found
                or canonical_json(source_value) != canonical_json(destination_value)
            ):
                raise ToolLoopContractError("observation_transfer_value_mismatch")
        request_claim_destinations = {
            str(claim.get("destination_pointer") or "")
            for claim in step.provenance_claims
            if claim.get("kind") == "request_derived"
        }
        claimed_destinations = {
            str(claim.get("destination_pointer") or "")
            for claim in step.provenance_claims
        }
        arguments = thaw_json(step.arguments or {})
        if any(
            not MainToolLoop._pointer_exists(arguments, pointer)
            for pointer in request_claim_destinations
        ):
            raise ToolLoopContractError("provenance_destination_missing")
        if not observations:
            return
        normalized_text = " ".join(str(text or "").casefold().split())
        for key, value in arguments.items():
            pointer = "/" + str(key).replace("~", "~0").replace("/", "~1")
            if pointer in claimed_destinations:
                continue
            if MainToolLoop._request_value_appears(value, normalized_text):
                continue
            raise ToolLoopContractError("argument_provenance_unproven")

    @staticmethod
    def _request_value_appears(value: Any, normalized_text: str) -> bool:
        """Verify explicit scalar or structured argument values against the request text."""

        if isinstance(value, str):
            token = " ".join(value.casefold().split())
            return bool(token and token in normalized_text)
        if isinstance(value, bool) or value is None:
            return False
        if isinstance(value, (int, float)):
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
    def _pointer_pattern_matches(pattern: str, pointer: str) -> bool:
        pattern_segments = str(pattern or "")[1:].split("/")
        pointer_segments = str(pointer or "")[1:].split("/")
        if len(pattern_segments) != len(pointer_segments):
            return False
        return all(
            expected == "*" or expected == observed
            for expected, observed in zip(pattern_segments, pointer_segments, strict=True)
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
        return {
            **dict(request_context),
            "requested_by_user_id": user_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "source_interface": source_interface,
            "source": source_interface,
            "session_id": str(getattr(session, "session_id", "") or ""),
        }

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
        operation_ids: list[str],
        receipt_refs: list[str],
        policies: list[str],
        committed_effect_count: int,
    ) -> dict[str, Any]:
        committed = max(0, int(committed_effect_count))
        if committed:
            message = (
                f"I completed {committed} bounded tool call(s), then stopped safely. "
                "The committed receipt references are included with this result."
            )
            status = "partial"
        else:
            message = "I could not complete that request safely within the bounded tool limits."
            status = "safe_stop"
        return self._outcome(
            status=status,
            message=message,
            stop_reason=reason,
            operation_ids=operation_ids,
            receipt_refs=receipt_refs,
            committed_effect_count=committed,
            persistence=self._most_restrictive_policy(policies),
            steps=steps,
            failures=failures,
            elapsed_ms=self._elapsed_ms(started),
        )

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
            "steps": max(0, int(steps)),
            "failures": max(0, int(failures)),
            "elapsed_ms": max(0, int(elapsed_ms)),
        }
        if question:
            result["question"] = str(question)[:2_000]
        if missing_fields:
            result["missing_fields"] = [str(item) for item in missing_fields[:32]]
        return result
