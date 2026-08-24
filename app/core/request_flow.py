from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.core.assistant_response import build_assistant_payload
from app.core.micro_jarvis import MicroDecision
from app.core.state_machine import next_state_for_owner_intent
from app.core.types import EMAIL_AGENT_INTENTS, FAST_COMMAND_INTENTS, Intent, SessionOwner, SessionState
from app.schemas.api import AskRequest


@dataclass(frozen=True)
class PreparedTurn:
    request_id: str
    effective_payload: AskRequest
    effective_context: dict[str, Any]
    raw_text: str
    active_agent_id: str
    session: Any
    wake_on_message: bool
    force_main_channel: bool
    channel_runtime_key: str | None


@dataclass(frozen=True)
class InterpretedTurn:
    working_context_payload: dict[str, Any]
    contextual_followup: dict[str, Any] | None
    decision: MicroDecision
    resolved_skill: dict[str, Any] | None


@dataclass(frozen=True)
class RoutedTurn:
    required_missing_fields: list[str]
    target_owner: SessionOwner
    classification_with_pipeline: dict[str, Any]


class RequestFlowCoordinator:
    """Own the top-level request state machine while policy ports remain independently testable."""

    def __init__(self, router_ports: Any) -> None:
        self._router = router_ports

    def route(self, payload: AskRequest) -> dict[str, Any]:
        turn = self._prepare_turn(payload)
        replay = self._replay_response(turn)
        if replay is not None:
            return replay
        guarded = self._handle_pre_decision_guards(turn)
        if guarded is not None:
            return guarded
        interpreted = self._interpret_turn(turn)
        guarded = self._handle_decision_guards(turn, interpreted)
        if guarded is not None:
            return guarded
        routed, response = self._prepare_routing(turn, interpreted)
        if response is not None:
            return response
        if routed is None:
            raise RuntimeError("routing_stage_did_not_resolve")
        response = self._dispatch_tool_lane(turn, interpreted, routed)
        if response is not None:
            return response
        return self._dispatch_main(turn, interpreted, routed)

    def _prepare_turn(self, payload: AskRequest) -> PreparedTurn:
        router = self._router
        request_id = str(
            payload.request_id
            or payload.context.get("request_id")
            or payload.context.get("external_message_id")
            or uuid4()
        ).strip()
        router._request_id_var.set(request_id)
        external_user_id = str(
            payload.context.get("external_user_id")
            or (payload.user_id if str(payload.source or "").strip().lower() == "discord" else "")
        ).strip()
        identity_binding = (
            router._identity_service.resolve(
                source=str(payload.source or ""),
                external_user_id=external_user_id,
            )
            if router._identity_service is not None and external_user_id
            else None
        )
        agent_context = router._resolve_agent_context(payload, identity_binding=identity_binding)
        normalized_text = str(agent_context.get("normalized_text") or payload.text or "").strip()
        if not normalized_text:
            normalized_text = payload.text
        effective_user_id = str(agent_context.get("resolved_user_id") or payload.user_id or "").strip() or "local_user"
        active_agent_id = str(agent_context.get("agent_id") or "jarvis").strip().lower() or "jarvis"
        raw_text = payload.text

        effective_context = dict(payload.context)
        effective_context["agent_id"] = active_agent_id
        effective_context["agent_display_name"] = str(agent_context.get("display_name") or "Jarvis")
        if identity_binding:
            effective_context.update(
                {
                    "external_user_id": external_user_id,
                    "external_display_name": payload.context.get("external_display_name"),
                    "identity_bound": True,
                    "age_band": identity_binding.get("age_band"),
                    "presentation_profile": identity_binding.get("presentation_profile") or "default",
                    "policy_profile": identity_binding.get("policy_profile") or "adult",
                    "is_child": bool(identity_binding.get("age_band"))
                    or str(identity_binding.get("policy_profile") or "").startswith("child_"),
                    "content_profile": "child"
                    if bool(identity_binding.get("age_band"))
                    or str(identity_binding.get("policy_profile") or "").startswith("child_")
                    else "adult",
                }
            )
        elif str(payload.source or "").strip().lower() == "discord":
            # Never trust a caller-supplied identity_bound flag. Only the
            # immutable identity repository may assert a Discord binding.
            effective_context["identity_bound"] = False
        wake_alias = agent_context.get("wake_alias")
        if isinstance(wake_alias, str) and wake_alias.strip():
            effective_context["wake_alias"] = wake_alias.strip().lower()

        effective_payload = AskRequest(
            text=normalized_text,
            request_id=request_id,
            session_id=payload.session_id,
            user_id=effective_user_id,
            source=payload.source,
            context=effective_context,
        )
        source_channel = str(effective_payload.source or "").strip().lower()
        force_main_channel = bool(effective_payload.context.get("force_main_owner")) or source_channel == "discord"
        wake_on_message = bool(effective_payload.context.get("wake_on_message")) or source_channel == "discord"

        channel_key = router._channel_key_for_payload(effective_payload)
        force_new_for_channel = bool(
            channel_key and router._micro_jarvis.looks_like_wake_command(effective_payload.text)
        )
        session = router._session_store.get_or_create(
            session_id=effective_payload.session_id,
            user_id=effective_payload.user_id,
            source=effective_payload.source,
            channel_key=channel_key,
            force_new_for_channel=force_new_for_channel,
        )
        session_context = dict(session.context_reference)
        if session_context.get("active_agent_id") != active_agent_id:
            session_context["active_agent_id"] = active_agent_id
        for identity_key in (
            "external_user_id",
            "external_display_name",
            "identity_bound",
            "age_band",
            "presentation_profile",
            "policy_profile",
            "is_child",
            "content_profile",
        ):
            if identity_key in effective_context:
                session_context[identity_key] = effective_context[identity_key]
        if channel_key:
            channel_status = router._session_store.channel_status(channel_key)
            if isinstance(channel_status, dict):
                session_context["channel_session"] = channel_status
        else:
            session_context.pop("channel_session", None)
        session.context_reference = session_context
        session.touch()
        router._session_store.save(session)
        channel_runtime = session_context.get("channel_session")
        channel_runtime_key = None
        if isinstance(channel_runtime, dict):
            raw_channel_key = channel_runtime.get("channel_key")
            if isinstance(raw_channel_key, str) and raw_channel_key.strip():
                channel_runtime_key = raw_channel_key.strip()
        router._event_log.record(
            event_type="input.received",
            session_id=session.session_id,
            payload={
                "source": effective_payload.source,
                "text": raw_text,
                "normalized_text": effective_payload.text,
                "state": session.state.value,
                "owner": session.owner.value,
                "agent_id": active_agent_id,
                "wake_alias": wake_alias,
                "channel_key": channel_runtime_key,
                "request_id": request_id,
                "identity_bound": bool(identity_binding),
            },
        )
        return PreparedTurn(
            request_id=request_id,
            effective_payload=effective_payload,
            effective_context=effective_context,
            raw_text=raw_text,
            active_agent_id=active_agent_id,
            session=session,
            wake_on_message=wake_on_message,
            force_main_channel=force_main_channel,
            channel_runtime_key=channel_runtime_key,
        )

    def _replay_response(self, turn: PreparedTurn) -> dict[str, Any] | None:
        router = self._router
        request_id = turn.request_id
        session = turn.session
        active_agent_id = turn.active_agent_id
        replay = (
            router._action_ticket_service.replay_response(request_id)
            if router._action_ticket_service is not None
            else None
        )
        if replay is not None:
            replay_result = dict(replay.get("result") or {})
            replay_dialog = dict(replay.get("dialog") or {})
            replay_classification = dict(replay.get("classification") or {})
            ticket = dict(replay.get("ticket") or {})
            replay_intent = str(ticket.get("intent") or Intent.UNKNOWN.value)
            assistant = build_assistant_payload(
                intent=replay_intent,
                route=str(ticket.get("route") or "idempotent_replay"),
                result=replay_result,
                dialog=replay_dialog,
                show_debug_labels=(
                    str(session.context_reference.get("presentation_profile") or "default").strip().lower()
                    not in {"child_simple", "minimal", "no_debug"}
                ),
            )
            persisted_text = str(replay.get("assistant_text") or "").strip()
            if persisted_text:
                assistant["text"] = persisted_text
            router._event_log.record(
                event_type="input.idempotent_replay",
                session_id=session.session_id,
                payload={"request_id": request_id, "ticket_id": ticket.get("ticket_id")},
            )
            return {
                "request_id": request_id,
                "ticket": {
                    "ticket_id": ticket.get("ticket_id"),
                    "status": ticket.get("status"),
                    "review_due_at": ticket.get("review_due_at"),
                    "root_ticket_id": ticket.get("root_ticket_id"),
                    "parent_ticket_id": ticket.get("parent_ticket_id"),
                },
                "session_id": session.session_id,
                "agent_id": str(ticket.get("agent_id") or active_agent_id),
                "source": session.source,
                "owner": session.owner.value,
                "state": session.state.value,
                "power_state": router._runtime_power.state.value,
                "session_runtime": {"last_activity_at": session.last_activity_timestamp},
                "intent": replay_intent,
                "classification": replay_classification,
                "route": str(ticket.get("route") or "idempotent_replay"),
                "result": replay_result,
                "dialog": replay_dialog,
                "assistant": assistant,
            }
        return None

    def _handle_pre_decision_guards(self, turn: PreparedTurn) -> dict[str, Any] | None:
        router = self._router
        wake_on_message = turn.wake_on_message
        channel_runtime_key = turn.channel_runtime_key
        effective_payload = turn.effective_payload
        effective_context = turn.effective_context
        session = turn.session
        raw_text = turn.raw_text
        if wake_on_message and not router._runtime_power.is_awake():
            router._runtime_power.wake()
            router._event_log.record(
                "runtime.wake",
                session.session_id,
                {
                    "reason": "channel_auto_wake",
                    "source": effective_payload.source,
                    "channel_key": channel_runtime_key,
                },
            )

        if not router._runtime_power.is_awake():
            if router._micro_jarvis.looks_like_wake_command(effective_payload.text):
                router._runtime_power.wake()
                router._event_log.record("runtime.wake", session.session_id, {"reason": "wake_phrase"})
                router._set_owner(session, SessionOwner.SYSTEM)
                router._set_state(session, SessionState.IDLE)
                return router._build_response(
                    session=session,
                    intent=Intent.SYSTEM_WAKE,
                    classification={
                        "intent": Intent.SYSTEM_WAKE.value,
                        "confidence": 0.99,
                        "entities": {},
                        "ambiguity_flags": [],
                        "recommended_owner": SessionOwner.SYSTEM.value,
                        "reasoning": "wake_phrase_while_asleep",
                    },
                    route="runtime_power",
                    result={"status": "awake", "message": "Jarvis is awake."},
                    request_text=raw_text,
                    user_id=effective_payload.user_id,
                )
            return router._build_response(
                session=session,
                intent=Intent.UNKNOWN,
                classification={
                    "intent": Intent.UNKNOWN.value,
                    "confidence": 0.0,
                    "entities": {},
                    "ambiguity_flags": ["sleep_guard"],
                    "recommended_owner": SessionOwner.SYSTEM.value,
                    "reasoning": "runtime_asleep",
                },
                route="sleep_guard",
                result={
                    "status": "sleeping",
                    "message": "Jarvis is asleep. Say `wake up` to continue.",
                },
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )

        if router._looks_like_exit_skill_phrase(effective_payload.text):
            pending = router._pending_clarification(session)
            cancelled_intent = str((pending or {}).get("intent") or "").strip() or None
            router._clear_pending_clarification(session)
            router._clear_main_sticky_followup(session)
            router._set_owner(session, SessionOwner.SYSTEM)
            router._set_state(session, SessionState.IDLE)
            classification = {
                "intent": Intent.CONVERSATIONAL.value,
                "confidence": 0.99,
                "entities": {},
                "ambiguity_flags": ["skill_context_exited"],
                "recommended_owner": SessionOwner.SYSTEM.value,
                "reasoning": "user_requested_skill_exit",
            }
            if cancelled_intent:
                classification["cancelled_intent"] = cancelled_intent
            result = {
                "status": "cancelled",
                "message": "Exited current skill context. I am listening.",
            }
            if cancelled_intent:
                result["cancelled_intent"] = cancelled_intent
            return router._build_response(
                session=session,
                intent=Intent.CONVERSATIONAL,
                classification=classification,
                route="session_control",
                result=result,
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )

        if router._child_plan_denied(effective_context) and router._pending_clarification(session) is not None:
            router._cancel_pending_interaction(
                session=session,
                reason="identity_policy_changed_or_conversation_only",
            )
            router._set_owner(session, SessionOwner.SYSTEM)
            router._set_state(session, SessionState.IDLE)
            return router._build_response(
                session=session,
                intent=Intent.UNKNOWN,
                classification={
                    "intent": Intent.UNKNOWN.value,
                    "confidence": 1.0,
                    "entities": {},
                    "ambiguity_flags": ["blocked_pending_action"],
                    "recommended_owner": SessionOwner.SYSTEM.value,
                    "reasoning": "identity_policy_denied_pending_action",
                },
                route="identity_policy",
                result={
                    "status": "policy_denied",
                    "message": "That unfinished household action is not available for this profile, so I cancelled it.",
                    "policy_profile": effective_context.get("policy_profile"),
                },
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )

        pending_response = router._handle_pending_clarification(payload=effective_payload, session=session)
        if pending_response is not None:
            return pending_response
        return None

    def _interpret_turn(self, turn: PreparedTurn) -> InterpretedTurn:
        router = self._router
        effective_payload = turn.effective_payload
        session = turn.session
        active_agent_id = turn.active_agent_id
        working_context_packet = router._build_working_context_packet(
            session=session,
            user_id=effective_payload.user_id,
            request_text=effective_payload.text,
            route_hint="micro_interpret",
            intent_hint=None,
        )
        working_context_payload = working_context_packet.to_dict()
        request_context_for_skills = {
            **effective_payload.context,
            "source_interface": effective_payload.source,
            "requested_by_user_id": effective_payload.user_id,
            "agent_id": effective_payload.context.get("agent_id") or "jarvis",
        }
        for contract in router._skill_context_contracts:
            enrich_hook = getattr(contract, "enrich_working_context", None)
            if not callable(enrich_hook):
                continue
            try:
                enriched = enrich_hook(
                    request_context=request_context_for_skills,
                    working_context=working_context_payload,
                )
                if isinstance(enriched, dict):
                    working_context_payload.update(enriched)
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                router._event_log.record(
                    event_type="context.contract.enrich_working_context.failed",
                    session_id=session.session_id,
                    payload={"contract_id": getattr(contract, "contract_id", "unknown"), "error": type(exc).__name__},
                )
        contextual_followup = router._infer_contextual_followup(
            text=effective_payload.text,
            working_context=working_context_payload,
        )
        micro_context = {
            "session_state": session.state.value,
            "session_owner": session.owner.value,
            "working_context": working_context_payload,
            **effective_payload.context,
        }
        if isinstance(contextual_followup, dict):
            micro_context["contextual_followup"] = contextual_followup

        micro_command_enabled = router._micro_command_enabled(effective_payload)
        if micro_command_enabled:
            decision = router._micro_jarvis.interpret(
                text=effective_payload.text,
                context=micro_context,
            )
        else:
            decision = MicroDecision(
                intent=Intent.UNKNOWN,
                confidence=0.0,
                entities={},
                ambiguity_flags=["micro_bypassed_unprefixed_discord"],
                recommended_owner=SessionOwner.MAIN,
                reasoning="discord_unprefixed_main_handoff",
            )
            router._event_log.record(
                event_type="pipeline.micro.bypassed",
                session_id=session.session_id,
                payload={
                    "reason": "discord_prefix_not_present",
                    "source": effective_payload.source,
                    "micro_command_explicit": False,
                    "target_owner": SessionOwner.MAIN.value,
                },
            )
        decision = router._resolve_followup_entities(session=session, decision=decision)
        decision = router._resolve_handoff_followup_entities(
            session=session,
            decision=decision,
            working_context=working_context_payload,
        )
        decision = router._normalize_decision_entities(decision)
        decision = router._apply_main_sticky_followup(session=session, decision=decision)
        conversation_lane = router._conversation_lane_policy.decide(
            text=effective_payload.text,
            intent=decision.intent,
            contextual_followup=contextual_followup if isinstance(contextual_followup, dict) else None,
        )
        if conversation_lane.route_to_conversation and decision.intent == Intent.UNKNOWN:
            ambiguity_flags = [
                str(flag)
                for flag in decision.ambiguity_flags
                if str(flag).strip().lower() not in {"unknown_intent", "model_only"}
            ]
            ambiguity_flags.append("conversation_lane_resolved")
            decision = MicroDecision(
                intent=Intent.CONVERSATIONAL,
                confidence=max(decision.confidence, conversation_lane.confidence),
                entities=dict(decision.entities),
                ambiguity_flags=ambiguity_flags,
                recommended_owner=SessionOwner.MAIN,
                reasoning=f"{decision.reasoning}_{conversation_lane.reason}",
            )
            router._event_log.record(
                event_type="pipeline.conversation_lane.resolved",
                session_id=session.session_id,
                payload={
                    "from_intent": Intent.UNKNOWN.value,
                    "to_intent": Intent.CONVERSATIONAL.value,
                    "reason": conversation_lane.reason,
                    "confidence": conversation_lane.confidence,
                },
            )
        resolved_skill = router._resolve_skill_for_intent(
            intent=decision.intent,
            user_id=effective_payload.user_id,
            agent_id=active_agent_id,
        )
        router._event_log.record(
            event_type="micro.decision",
            session_id=session.session_id,
            payload={
                **decision.to_dict(),
                "resolved_skill_id": str((resolved_skill or {}).get("skill_id") or ""),
                "agent_id": active_agent_id,
            },
        )
        return InterpretedTurn(
            working_context_payload=working_context_payload,
            contextual_followup=contextual_followup if isinstance(contextual_followup, dict) else None,
            decision=decision,
            resolved_skill=resolved_skill,
        )

    def _handle_decision_guards(
        self,
        turn: PreparedTurn,
        interpreted: InterpretedTurn,
    ) -> dict[str, Any] | None:
        router = self._router
        effective_payload = turn.effective_payload
        effective_context = turn.effective_context
        session = turn.session
        raw_text = turn.raw_text
        decision = interpreted.decision
        if router._child_action_denied(effective_context, decision.intent):
            router._set_owner(session, SessionOwner.SYSTEM)
            router._set_state(session, SessionState.IDLE)
            return router._build_response(
                session=session,
                intent=decision.intent,
                classification=decision.to_dict(),
                route="identity_policy",
                result={
                    "status": "policy_denied",
                    "message": router._CHILD_ACTION_DENIAL_MESSAGE,
                    "policy_profile": effective_context.get("policy_profile"),
                },
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )

        if decision.intent == Intent.SYSTEM_SLEEP:
            router._runtime_power.sleep()
            router._event_log.record("runtime.sleep", session.session_id, {"reason": "sleep_phrase"})
            router._clear_main_sticky_followup(session)
            router._set_owner(session, SessionOwner.SYSTEM)
            router._set_state(session, SessionState.IDLE)
            return router._build_response(
                session=session,
                intent=decision.intent,
                classification=decision.to_dict(),
                route="runtime_power",
                result={"status": "sleeping", "message": "Jarvis is going to sleep."},
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )

        if decision.intent == Intent.SYSTEM_WAKE:
            router._runtime_power.wake()
            router._event_log.record("runtime.wake", session.session_id, {"reason": "wake_phrase"})
            router._clear_main_sticky_followup(session)
            router._set_owner(session, SessionOwner.SYSTEM)
            router._set_state(session, SessionState.IDLE)
            return router._build_response(
                session=session,
                intent=decision.intent,
                classification=decision.to_dict(),
                route="runtime_power",
                result={"status": "awake", "message": "Jarvis is already awake."},
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )
        return None

    def _prepare_routing(
        self,
        turn: PreparedTurn,
        interpreted: InterpretedTurn,
    ) -> tuple[RoutedTurn | None, dict[str, Any] | None]:
        router = self._router
        request_id = turn.request_id
        effective_payload = turn.effective_payload
        session = turn.session
        raw_text = turn.raw_text
        active_agent_id = turn.active_agent_id
        force_main_channel = turn.force_main_channel
        working_context_payload = interpreted.working_context_payload
        contextual_followup = interpreted.contextual_followup
        decision = interpreted.decision
        resolved_skill = interpreted.resolved_skill
        required_missing_fields = router._required_fields_for_intent(
            intent=decision.intent,
            entities=decision.entities,
        )

        if router._action_ticket_service is not None:
            started = router._action_ticket_service.begin_request(
                request_id=request_id,
                session_id=session.session_id,
                context_reference=session.context_reference,
                user_id=effective_payload.user_id,
                agent_id=active_agent_id,
                source=effective_payload.source,
                intent=decision.intent.value,
                skill_id=str((resolved_skill or {}).get("skill_id") or "").strip() or None,
                route="micro_interpret",
                request_text=raw_text,
                classification=decision.to_dict(),
            )
            if started.context_reference != session.context_reference:
                session.context_reference = started.context_reference
                session.touch()
                router._session_store.save(session)

        repair_response = router._attempt_main_repair(
            payload=effective_payload,
            session=session,
            micro_decision=decision,
            required_missing_fields=required_missing_fields,
            working_context_payload=working_context_payload,
            contextual_followup=contextual_followup if isinstance(contextual_followup, dict) else None,
        )
        if repair_response is not None:
            return None, repair_response

        routing_decision = router._agent_routing_policy.decide(
            intent=decision.intent,
            recommended_owner=decision.recommended_owner,
            ambiguity_flags=list(decision.ambiguity_flags),
            missing_fields=required_missing_fields,
            force_main_channel=force_main_channel,
            skill=resolved_skill,
        )
        target_owner = routing_decision.owner
        decision.recommended_owner = target_owner
        if routing_decision.channel_forced_main and "channel_force_main_owner" not in decision.ambiguity_flags:
            decision.ambiguity_flags.append("channel_force_main_owner")
        if routing_decision.micro_contract_escalation:
            if "micro_contract_escalation" not in decision.ambiguity_flags:
                decision.ambiguity_flags.append("micro_contract_escalation")
            router._event_log.record(
                event_type="micro.execution.blocked_by_skill_contract",
                session_id=session.session_id,
                payload={
                    "intent": decision.intent.value,
                    "skill_id": str((resolved_skill or {}).get("skill_id") or ""),
                    "reason": "micro_not_allowed_for_intent",
                },
            )
        router._event_log.record(
            event_type="pipeline.routing.decided",
            session_id=session.session_id,
            payload={
                "intent": decision.intent.value,
                "owner": target_owner.value,
                "request_classification": routing_decision.pipeline.request_classification.value,
                "execution_path": routing_decision.pipeline.execution_path.value,
                "requires_validation": routing_decision.pipeline.requires_validation,
                "reasons": routing_decision.reasons,
            },
        )
        classification_with_pipeline = router._with_pipeline_metadata(
            classification=decision.to_dict(),
            pipeline=routing_decision.pipeline,
            routing_reasons=routing_decision.reasons,
        )
        router._set_owner(session, target_owner)
        router._set_state(session, next_state_for_owner_intent(target_owner, decision.intent))
        return (
            RoutedTurn(
                required_missing_fields=required_missing_fields,
                target_owner=target_owner,
                classification_with_pipeline=classification_with_pipeline,
            ),
            None,
        )

    def _dispatch_tool_lane(
        self,
        turn: PreparedTurn,
        interpreted: InterpretedTurn,
        routed: RoutedTurn,
    ) -> dict[str, Any] | None:
        router = self._router
        effective_payload = turn.effective_payload
        session = turn.session
        raw_text = turn.raw_text
        active_agent_id = turn.active_agent_id
        decision = interpreted.decision
        resolved_skill = interpreted.resolved_skill
        target_owner = routed.target_owner
        classification_with_pipeline = routed.classification_with_pipeline
        if target_owner == SessionOwner.MICRO and decision.intent in FAST_COMMAND_INTENTS:
            tool_result = router._execute_fast_command(
                decision=decision,
                source_interface=effective_payload.source,
                requested_by_user_id=effective_payload.user_id,
                resolved_skill=resolved_skill,
                agent_id=active_agent_id,
                request_context=effective_payload.context,
            )
            router._event_log.record(
                event_type="tool.executed",
                session_id=session.session_id,
                payload={
                    "intent": decision.intent.value,
                    "result_status": tool_result.get("status"),
                },
            )
            followup_response = router._maybe_open_tool_followup(
                session=session,
                decision=decision,
                tool_result=tool_result,
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )
            if followup_response is not None:
                return followup_response
            router._clear_pending_clarification(session)
            router._set_state(session, SessionState.IDLE)
            return router._build_response(
                session=session,
                intent=decision.intent,
                classification=classification_with_pipeline,
                route="micro_tool",
                result=tool_result,
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )

        if target_owner == SessionOwner.MAIN and decision.intent in EMAIL_AGENT_INTENTS:
            tool_result = router._execute_fast_command(
                decision=decision,
                source_interface=effective_payload.source,
                requested_by_user_id=effective_payload.user_id,
                resolved_skill=resolved_skill,
                agent_id=active_agent_id,
                request_context=effective_payload.context,
            )
            router._event_log.record(
                event_type="tool.executed",
                session_id=session.session_id,
                payload={
                    "intent": decision.intent.value,
                    "result_status": tool_result.get("status"),
                    "sensitive_domain": "email",
                },
            )
            router._clear_pending_clarification(session)
            router._set_state(session, SessionState.IDLE)
            return router._build_response(
                session=session,
                intent=decision.intent,
                classification=classification_with_pipeline,
                route="main_skill",
                result=tool_result,
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )
        return None

    def _dispatch_main(
        self,
        turn: PreparedTurn,
        interpreted: InterpretedTurn,
        routed: RoutedTurn,
    ) -> dict[str, Any]:
        router = self._router
        request_id = turn.request_id
        effective_payload = turn.effective_payload
        effective_context = turn.effective_context
        session = turn.session
        raw_text = turn.raw_text
        active_agent_id = turn.active_agent_id
        working_context_payload = interpreted.working_context_payload
        contextual_followup = interpreted.contextual_followup
        decision = interpreted.decision
        resolved_skill = interpreted.resolved_skill
        required_missing_fields = routed.required_missing_fields
        classification_with_pipeline = routed.classification_with_pipeline
        main_request_text = effective_payload.text
        if decision.intent in {Intent.UNKNOWN, Intent.CONVERSATIONAL} and isinstance(contextual_followup, dict):
            rewritten_text = str(contextual_followup.get("rewritten_user_text") or "").strip()
            if rewritten_text:
                main_request_text = rewritten_text

        runtime_capability_catalog = router._runtime_capability_catalog(
            payload=effective_payload,
            agent_id=active_agent_id,
        )
        main_context = {
            "micro_intent": decision.intent.value,
            "micro_confidence": decision.confidence,
            "micro_entities": decision.entities,
            "micro_ambiguity_flags": decision.ambiguity_flags,
            "required_missing_fields": required_missing_fields,
            "runtime_skill_intents": [decision.intent.value],
            "runtime_capability_catalog": runtime_capability_catalog,
            "working_context": working_context_payload,
            "session_summary": working_context_payload.get("session_summary"),
            "recent_turns": working_context_payload.get("recent_turns"),
            "entity_hints": working_context_payload.get("entity_hints"),
            "pending_interaction": working_context_payload.get("pending_interaction"),
            "budget_metadata": working_context_payload.get("budget_metadata"),
            "agent_id": active_agent_id,
            "agent_display_name": effective_context.get("agent_display_name"),
            "requested_by_user_id": effective_payload.user_id,
            "is_child": bool(effective_context.get("is_child")),
            "age_band": effective_context.get("age_band"),
            "content_profile": effective_context.get("content_profile"),
            "policy_profile": effective_context.get("policy_profile"),
            "presentation_profile": effective_context.get("presentation_profile"),
        }
        if isinstance(contextual_followup, dict):
            main_context["contextual_followup"] = contextual_followup

        response = router._main_jarvis.respond(
            text=main_request_text,
            context=main_context,
        )
        commitment_response = router._handle_main_turn_commitment(
            response=response,
            payload=effective_payload,
            session=session,
            effective_context=effective_context,
            runtime_capability_catalog=runtime_capability_catalog,
            request_text=raw_text,
        )
        if commitment_response is not None:
            return commitment_response
        plan = response.get("plan")
        if isinstance(plan, dict) and router._child_plan_denied(effective_context):
            response = {
                "status": "policy_denied",
                "message": router._CHILD_ACTION_DENIAL_MESSAGE,
                "policy_profile": effective_context.get("policy_profile"),
            }
            router._set_state(session, SessionState.IDLE)
        elif isinstance(plan, dict):
            if router._action_ticket_service is not None:
                started = router._action_ticket_service.begin_request(
                    request_id=request_id,
                    session_id=session.session_id,
                    context_reference=session.context_reference,
                    user_id=effective_payload.user_id,
                    agent_id=active_agent_id,
                    source=effective_payload.source,
                    intent=decision.intent.value,
                    skill_id=str((resolved_skill or {}).get("skill_id") or "").strip() or None,
                    route="main_jarvis",
                    request_text=raw_text,
                    classification=classification_with_pipeline,
                    force=True,
                )
                if started.context_reference != session.context_reference:
                    session.context_reference = started.context_reference
                    session.touch()
                    router._session_store.save(session)
            router._event_log.record(
                event_type="main.plan.generated",
                session_id=session.session_id,
                payload={
                    "plan_type": plan.get("plan_type"),
                    "scope": plan.get("scope"),
                    "confidence": plan.get("confidence"),
                    "command_count": len(plan.get("commands") or []),
                },
            )
            execution = router._execute_main_plan(
                plan=plan,
                session=session,
                session_id=session.session_id,
                source_interface=effective_payload.source,
                requested_by_user_id=effective_payload.user_id,
                agent_id=active_agent_id,
                goal_text=main_request_text,
                request_context={
                    **effective_payload.context,
                    "working_context": working_context_payload,
                    "session_summary": working_context_payload.get("session_summary"),
                    "recent_turns": working_context_payload.get("recent_turns"),
                    "entity_hints": working_context_payload.get("entity_hints"),
                    "pending_interaction": working_context_payload.get("pending_interaction"),
                    "budget_metadata": working_context_payload.get("budget_metadata"),
                    "contextual_followup": contextual_followup if isinstance(contextual_followup, dict) else None,
                },
            )
            response["execution"] = execution
            if execution.get("status") in {"ok", "partial"}:
                response["status"] = "executed"
                success_message = str(response.get("success_message") or "").strip()
                if execution.get("status") == "ok" and success_message:
                    response["message"] = success_message
                router._clear_pending_clarification(session)
            router._set_state(session, SessionState.IDLE)
        elif decision.intent in FAST_COMMAND_INTENTS:
            missing_fields = router._required_fields_for_intent(intent=decision.intent, entities=decision.entities)
            if not missing_fields:
                tool_result = router._execute_fast_command(
                    decision=decision,
                    source_interface=effective_payload.source,
                    requested_by_user_id=effective_payload.user_id,
                    resolved_skill=resolved_skill,
                    agent_id=active_agent_id,
                )
                router._event_log.record(
                    event_type="main.fast_fallback.executed",
                    session_id=session.session_id,
                    payload={
                        "intent": decision.intent.value,
                        "result_status": tool_result.get("status"),
                    },
                )
                response = dict(tool_result)
                response["executed_by"] = "main_fast_fallback"
                router._clear_pending_clarification(session)
                router._set_state(session, SessionState.IDLE)
        classification_with_pipeline, response = router._maybe_open_conversation_followup(
            session=session,
            decision=decision,
            classification=classification_with_pipeline,
            response=response,
            request_text=raw_text,
            working_context_payload=working_context_payload,
        )
        router._event_log.record(
            event_type="response.generated",
            session_id=session.session_id,
            payload={"route": "main_jarvis", "status": response.get("status")},
        )
        return router._build_response(
            session=session,
            intent=decision.intent,
            classification=classification_with_pipeline,
            route="main_jarvis",
            result=response,
            request_text=raw_text,
            user_id=effective_payload.user_id,
        )
