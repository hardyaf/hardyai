from __future__ import annotations

import re
from typing import Any, Callable
from uuid import uuid4

from app.core.action_execution import ActionExecutionService
from app.core.domain_context import DomainContextService
from app.core.micro_jarvis import MicroDecision
from app.core.pending_interaction import PendingInteractionCoordinator
from app.core.session_store import SessionRecord, SessionStore
from app.core.session_transitions import SessionTransitionService
from app.core.turn_finalizer import TurnFinalizer
from app.core.types import Intent, SessionOwner, SessionState
from app.schemas.api import AskRequest
from app.services.event_log import EventLogService


class ClarificationCoordinator:
    """Own continuation, cancellation, completion, and execution of pending turns."""

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
    ) -> None:
        self._domain_context = domain_context
        self._pending_interactions = pending_interactions
        self._session_transitions = session_transitions
        self._action_execution = action_execution
        self._turn_finalizer = turn_finalizer
        self._session_store = session_store
        self._event_log = event_log
        self._action_ticket_service = action_ticket_service

    def handle(
        self,
        *,
        payload: AskRequest,
        session: SessionRecord,
        request_id: str | None,
        should_interrupt: Callable[..., bool],
        extract_model_updates: Callable[..., dict[str, Any]],
        complete_conversation: Callable[..., dict[str, Any]],
        open_tool_followup: Callable[..., dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        pending = self._pending_interactions.get(session=session)
        if pending is None:
            return None

        intent = self._coerce_intent(str(pending.get("intent") or ""))
        if intent is None:
            self._clear(session=session)
            return None

        if self._looks_like_cancel_phrase(payload.text):
            return self._cancel(
                payload=payload,
                session=session,
                intent=intent,
                request_id=request_id,
            )

        if should_interrupt(payload=payload, session=session, pending_intent=intent):
            self._clear(session=session)
            self._session_transitions.set_state(session=session, state=SessionState.IDLE)
            self._event_log.record(
                event_type="pending.clarification.interrupted",
                session_id=session.session_id,
                payload={
                    "pending_intent": intent.value,
                    "reason": "new_command_detected",
                    "text": payload.text,
                },
            )
            return None

        entities = pending.get("entities")
        merged_entities = self._domain_context.normalize_entities(
            intent=intent,
            entities=dict(entities) if isinstance(entities, dict) else {},
        )
        pending_missing = pending.get("missing_fields")
        pending_missing = (
            [str(item) for item in pending_missing if str(item).strip()]
            if isinstance(pending_missing, list)
            else []
        )
        is_conversation_pending = self._is_conversation_pending_flow(pending=pending, intent=intent)

        model_updates: dict[str, Any] = {}
        if not is_conversation_pending:
            model_updates = extract_model_updates(
                session=session,
                payload=payload,
                intent=intent,
                missing_fields=pending_missing,
                current_entities=merged_entities,
            )
        safe_updates = self._domain_context.extract_pending_updates(
            session=session,
            intent=intent,
            text=payload.text,
            missing_fields=pending_missing,
            current_entities=merged_entities,
        )
        self._merge_entity_updates(merged_entities, safe_updates)
        self._merge_entity_updates(merged_entities, model_updates)
        merged_entities = self._domain_context.normalize_entities(intent=intent, entities=merged_entities)

        missing_fields = self._remaining_fields(
            intent=intent,
            pending_missing=pending_missing,
            entities=merged_entities,
            conversation_pending=is_conversation_pending,
        )
        if missing_fields:
            return self._continue(
                request_id=request_id,
                payload=payload,
                session=session,
                pending=pending,
                intent=intent,
                entities=merged_entities,
                missing_fields=missing_fields,
            )

        if is_conversation_pending:
            return complete_conversation(
                session=session,
                payload=payload,
                intent=intent,
                merged_entities=merged_entities,
                pending=pending,
            )

        return self._execute_completed_action(
            request_id=request_id,
            payload=payload,
            session=session,
            intent=intent,
            entities=merged_entities,
            open_tool_followup=open_tool_followup,
        )

    def _cancel(
        self,
        *,
        payload: AskRequest,
        session: SessionRecord,
        intent: Intent,
        request_id: str | None,
    ) -> dict[str, Any]:
        self._pending_interactions.cancel(session=session, reason="user_cancelled_pending_flow")
        self._session_transitions.clear_main_followup(session=session)
        self._session_transitions.set_owner(session=session, owner=SessionOwner.MAIN)
        self._session_transitions.set_state(session=session, state=SessionState.IDLE)
        return self._finalize(
            request_id=request_id,
            session=session,
            intent=Intent.CONVERSATIONAL,
            classification={
                "intent": Intent.CONVERSATIONAL.value,
                "confidence": 0.98,
                "entities": {},
                "ambiguity_flags": ["cancelled_pending_clarification"],
                "recommended_owner": SessionOwner.MAIN.value,
                "reasoning": "user_cancelled_pending_flow",
                "cancelled_intent": intent.value,
            },
            route="main_jarvis_repair",
            result={
                "status": "cancelled",
                "message": "Okay, cancelled. I did not make any changes.",
                "cancelled_intent": intent.value,
            },
            request_text=payload.text,
            user_id=payload.user_id,
        )

    def _continue(
        self,
        *,
        request_id: str | None,
        payload: AskRequest,
        session: SessionRecord,
        pending: dict[str, Any],
        intent: Intent,
        entities: dict[str, Any],
        missing_fields: list[str],
    ) -> dict[str, Any]:
        question = str(pending.get("question") or "").strip() or None
        if question is None:
            question = self._domain_context.clarification_question(intent=intent, field_name=missing_fields[0])
        continued = self._pending_interactions.continue_interaction(
            session=session,
            entities=entities,
            missing_fields=missing_fields,
            question=question,
            metadata_updates={"source": "router._continue_pending_interaction"},
            reason="router._continue_pending_interaction",
        )
        if not continued:
            self._pending_interactions.store(
                session=session,
                intent=intent.value,
                entities=entities,
                missing_fields=missing_fields,
                question=question,
                metadata={"source": "router._store_pending_clarification"},
                reason="router._store_pending_clarification",
            )
        self._session_transitions.arm_main_followup(
            session=session,
            reason="pending_clarification_continue",
        )
        self._session_transitions.set_owner(session=session, owner=SessionOwner.MAIN)
        self._session_transitions.set_state(session=session, state=SessionState.AWAITING_CONFIRMATION)
        return self._finalize(
            request_id=request_id,
            session=session,
            intent=intent,
            classification={
                "intent": intent.value,
                "confidence": 0.64,
                "entities": entities,
                "ambiguity_flags": ["clarification_pending"],
                "recommended_owner": SessionOwner.MAIN.value,
                "reasoning": "pending_clarification_continue",
                "repair_status": "needs_clarification",
            },
            route="main_jarvis_repair",
            result={
                "status": "needs_clarification",
                "message": "Thanks. I still need one detail before I can run that.",
                "question": question,
                "missing_fields": missing_fields,
                "entities": entities,
                "repaired_by": "main_jarvis",
                "repair_source": "clarification_followup",
            },
            request_text=payload.text,
            user_id=payload.user_id,
        )

    def _execute_completed_action(
        self,
        *,
        request_id: str | None,
        payload: AskRequest,
        session: SessionRecord,
        intent: Intent,
        entities: dict[str, Any],
        open_tool_followup: Callable[..., dict[str, Any] | None],
    ) -> dict[str, Any]:
        repaired = MicroDecision(
            intent=intent,
            confidence=0.8,
            entities=entities,
            ambiguity_flags=["clarification_completed"],
            recommended_owner=SessionOwner.MAIN,
            reasoning="pending_clarification_completed",
        )
        agent_id = self._session_transitions.active_agent_id(session)
        resolved_skill = self._action_execution.resolve(
            intent=intent.value,
            user_id=payload.user_id,
            agent_id=agent_id,
        )
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
                route="main_jarvis_repair",
                request_text=payload.text,
                classification=repaired.to_dict(),
                force=True,
            )
            if started.context_reference != session.context_reference:
                session.context_reference = started.context_reference
                session.touch()
                self._session_store.save(session)

        self._session_transitions.set_owner(session=session, owner=SessionOwner.MAIN)
        self._session_transitions.set_state(session=session, state=SessionState.ERROR_RECOVERY)
        tool_result = self._action_execution.execute(
            intent=intent.value,
            entities=repaired.entities,
            source_interface=payload.source,
            requested_by_user_id=payload.user_id,
            resolved_skill=resolved_skill,
            agent_id=agent_id,
            request_id=request_id,
            request_context=payload.context,
        )
        self._event_log.record(
            event_type="main.repair.clarification.executed",
            session_id=session.session_id,
            payload={"intent": intent.value, "result_status": tool_result.get("status")},
        )
        followup_response = open_tool_followup(
            session=session,
            decision=repaired,
            tool_result=tool_result,
            request_text=payload.text,
            user_id=payload.user_id,
        )
        if followup_response is not None:
            return followup_response
        self._clear(session=session)
        self._session_transitions.set_state(session=session, state=SessionState.IDLE)
        classification = repaired.to_dict()
        classification["repair_status"] = "resolved_action"
        classification["repair_source"] = "clarification_followup"
        result = dict(tool_result)
        result["repaired_by"] = "main_jarvis"
        result["repair_source"] = "clarification_followup"
        return self._finalize(
            request_id=request_id,
            session=session,
            intent=intent,
            classification=classification,
            route="main_jarvis_repair",
            result=result,
            request_text=payload.text,
            user_id=payload.user_id,
        )

    def _remaining_fields(
        self,
        *,
        intent: Intent,
        pending_missing: list[str],
        entities: dict[str, Any],
        conversation_pending: bool,
    ) -> list[str]:
        pending_remaining = [
            field
            for field in pending_missing
            if not self._entity_value_present(entities.get(str(field).strip()))
        ]
        pending_remaining = self._domain_context.normalize_missing_fields(pending_remaining)
        if conversation_pending:
            return pending_remaining
        required = self._domain_context.required_fields(intent=intent, entities=entities)
        return self._domain_context.merge_missing_fields(required, pending_remaining)

    def _clear(self, *, session: SessionRecord) -> None:
        self._pending_interactions.clear(
            session=session,
            reason="router._clear_pending_clarification",
        )

    def _finalize(self, *, request_id: str | None, **kwargs: Any) -> dict[str, Any]:
        return self._turn_finalizer.build_response(request_id=request_id, **kwargs)

    @staticmethod
    def _merge_entity_updates(target: dict[str, Any], updates: dict[str, Any]) -> None:
        for key, value in updates.items():
            if value is None:
                continue
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            target[key] = value

    @staticmethod
    def _entity_value_present(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, list):
            return any(str(item).strip() for item in value)
        return True

    @staticmethod
    def _is_conversation_pending_flow(*, pending: dict[str, Any], intent: Intent) -> bool:
        return str(pending.get("kind") or "").strip().lower().startswith("conversation") or intent in {
            Intent.CONVERSATIONAL,
            Intent.UNKNOWN,
        }

    @staticmethod
    def _coerce_intent(raw_intent: str) -> Intent | None:
        cleaned = raw_intent.strip().lower()
        return next((intent for intent in Intent if intent.value == cleaned), None)

    @staticmethod
    def _looks_like_cancel_phrase(text: str) -> bool:
        cleaned = re.sub(r"[^a-z0-9\s]", " ", text.strip().lower())
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return False
        direct_phrases = {
            "cancel",
            "cancel it",
            "cancel that",
            "cancel this",
            "never mind",
            "nevermind",
            "forget it",
            "forget that",
            "scratch that",
            "stop",
            "abort",
            "disregard that",
            "no thanks",
            "no thank you",
        }
        if cleaned in direct_phrases:
            return True
        return bool(
            re.fullmatch(
                r"(?:please\s+)?(?:never\s*mind|cancel(?:\s+(?:it|that|this))?|"
                r"forget\s+(?:it|that)|scratch\s+that|stop|abort|disregard\s+that|"
                r"no\s+thanks|no\s+thank\s+you)",
                cleaned,
                flags=re.IGNORECASE,
            )
        )
