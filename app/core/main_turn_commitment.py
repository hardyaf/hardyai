from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from app.core.action_execution import ActionExecutionService
from app.core.domain_context import DomainContextService
from app.core.main_turn_contract import normalize_main_turn_decision
from app.core.micro_jarvis import MicroDecision
from app.core.pending_interaction import PendingInteractionCoordinator
from app.core.persistence_policy import persistence_policy
from app.core.session_store import SessionRecord, SessionStore
from app.core.session_transitions import SessionTransitionService
from app.core.turn_finalizer import TurnFinalizationOptions, TurnFinalizer
from app.core.types import MAIN_ACTION_INTENTS, Intent, SessionOwner, SessionState
from app.schemas.api import AskRequest
from app.services.event_log import EventLogService


ToolFollowup = Callable[..., dict[str, Any] | None]
RequestDecisionBinder = Callable[..., MicroDecision]


class MainTurnCommitmentCoordinator:
    """Validates and commits Main's single typed action decision."""

    def __init__(
        self,
        *,
        domain_context: DomainContextService,
        pending_interactions: PendingInteractionCoordinator,
        session_transitions: SessionTransitionService,
        action_execution: ActionExecutionService,
        turn_finalizer: TurnFinalizer,
        session_store: SessionStore,
        event_log: EventLogService,
        action_ticket_service: Any | None,
        low_confidence_floor: float,
        child_action_denial_message: str,
        main_tool_loop: Any | None = None,
        main_tool_model: Any | None = None,
        main_tool_execution_mode: str = "off",
    ) -> None:
        self._domain_context = domain_context
        self._pending_interactions = pending_interactions
        self._session_transitions = session_transitions
        self._action_execution = action_execution
        self._turn_finalizer = turn_finalizer
        self._session_store = session_store
        self._event_log = event_log
        self._action_ticket_service = action_ticket_service
        self._low_confidence_floor = low_confidence_floor
        self._child_action_denial_message = child_action_denial_message
        self._main_tool_loop = main_tool_loop
        self._main_tool_model = main_tool_model
        self._main_tool_execution_mode = str(main_tool_execution_mode or "off").strip().casefold()

    def evaluate_new_path(
        self,
        *,
        payload: AskRequest,
        session: SessionRecord,
        effective_context: dict[str, Any],
        request_text: str,
        request_id: str,
        shadow: bool,
    ) -> dict[str, Any] | None:
        """Run the generic commitment/loop seam without a legacy intent gate."""

        mode = "shadow" if shadow else "active"
        decide_turn = getattr(self._main_tool_model, "decide_turn", None)
        if self._main_tool_execution_mode != mode or not callable(decide_turn):
            if shadow:
                return None
            return self._finalize_new_path(
                payload=payload,
                session=session,
                request_id=request_id,
                result={
                    "status": "safe_stop",
                    "message": "I could not safely evaluate that action right now.",
                    "stop_reason": "main_tool_model_unavailable",
                    "persistence": "standard",
                },
                commitment=None,
            )
        raw = decide_turn(
            request_text,
            {
                "main_tool_execution_mode": mode,
                "agent_id": effective_context.get("agent_id"),
                "requested_by_user_id": payload.user_id,
                "session_summary": session.context_reference.get("session_summary"),
                "recent_turns": session.context_reference.get("recent_turns"),
            },
        )
        commitment = normalize_main_turn_decision(
            raw if isinstance(raw, dict) else None,
            execution_mode=mode,
        )
        if commitment is None:
            self._record_new_path_commitment(
                session=session,
                mode=mode,
                status="invalid",
                reason_code=None,
            )
            if shadow:
                return None
            return self._finalize_new_path(
                payload=payload,
                session=session,
                request_id=request_id,
                result={
                    "status": "safe_stop",
                    "message": "I could not safely determine whether that request should use a capability.",
                    "stop_reason": "invalid_main_action_commitment",
                    "persistence": "standard",
                },
                commitment=None,
            )
        self._record_new_path_commitment(
            session=session,
            mode=mode,
            status=str(commitment.get("mode") or "unknown"),
            reason_code=str(commitment.get("reason_code") or "") or None,
        )
        if commitment["mode"] == "conversation":
            if shadow:
                return None
            return self._finalize_new_path(
                payload=payload,
                session=session,
                request_id=request_id,
                result={
                    "status": "conversation",
                    "message": str(commitment.get("message") or ""),
                    "stop_reason": "generic_commitment_conversation",
                    "persistence": "standard",
                },
                commitment=commitment,
                intent=Intent.CONVERSATIONAL,
            )
        if commitment["mode"] == "clarify_action":
            if shadow:
                return None
            question = (
                "Please restate the complete goal, including the object and outcome you want."
            )
            if self._main_tool_loop is None:
                return self._finalize_new_path(
                    payload=payload,
                    session=session,
                    request_id=request_id,
                    result={
                        "status": "safe_stop",
                        "message": "I could not safely preserve that clarification.",
                        "stop_reason": "main_tool_loop_unavailable",
                        "persistence": "no_store",
                    },
                    commitment=commitment,
                )
            binding_hash = self._main_tool_loop.binding_hash(
                user_id=payload.user_id,
                agent_id=str(effective_context.get("agent_id") or "jarvis"),
                source_interface=payload.source,
                request_context=payload.context,
            )
            self._pending_interactions.store_generic_action_clarification(
                session=session,
                question=question,
                root_request_id=request_id,
                binding_hash=binding_hash,
            )
            self._session_transitions.arm_main_followup(
                session=session,
                reason="main_action_clarification_v2",
            )
            self._session_transitions.set_owner(session=session, owner=SessionOwner.MAIN)
            self._session_transitions.set_state(
                session=session,
                state=SessionState.AWAITING_CONFIRMATION,
            )
            return self._finalize_new_path(
                payload=payload,
                session=session,
                request_id=request_id,
                result={
                    "status": "needs_clarification",
                    "message": question,
                    "question": question,
                    "missing_fields": ["complete_goal"],
                    "stop_reason": "generic_commitment_clarification",
                    "persistence": "no_store",
                },
                commitment=commitment,
            )

        if self._main_tool_loop is None:
            if shadow:
                return None
            return self._finalize_new_path(
                payload=payload,
                session=session,
                request_id=request_id,
                result={
                    "status": "safe_stop",
                    "message": "I could not safely evaluate that capability request.",
                    "stop_reason": "main_tool_loop_unavailable",
                    "persistence": "standard",
                },
                commitment=commitment,
            )
        outcome = self._main_tool_loop.run(
            text=request_text,
            request_id=request_id,
            session=session,
            user_id=payload.user_id,
            agent_id=str(effective_context.get("agent_id") or "jarvis"),
            source_interface=payload.source,
            request_context={
                **payload.context,
                "session_summary": session.context_reference.get("session_summary"),
                "recent_turns": session.context_reference.get("recent_turns"),
            },
        )
        self._record_new_path_outcome(session=session, mode=mode, outcome=outcome)
        if shadow:
            return None
        return self._finalize_new_path(
            payload=payload,
            session=session,
            request_id=request_id,
            result=outcome,
            commitment=commitment,
        )

    def resume_pending_tool(
        self,
        *,
        payload: AskRequest,
        session: SessionRecord,
        pending: dict[str, Any],
        effective_context: dict[str, Any],
        request_text: str,
        request_id: str,
    ) -> dict[str, Any] | None:
        if self._main_tool_execution_mode != "active" or self._main_tool_loop is None:
            return None
        outcome = self._main_tool_loop.resume(
            text=request_text,
            pending=pending,
            session=session,
            user_id=payload.user_id,
            agent_id=str(effective_context.get("agent_id") or "jarvis"),
            source_interface=payload.source,
            request_context=payload.context,
        )
        return self._finalize_new_path(
            payload=payload,
            session=session,
            request_id=request_id,
            result=outcome,
            commitment={
                "mode": "execute_action",
                "confidence": 1.0,
                "reason_code": "plausible_action",
            },
        )

    def _finalize_new_path(
        self,
        *,
        payload: AskRequest,
        session: SessionRecord,
        request_id: str,
        result: dict[str, Any],
        commitment: dict[str, Any] | None,
        intent: Intent = Intent.UNKNOWN,
    ) -> dict[str, Any]:
        status = str(result.get("status") or "safe_stop").strip().casefold()
        if status in {"needs_clarification", "needs_input"}:
            self._session_transitions.set_owner(session=session, owner=SessionOwner.MAIN)
            self._session_transitions.set_state(
                session=session,
                state=SessionState.AWAITING_CONFIRMATION,
            )
        else:
            self._session_transitions.set_owner(session=session, owner=SessionOwner.MAIN)
            self._session_transitions.set_state(session=session, state=SessionState.IDLE)
        policy = persistence_policy(
            result.get("persistence"),
            canonicalize_legacy_aliases=True,
        )
        classification = {
            "intent": intent.value,
            "confidence": float((commitment or {}).get("confidence") or 0.0),
            "entities": {},
            "ambiguity_flags": ["main_tool_loop"],
            "recommended_owner": SessionOwner.MAIN.value,
            "reasoning": "main_action_commitment_v2",
            "commitment_mode": (commitment or {}).get("mode"),
            "reason_code": (commitment or {}).get("reason_code"),
        }
        return self._turn_finalizer.build_response(
            request_id=request_id,
            session=session,
            intent=intent,
            classification=classification,
            route="main_tool_loop",
            result={**dict(result), "_persistence_policy": policy.name.value},
            request_text=payload.text,
            user_id=payload.user_id,
            options=TurnFinalizationOptions(
                record_context_history=policy.record_entity_context,
                record_recent_turns=policy.record_recent_turns,
                record_conversation_history=policy.record_conversation_history,
                record_memory=policy.record_memory,
                capture_ticket=False,
                persistence_policy=policy,
            ),
        )

    def _record_new_path_commitment(
        self,
        *,
        session: SessionRecord,
        mode: str,
        status: str,
        reason_code: str | None,
    ) -> None:
        self._event_log.record(
            event_type=f"main.action.commitment.{mode}.evaluated",
            session_id=session.session_id,
            payload={
                "status": status,
                "reason_code": reason_code,
            },
        )

    def _record_new_path_outcome(
        self,
        *,
        session: SessionRecord,
        mode: str,
        outcome: dict[str, Any],
    ) -> None:
        self._event_log.record(
            event_type=f"main.action.loop.{mode}.evaluated",
            session_id=session.session_id,
            payload={
                "status": outcome.get("status"),
                "stop_reason": outcome.get("stop_reason"),
                "selected_skill_ids": list(outcome.get("selected_skill_ids") or [])[:3],
                "tool_ids": list(outcome.get("tool_ids") or [])[:16],
                "operation_ids": list(outcome.get("operation_ids") or [])[:16],
                "receipt_refs": list(outcome.get("receipt_refs") or [])[:16],
                "observation_count": int(outcome.get("observation_count") or 0),
                "committed_effect_count": int(outcome.get("committed_effect_count") or 0),
                "would_call_count": int(outcome.get("would_call_count") or 0),
                "steps": int(outcome.get("steps") or 0),
                "failures": int(outcome.get("failures") or 0),
                "elapsed_ms": int(outcome.get("elapsed_ms") or 0),
            },
        )

    def handle(
        self,
        *,
        response: dict[str, Any],
        payload: AskRequest,
        session: SessionRecord,
        effective_context: dict[str, Any],
        runtime_capability_catalog: list[dict[str, Any]],
        request_text: str,
        request_id: str | None,
        open_tool_followup: ToolFollowup,
        bind_request_decision: RequestDecisionBinder,
    ) -> dict[str, Any] | None:
        if str(response.get("status") or "").strip().lower() != "main_turn_decision":
            return None
        turn_decision = response.get("turn_decision")
        if not isinstance(turn_decision, dict):
            return None

        intent = self._coerce_intent(str(turn_decision.get("intent") or ""))
        mode = str(turn_decision.get("mode") or "").strip().lower()
        if intent is None or intent not in MAIN_ACTION_INTENTS or mode not in {"clarify_action", "execute_action"}:
            return None

        entities_raw = turn_decision.get("entities")
        entities = self._domain_context.normalize_entities(
            intent=intent,
            entities=dict(entities_raw) if isinstance(entities_raw, dict) else {},
        )
        confidence_raw = turn_decision.get("confidence")
        confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) else 0.0
        reasoning = str(turn_decision.get("reasoning") or "main_turn_commitment").strip()
        original_intent = intent
        committed = bind_request_decision(
            session=session,
            decision=MicroDecision(
                intent=intent,
                confidence=max(0.0, min(confidence, 1.0)),
                entities=entities,
                ambiguity_flags=["main_turn_commitment"],
                recommended_owner=SessionOwner.MAIN,
                reasoning=reasoning,
            ),
            request_context=payload.context,
            working_context=dict(session.context_reference),
            text=request_text,
        )
        intent = committed.intent
        entities = self._domain_context.normalize_entities(
            intent=intent,
            entities=dict(committed.entities),
        )
        committed.entities = entities
        confidence = committed.confidence

        capability = self._capability_for_intent(
            intent=intent,
            runtime_capability_catalog=runtime_capability_catalog,
        )
        eligible_intents = {
            str(item or "").strip().casefold()
            for item in (capability or {}).get("main_intents") or []
            if str(item or "").strip()
        }
        configured = (capability or {}).get("configured") is True
        authorized_here = (capability or {}).get("authorized_here") is True
        if intent.value not in eligible_intents or not configured or not authorized_here:
            return self._deny_unavailable(
                intent=intent,
                mode=mode,
                turn_decision=turn_decision,
                capability=capability,
                configured=configured,
                authorized_here=authorized_here,
                catalog_eligible=intent.value in eligible_intents,
                payload=payload,
                session=session,
                request_text=request_text,
                request_id=request_id,
            )

        if self._child_action_denied(effective_context, intent):
            self._session_transitions.set_owner(session=session, owner=SessionOwner.SYSTEM)
            self._session_transitions.set_state(session=session, state=SessionState.IDLE)
            return self._finalize(
                request_id=request_id,
                session=session,
                intent=intent,
                classification={
                    "intent": intent.value,
                    "confidence": 1.0,
                    "entities": {},
                    "ambiguity_flags": ["blocked_main_turn_commitment"],
                    "recommended_owner": SessionOwner.SYSTEM.value,
                    "reasoning": "identity_policy_denied_main_turn_commitment",
                },
                route="identity_policy",
                result={
                    "status": "policy_denied",
                    "message": self._child_action_denial_message,
                    "policy_profile": effective_context.get("policy_profile"),
                },
                request_text=request_text,
                user_id=payload.user_id,
            )

        missing_fields = self._domain_context.required_fields(intent=intent, entities=entities)
        decision_missing = turn_decision.get("missing_fields")
        if intent == original_intent and isinstance(decision_missing, list):
            missing_fields = self._domain_context.merge_missing_fields(
                missing_fields,
                [
                    str(field_name)
                    for field_name in decision_missing
                    if str(field_name).strip()
                    and not str(entities.get(str(field_name)) or "").strip()
                ],
            )
        if (
            "trusted_discord_attachment_binding" in committed.ambiguity_flags
            and not missing_fields
        ):
            mode = "execute_action"
        agent_id = self._session_transitions.active_agent_id(session)
        resolved_skill = self._action_execution.resolve(
            intent=intent.value,
            user_id=payload.user_id,
            agent_id=agent_id,
        )

        if mode == "clarify_action" or missing_fields:
            return self._open_clarification(
                request_id=request_id,
                session=session,
                payload=payload,
                request_text=request_text,
                turn_decision=turn_decision,
                intent=intent,
                entities=entities,
                missing_fields=missing_fields,
                confidence=confidence,
                reasoning=reasoning,
                resolved_skill=resolved_skill,
            )

        if committed.confidence < self._low_confidence_floor:
            self._session_transitions.set_owner(session=session, owner=SessionOwner.MAIN)
            self._session_transitions.set_state(session=session, state=SessionState.CONVERSATIONAL)
            return self._finalize(
                request_id=request_id,
                session=session,
                intent=intent,
                classification={**committed.to_dict(), "commitment_mode": "execute_action"},
                route="main_jarvis_commitment",
                result={
                    "status": "needs_clarification",
                    "message": "I understand the likely action, but I am not confident enough to run it.",
                    "question": "Can you restate the action and the scope you want?",
                    "missing_fields": [],
                    "commitment_mode": "execute_action",
                },
                request_text=request_text,
                user_id=payload.user_id,
            )

        classification = {**committed.to_dict(), "commitment_mode": "execute_action"}
        if self._action_ticket_service is not None:
            started = self._action_ticket_service.begin_request(
                request_id=str(request_id or payload.request_id or uuid4()),
                session_id=session.session_id,
                context_reference=session.context_reference,
                user_id=payload.user_id,
                agent_id=agent_id,
                source=payload.source,
                intent=intent.value,
                skill_id=str((resolved_skill or {}).get("skill_id") or "").strip() or None,
                route="main_jarvis_commitment",
                request_text=request_text,
                classification=classification,
                force=True,
            )
            if started.context_reference != session.context_reference:
                session.context_reference = started.context_reference
                session.touch()
                self._session_store.save(session)

        self._session_transitions.set_owner(session=session, owner=SessionOwner.MAIN)
        self._session_transitions.set_state(session=session, state=SessionState.ERROR_RECOVERY)
        tool_result = self._action_execution.execute(
            intent=committed.intent.value,
            entities=committed.entities,
            source_interface=payload.source,
            requested_by_user_id=payload.user_id,
            resolved_skill=resolved_skill,
            agent_id=agent_id,
            request_id=request_id,
            request_context=payload.context,
        )
        self._event_log.record(
            event_type="main.action.commitment.executed",
            session_id=session.session_id,
            payload={"intent": intent.value, "result_status": tool_result.get("status")},
        )
        followup_response = open_tool_followup(
            session=session,
            decision=committed,
            tool_result=tool_result,
            request_text=request_text,
            user_id=payload.user_id,
        )
        if followup_response is not None:
            return followup_response
        self._pending_interactions.clear(
            session=session,
            reason="router._clear_pending_clarification",
        )
        self._session_transitions.set_state(session=session, state=SessionState.IDLE)
        result = dict(tool_result)
        result["committed_by"] = "main_turn_decision"
        return self._finalize(
            request_id=request_id,
            session=session,
            intent=intent,
            classification=classification,
            route="main_jarvis_commitment",
            result=result,
            request_text=request_text,
            user_id=payload.user_id,
        )

    def _deny_unavailable(
        self,
        *,
        intent: Intent,
        mode: str,
        turn_decision: dict[str, Any],
        capability: dict[str, Any] | None,
        configured: bool,
        authorized_here: bool,
        catalog_eligible: bool,
        payload: AskRequest,
        session: SessionRecord,
        request_text: str,
        request_id: str | None,
    ) -> dict[str, Any]:
        message = str((capability or {}).get("access_note") or "").strip()
        message = message or "That action is not currently configured and authorized in this context."
        self._session_transitions.set_owner(session=session, owner=SessionOwner.MAIN)
        self._session_transitions.set_state(session=session, state=SessionState.IDLE)
        classification = {
            "intent": intent.value,
            "confidence": float(turn_decision.get("confidence") or 0.0),
            "entities": {},
            "ambiguity_flags": ["main_turn_commitment_scope_denied"],
            "recommended_owner": SessionOwner.MAIN.value,
            "reasoning": "main_turn_commitment_scope_denied",
            "commitment_mode": mode,
        }
        self._event_log.record(
            event_type="main.action.commitment.denied",
            session_id=session.session_id,
            payload={
                "intent": intent.value,
                "configured": configured,
                "authorized_here": authorized_here,
                "catalog_eligible": catalog_eligible,
            },
        )
        return self._finalize(
            request_id=request_id,
            session=session,
            intent=intent,
            classification=classification,
            route="main_jarvis_commitment",
            result={
                "status": "policy_denied" if configured and not authorized_here else "unavailable",
                "message": message,
                "commitment_mode": mode,
            },
            request_text=request_text,
            user_id=payload.user_id,
        )

    def _open_clarification(
        self,
        *,
        request_id: str | None,
        session: SessionRecord,
        payload: AskRequest,
        request_text: str,
        turn_decision: dict[str, Any],
        intent: Intent,
        entities: dict[str, Any],
        missing_fields: list[str],
        confidence: float,
        reasoning: str,
        resolved_skill: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not missing_fields:
            missing_fields = ["requested_detail"]
        question = str(turn_decision.get("question") or "").strip()
        if not question:
            question = self._domain_context.clarification_question(intent=intent, field_name=missing_fields[0])
        self._pending_interactions.store(
            session=session,
            intent=intent.value,
            entities=entities,
            missing_fields=missing_fields,
            question=question,
            kind="action_clarification",
            skill_id=str((resolved_skill or {}).get("skill_id") or "").strip() or None,
            metadata={
                "source": "main_turn_commitment",
                "confidence": confidence,
                "reasoning": reasoning,
            },
            reason="router._store_pending_clarification",
        )
        self._session_transitions.arm_main_followup(
            session=session,
            reason="main_turn_commitment_clarification",
        )
        self._session_transitions.set_owner(session=session, owner=SessionOwner.MAIN)
        self._session_transitions.set_state(session=session, state=SessionState.AWAITING_CONFIRMATION)
        classification = {
            "intent": intent.value,
            "confidence": confidence,
            "entities": entities,
            "ambiguity_flags": ["main_turn_commitment_clarification"],
            "recommended_owner": SessionOwner.MAIN.value,
            "reasoning": reasoning,
            "commitment_mode": "clarify_action",
        }
        self._event_log.record(
            event_type="main.action.commitment.clarification_opened",
            session_id=session.session_id,
            payload={"intent": intent.value, "missing_fields": missing_fields},
        )
        return self._finalize(
            request_id=request_id,
            session=session,
            intent=intent,
            classification=classification,
            route="main_jarvis_commitment",
            result={
                "status": "needs_clarification",
                "message": str(turn_decision.get("message") or "").strip()
                or "I understand the action, but I need one detail before I run it.",
                "question": question,
                "missing_fields": missing_fields,
                "entities": entities,
                "commitment_mode": "clarify_action",
            },
            request_text=request_text,
            user_id=payload.user_id,
        )

    def _finalize(self, *, request_id: str | None, **kwargs: Any) -> dict[str, Any]:
        return self._turn_finalizer.build_response(request_id=request_id, **kwargs)

    @staticmethod
    def _capability_for_intent(
        *,
        intent: Intent,
        runtime_capability_catalog: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        for entry in runtime_capability_catalog:
            if not isinstance(entry, dict):
                continue
            documented = {
                str(item or "").strip().casefold()
                for item in entry.get("intents") or []
                if str(item or "").strip()
            }
            if intent.value in documented:
                return entry
        return None

    @staticmethod
    def _coerce_intent(raw_intent: str) -> Intent | None:
        cleaned = raw_intent.strip().lower()
        return next((intent for intent in Intent if intent.value == cleaned), None)

    @staticmethod
    def _child_action_denied(context: dict[str, Any], intent: Intent) -> bool:
        is_child = bool(context.get("is_child"))
        profile = str(context.get("policy_profile") or "").strip().lower()
        if not is_child or profile != "child_conversation_only":
            return False
        return intent not in {Intent.CONVERSATIONAL, Intent.UNKNOWN, Intent.SYSTEM_WAKE, Intent.SYSTEM_SLEEP}
