from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.session_store import SessionRecord
from app.core.turn_finalizer import TurnFinalizationOptions, TurnFinalizer
from app.core.types import Intent, SessionOwner, SessionState
from app.skills.authorized_executor import AuthorizedSkillExecutor


@dataclass(frozen=True)
class DirectActionOutcome:
    authorized: bool
    response: dict[str, Any]


class ActionExecutionService:
    """Canonical registry-authorized execution path for routed and direct actions."""

    def __init__(
        self,
        *,
        authorized_executor: AuthorizedSkillExecutor,
        turn_finalizer: TurnFinalizer,
        action_ticket_service: Any | None,
    ) -> None:
        self._authorized_executor = authorized_executor
        self._turn_finalizer = turn_finalizer
        self._action_ticket_service = action_ticket_service

    def resolve(self, *, intent: str, user_id: str, agent_id: str) -> dict[str, Any] | None:
        return self._authorized_executor.resolve(
            intent=intent,
            user_id=user_id,
            agent_id=agent_id,
        )

    def execute(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        source_interface: str,
        requested_by_user_id: str,
        agent_id: str,
        request_context: dict[str, Any] | None,
        request_id: str | None,
        resolved_skill: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._authorized_executor.execute(
            intent=intent,
            entities=entities,
            source_interface=source_interface,
            requested_by_user_id=requested_by_user_id,
            agent_id=agent_id,
            request_context=request_context,
            request_id=request_id,
            resolved_skill=resolved_skill,
        )

    def execute_direct(
        self,
        *,
        request_id: str,
        intent: Intent,
        entities: dict[str, Any],
        user_id: str,
        agent_id: str,
        source_interface: str,
        request_text: str,
        route: str,
    ) -> DirectActionOutcome:
        skill = self.resolve(intent=intent.value, user_id=user_id, agent_id=agent_id)
        if skill is None:
            return DirectActionOutcome(
                authorized=False,
                response={
                    "status": "policy_denied",
                    "message": "This skill is not currently available for this user and agent.",
                    "intent": intent.value,
                    "denial_reason": "skill_unavailable_or_unauthorized",
                },
            )

        replay = (
            self._action_ticket_service.replay_response(request_id)
            if self._action_ticket_service is not None
            else None
        )
        if replay is not None:
            response = dict(replay.get("result") or {})
            response["request_id"] = request_id
            ticket = dict(replay.get("ticket") or {})
            response["ticket"] = {
                "ticket_id": ticket.get("ticket_id"),
                "status": ticket.get("status"),
                "review_due_at": ticket.get("review_due_at"),
            }
            return DirectActionOutcome(authorized=True, response=response)

        session = SessionRecord(
            session_id=f"direct:{intent.value}:{request_id}",
            user_id=user_id,
            source=source_interface,
            state=SessionState.FAST_COMMAND,
            owner=SessionOwner.MICRO,
            context_reference={"active_agent_id": agent_id},
        )
        classification = {
            "intent": intent.value,
            "confidence": 1.0,
            "entities": dict(entities),
            "ambiguity_flags": [],
            "recommended_owner": "micro",
            "reasoning": route,
        }
        if self._action_ticket_service is not None:
            started = self._action_ticket_service.begin_request(
                request_id=request_id,
                session_id=session.session_id,
                context_reference=session.context_reference,
                user_id=user_id,
                agent_id=agent_id,
                source=source_interface,
                intent=intent.value,
                skill_id=str(skill.get("skill_id") or "").strip() or None,
                route=route,
                request_text=request_text,
                classification=classification,
                force=True,
            )
            session.context_reference = started.context_reference

        result = self.execute(
            intent=intent.value,
            entities=entities,
            source_interface=source_interface,
            requested_by_user_id=user_id,
            agent_id=agent_id,
            request_context={},
            request_id=request_id,
            resolved_skill=skill,
        )
        finalized = self._turn_finalizer.build_response(
            request_id=request_id,
            session=session,
            intent=intent,
            classification=classification,
            route=route,
            result=result,
            request_text=request_text,
            user_id=user_id,
            options=TurnFinalizationOptions(
                record_context_history=False,
                record_conversation_history=False,
                record_memory=False,
            ),
        )
        public_result = dict(finalized.get("result") or {})
        public_result.pop("debug_skill_id", None)
        public_result["request_id"] = str(finalized.get("request_id") or request_id)
        delivery = finalized.get("delivery")
        if isinstance(delivery, dict):
            public_result["delivery"] = dict(delivery)
        ticket = finalized.get("ticket")
        if isinstance(ticket, dict):
            public_result["ticket"] = {
                "ticket_id": ticket.get("ticket_id"),
                "status": ticket.get("status"),
                "review_due_at": ticket.get("review_due_at"),
            }
        return DirectActionOutcome(authorized=True, response=public_result)
