from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from app.context.context_builder import ContextBuilder
from app.context.entity_registry import EntityRegistryManager
from app.context.pending import PendingInteractionManager
from app.context.reference_resolver import ReferenceResolver
from app.context.session_context_manager import SessionContextManager
from app.context.summarizer import SessionSummaryManager
from app.core.agent_loop import MainAgentLoop
from app.core.agent_loop_types import AgentLoopLimits
from app.core.agent_routing import AgentRoutingPolicy
from app.core.assistant_response import build_assistant_payload
from app.core.content_policy import MainAgentContentPolicyGate
from app.core.conversation_routing import ConversationLanePolicy
from app.core.context_budget import ContextBudget
from app.core.evaluator import MainAgentEvaluator
from app.core.executor import MainAgentExecutor
from app.core.main_jarvis import MainJarvis
from app.core.micro_jarvis import MicroDecision, MicroJarvis
from app.core.planner import MainAgentPlanner
from app.core.request_pipeline import JarvisRequestPipeline, PipelineDecision
from app.core.session_store import SessionRecord, SessionStore
from app.core.state_machine import (
    RuntimePowerController,
    next_state_for_owner_intent,
)
from app.core.types import (
    EMAIL_AGENT_INTENTS,
    FAST_COMMAND_INTENTS,
    MAIN_ACTION_INTENTS,
    Intent,
    SessionOwner,
    SessionState,
)
from app.services.conversation_history_service import ConversationHistoryService
from app.services.memory_service import MemoryService
from app.services.durable_write_service import DurableWriteService
from app.schemas.api import AskRequest
from app.services.event_log import EventLogService
from app.skills.authorized_executor import AuthorizedSkillExecutor, RuntimeCapabilityProjector
from app.skills.context_contracts import default_skill_context_contracts
from app.skills.execution_dispatcher import SkillExecutionDispatcher
from app.tools.calendar_service import CalendarService
from app.tools.home_service import HomeService
from app.tools.lists_service import ListsService

if TYPE_CHECKING:
    from app.skills.registry_service import SkillRegistryService
    from app.tickets.service import ActionTicketService
    from app.services.identity_service import ExternalIdentityService


OWNER_LABEL = {
    SessionOwner.SYSTEM: "system",
    SessionOwner.MICRO: "micro",
    SessionOwner.MAIN: "main",
}

MAIN_CONVERSATIONAL_CONFIDENCE_THRESHOLD = 0.70
MAIN_LOW_CONFIDENCE_FLOOR = 0.55
MAIN_HIGH_RISK_CONFIDENCE_THRESHOLD = 0.80
MAIN_STICKY_FOLLOWUP_TURNS = 2
PENDING_INTERACTION_TTL_SECONDS = 1800.0
RECENT_TURNS_MAX_ENTRIES = 24
RECENT_TURNS_MAX_CHARS = 6000
SESSION_SUMMARY_UPDATE_EVERY_TURNS = 6
SESSION_SUMMARY_BUDGET_CHAR_THRESHOLD = 5200
SESSION_SUMMARY_MAX_CHARS = 900

NON_BLOCKING_AMBIGUITY_FLAGS = {
    "short",
    "resolved_via_main_repair",
    "list_reference_resolved_from_context",
    "switch_reference_resolved_from_context",
    "main_sticky_followup",
}
FOLLOWUP_PRONOUN_PATTERN = re.compile(
    r"^(?:do|does|did|are|is|can|could|would|will|should)\s+"
    r"(?=.{1,100}$).*\b(?:they|it|he|she|them|those|that|this)\b",
    flags=re.IGNORECASE,
)
FOLLOWUP_ELLIPTICAL_PATTERN = re.compile(
    r"^(?:do both|are they|can it|does that|does this|they both)\b",
    flags=re.IGNORECASE,
)
CONVERSATION_PENDING_QUESTION_PATTERN = re.compile(
    r"^(?:who|which|what)\b",
    flags=re.IGNORECASE,
)
CONVERSATION_PENDING_CONFIRM_PATTERN = re.compile(
    r"^(?:is|are|do|does|did|can|could|would|will|should|has|have)\b",
    flags=re.IGNORECASE,
)


class JarvisRouter:
    _CHILD_ACTION_DENIAL_MESSAGE = (
        "I can't control things in the house for you. You can ask me a question or talk with me instead."
    )

    def __init__(
        self,
        micro_jarvis: MicroJarvis,
        main_jarvis: MainJarvis,
        session_store: SessionStore,
        runtime_power: RuntimePowerController,
        event_log: EventLogService,
        memory_service: MemoryService | None,
        lists_service: ListsService,
        calendar_service: CalendarService,
        home_service: HomeService,
        skill_registry: "SkillRegistryService | None" = None,
        conversation_history_service: ConversationHistoryService | None = None,
        agent_loop_max_steps: int = 8,
        agent_loop_max_failures: int = 2,
        agent_loop_context_max_chars: int = 2400,
        agent_loop_auto_approve_actions: bool = True,
        main_agent_content_policy_enabled: bool = True,
        main_agent_content_policy_children_only: bool = True,
        main_agent_content_policy_blocked_patterns: list[str] | None = None,
        main_agent_token_session_enabled: bool = True,
        main_agent_token_session_max_turns: int = 12,
        main_conversational_confidence_threshold: float = MAIN_CONVERSATIONAL_CONFIDENCE_THRESHOLD,
        main_low_confidence_floor: float = MAIN_LOW_CONFIDENCE_FLOOR,
        main_high_risk_confidence_threshold: float = MAIN_HIGH_RISK_CONFIDENCE_THRESHOLD,
        main_sticky_followup_turns: int = MAIN_STICKY_FOLLOWUP_TURNS,
        main_pending_clarification_heuristic_fallback_enabled: bool = False,
        pending_interaction_ttl_seconds: float = PENDING_INTERACTION_TTL_SECONDS,
        recent_turns_max_entries: int = RECENT_TURNS_MAX_ENTRIES,
        recent_turns_max_chars: int = RECENT_TURNS_MAX_CHARS,
        session_summary_update_every_turns: int = SESSION_SUMMARY_UPDATE_EVERY_TURNS,
        session_summary_budget_char_threshold: int = SESSION_SUMMARY_BUDGET_CHAR_THRESHOLD,
        session_summary_max_chars: int = SESSION_SUMMARY_MAX_CHARS,
        action_ticket_service: "ActionTicketService | None" = None,
        identity_service: "ExternalIdentityService | None" = None,
        email_agent_service: Any | None = None,
        durable_write_service: DurableWriteService | None = None,
    ) -> None:
        self._micro_jarvis = micro_jarvis
        self._main_jarvis = main_jarvis
        self._session_store = session_store
        self._runtime_power = runtime_power
        self._event_log = event_log
        self._memory_service = memory_service
        self._conversation_history_service = conversation_history_service
        self._lists_service = lists_service
        self._calendar_service = calendar_service
        self._home_service = home_service
        self._skill_registry = skill_registry
        self._action_ticket_service = action_ticket_service
        self._identity_service = identity_service
        self._durable_write_service = durable_write_service
        self._request_id_var: ContextVar[str | None] = ContextVar(
            "jarvis_request_id",
            default=None,
        )
        self._request_pipeline = JarvisRequestPipeline()
        self._conversation_lane_policy = ConversationLanePolicy()
        self._agent_routing_policy = AgentRoutingPolicy(
            pipeline=self._request_pipeline,
            skill_registry=skill_registry,
        )
        execution_dispatcher = SkillExecutionDispatcher(
            lists_service=lists_service,
            calendar_service=calendar_service,
            home_service=home_service,
            email_agent_service=email_agent_service,
        )
        self._authorized_skill_executor = AuthorizedSkillExecutor(
            skill_registry=skill_registry,
            dispatcher=execution_dispatcher,
        )
        self._capability_projector = RuntimeCapabilityProjector(
            skill_registry=skill_registry,
            dispatcher=execution_dispatcher,
            main_action_intents={intent.value for intent in MAIN_ACTION_INTENTS},
            known_intents={intent.value for intent in Intent},
        )
        self._main_agent_planner = MainAgentPlanner(auto_approve_actions=agent_loop_auto_approve_actions)
        self._main_agent_evaluator = MainAgentEvaluator()
        self._main_agent_context_budget = ContextBudget(max_chars=agent_loop_context_max_chars)
        self._main_agent_limits = AgentLoopLimits(
            max_steps=max(1, int(agent_loop_max_steps)),
            max_failures=max(1, int(agent_loop_max_failures)),
        )
        self._main_agent_content_policy_gate = MainAgentContentPolicyGate(
            enabled=main_agent_content_policy_enabled,
            enforce_for_children_only=main_agent_content_policy_children_only,
            blocked_patterns=main_agent_content_policy_blocked_patterns,
        )
        self._main_agent_token_session_enabled = bool(main_agent_token_session_enabled)
        self._main_agent_token_session_max_turns = max(1, int(main_agent_token_session_max_turns))
        self._main_conversational_confidence_threshold = max(
            0.0,
            min(float(main_conversational_confidence_threshold), 1.0),
        )
        self._main_low_confidence_floor = max(
            0.0,
            min(float(main_low_confidence_floor), self._main_conversational_confidence_threshold),
        )
        self._main_high_risk_confidence_threshold = max(
            self._main_conversational_confidence_threshold,
            min(float(main_high_risk_confidence_threshold), 1.0),
        )
        self._main_sticky_followup_turns = max(0, int(main_sticky_followup_turns))
        self._main_pending_clarification_heuristic_fallback_enabled = bool(
            main_pending_clarification_heuristic_fallback_enabled
        )
        self._pending_interaction_ttl_seconds = max(1.0, float(pending_interaction_ttl_seconds))
        self._pending_interaction_manager = PendingInteractionManager(
            default_ttl_seconds=self._pending_interaction_ttl_seconds
        )
        self._entity_registry_manager = EntityRegistryManager()
        self._reference_resolver = ReferenceResolver()
        self._skill_context_contracts = default_skill_context_contracts(
            email_agent_service=email_agent_service
        )
        self._session_context_manager = SessionContextManager(
            max_recent_turns=max(2, int(recent_turns_max_entries)),
            max_recent_chars=max(512, int(recent_turns_max_chars)),
        )
        self._session_summary_manager = SessionSummaryManager(
            update_every_turns=max(1, int(session_summary_update_every_turns)),
            budget_char_threshold=max(256, int(session_summary_budget_char_threshold)),
            max_summary_chars=max(128, int(session_summary_max_chars)),
        )
        self._context_builder = ContextBuilder()

    def _skill_execution_context(
        self,
        *,
        source_interface: str,
        requested_by_user_id: str,
        agent_id: str,
        request_context: dict[str, Any] | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._authorized_skill_executor.build_context(
            source_interface=source_interface,
            requested_by_user_id=requested_by_user_id,
            agent_id=agent_id,
            request_context=request_context,
            request_id=request_id or self._request_id_var.get(),
        )

    def _runtime_capability_catalog(
        self,
        *,
        payload: AskRequest,
        agent_id: str,
    ) -> list[dict[str, Any]]:
        return self._capability_projector.project(
            user_id=payload.user_id,
            agent_id=agent_id,
            source_interface=payload.source,
            request_context=payload.context,
        )

    def route(self, payload: AskRequest) -> dict[str, Any]:
        request_id = str(
            payload.request_id
            or payload.context.get("request_id")
            or payload.context.get("external_message_id")
            or uuid4()
        ).strip()
        self._request_id_var.set(request_id)
        external_user_id = str(
            payload.context.get("external_user_id")
            or (payload.user_id if str(payload.source or "").strip().lower() == "discord" else "")
        ).strip()
        identity_binding = (
            self._identity_service.resolve(
                source=str(payload.source or ""),
                external_user_id=external_user_id,
            )
            if self._identity_service is not None and external_user_id
            else None
        )
        agent_context = self._resolve_agent_context(payload, identity_binding=identity_binding)
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

        channel_key = self._channel_key_for_payload(effective_payload)
        force_new_for_channel = bool(
            channel_key and self._micro_jarvis.looks_like_wake_command(effective_payload.text)
        )
        session = self._session_store.get_or_create(
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
            channel_status = self._session_store.channel_status(channel_key)
            if isinstance(channel_status, dict):
                session_context["channel_session"] = channel_status
        else:
            session_context.pop("channel_session", None)
        session.context_reference = session_context
        session.touch()
        self._session_store.save(session)
        channel_runtime = session_context.get("channel_session")
        channel_runtime_key = None
        if isinstance(channel_runtime, dict):
            raw_channel_key = channel_runtime.get("channel_key")
            if isinstance(raw_channel_key, str) and raw_channel_key.strip():
                channel_runtime_key = raw_channel_key.strip()
        self._event_log.record(
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

        replay = (
            self._action_ticket_service.replay_response(request_id)
            if self._action_ticket_service is not None
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
            self._event_log.record(
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
                "power_state": self._runtime_power.state.value,
                "session_runtime": {"last_activity_at": session.last_activity_timestamp},
                "intent": replay_intent,
                "classification": replay_classification,
                "route": str(ticket.get("route") or "idempotent_replay"),
                "result": replay_result,
                "dialog": replay_dialog,
                "assistant": assistant,
            }

        if wake_on_message and not self._runtime_power.is_awake():
            self._runtime_power.wake()
            self._event_log.record(
                "runtime.wake",
                session.session_id,
                {
                    "reason": "channel_auto_wake",
                    "source": effective_payload.source,
                    "channel_key": channel_runtime_key,
                },
            )

        if not self._runtime_power.is_awake():
            if self._micro_jarvis.looks_like_wake_command(effective_payload.text):
                self._runtime_power.wake()
                self._event_log.record("runtime.wake", session.session_id, {"reason": "wake_phrase"})
                self._set_owner(session, SessionOwner.SYSTEM)
                self._set_state(session, SessionState.IDLE)
                return self._build_response(
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
            return self._build_response(
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

        if self._looks_like_exit_skill_phrase(effective_payload.text):
            pending = self._pending_clarification(session)
            cancelled_intent = str((pending or {}).get("intent") or "").strip() or None
            self._clear_pending_clarification(session)
            self._clear_main_sticky_followup(session)
            self._set_owner(session, SessionOwner.SYSTEM)
            self._set_state(session, SessionState.IDLE)
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
            return self._build_response(
                session=session,
                intent=Intent.CONVERSATIONAL,
                classification=classification,
                route="session_control",
                result=result,
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )

        if self._child_plan_denied(effective_context) and self._pending_clarification(session) is not None:
            self._cancel_pending_interaction(
                session=session,
                reason="identity_policy_changed_or_conversation_only",
            )
            self._set_owner(session, SessionOwner.SYSTEM)
            self._set_state(session, SessionState.IDLE)
            return self._build_response(
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

        pending_response = self._handle_pending_clarification(payload=effective_payload, session=session)
        if pending_response is not None:
            return pending_response

        working_context_packet = self._build_working_context_packet(
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
        for contract in self._skill_context_contracts:
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
                self._event_log.record(
                    event_type="context.contract.enrich_working_context.failed",
                    session_id=session.session_id,
                    payload={"contract_id": getattr(contract, "contract_id", "unknown"), "error": type(exc).__name__},
                )
        contextual_followup = self._infer_contextual_followup(
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

        micro_command_enabled = self._micro_command_enabled(effective_payload)
        if micro_command_enabled:
            decision = self._micro_jarvis.interpret(
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
            self._event_log.record(
                event_type="pipeline.micro.bypassed",
                session_id=session.session_id,
                payload={
                    "reason": "discord_prefix_not_present",
                    "source": effective_payload.source,
                    "micro_command_explicit": False,
                    "target_owner": SessionOwner.MAIN.value,
                },
            )
        decision = self._resolve_followup_entities(session=session, decision=decision)
        decision = self._resolve_handoff_followup_entities(
            session=session,
            decision=decision,
            working_context=working_context_payload,
        )
        decision = self._normalize_decision_entities(decision)
        decision = self._apply_main_sticky_followup(session=session, decision=decision)
        conversation_lane = self._conversation_lane_policy.decide(
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
            self._event_log.record(
                event_type="pipeline.conversation_lane.resolved",
                session_id=session.session_id,
                payload={
                    "from_intent": Intent.UNKNOWN.value,
                    "to_intent": Intent.CONVERSATIONAL.value,
                    "reason": conversation_lane.reason,
                    "confidence": conversation_lane.confidence,
                },
            )
        resolved_skill = self._resolve_skill_for_intent(
            intent=decision.intent,
            user_id=effective_payload.user_id,
            agent_id=active_agent_id,
        )
        self._event_log.record(
            event_type="micro.decision",
            session_id=session.session_id,
            payload={
                **decision.to_dict(),
                "resolved_skill_id": str((resolved_skill or {}).get("skill_id") or ""),
                "agent_id": active_agent_id,
            },
        )

        if self._child_action_denied(effective_context, decision.intent):
            self._set_owner(session, SessionOwner.SYSTEM)
            self._set_state(session, SessionState.IDLE)
            return self._build_response(
                session=session,
                intent=decision.intent,
                classification=decision.to_dict(),
                route="identity_policy",
                result={
                    "status": "policy_denied",
                    "message": self._CHILD_ACTION_DENIAL_MESSAGE,
                    "policy_profile": effective_context.get("policy_profile"),
                },
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )

        if decision.intent == Intent.SYSTEM_SLEEP:
            self._runtime_power.sleep()
            self._event_log.record("runtime.sleep", session.session_id, {"reason": "sleep_phrase"})
            self._clear_main_sticky_followup(session)
            self._set_owner(session, SessionOwner.SYSTEM)
            self._set_state(session, SessionState.IDLE)
            return self._build_response(
                session=session,
                intent=decision.intent,
                classification=decision.to_dict(),
                route="runtime_power",
                result={"status": "sleeping", "message": "Jarvis is going to sleep."},
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )

        if decision.intent == Intent.SYSTEM_WAKE:
            self._runtime_power.wake()
            self._event_log.record("runtime.wake", session.session_id, {"reason": "wake_phrase"})
            self._clear_main_sticky_followup(session)
            self._set_owner(session, SessionOwner.SYSTEM)
            self._set_state(session, SessionState.IDLE)
            return self._build_response(
                session=session,
                intent=decision.intent,
                classification=decision.to_dict(),
                route="runtime_power",
                result={"status": "awake", "message": "Jarvis is already awake."},
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )

        required_missing_fields = self._required_fields_for_intent(
            intent=decision.intent,
            entities=decision.entities,
        )

        if self._action_ticket_service is not None:
            started = self._action_ticket_service.begin_request(
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
                self._session_store.save(session)

        repair_response = self._attempt_main_repair(
            payload=effective_payload,
            session=session,
            micro_decision=decision,
            required_missing_fields=required_missing_fields,
            working_context_payload=working_context_payload,
            contextual_followup=contextual_followup if isinstance(contextual_followup, dict) else None,
        )
        if repair_response is not None:
            return repair_response

        routing_decision = self._agent_routing_policy.decide(
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
            self._event_log.record(
                event_type="micro.execution.blocked_by_skill_contract",
                session_id=session.session_id,
                payload={
                    "intent": decision.intent.value,
                    "skill_id": str((resolved_skill or {}).get("skill_id") or ""),
                    "reason": "micro_not_allowed_for_intent",
                },
            )
        self._event_log.record(
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
        classification_with_pipeline = self._with_pipeline_metadata(
            classification=decision.to_dict(),
            pipeline=routing_decision.pipeline,
            routing_reasons=routing_decision.reasons,
        )
        self._set_owner(session, target_owner)
        self._set_state(session, next_state_for_owner_intent(target_owner, decision.intent))

        if target_owner == SessionOwner.MICRO and decision.intent in FAST_COMMAND_INTENTS:
            tool_result = self._execute_fast_command(
                decision=decision,
                source_interface=effective_payload.source,
                requested_by_user_id=effective_payload.user_id,
                resolved_skill=resolved_skill,
                agent_id=active_agent_id,
                request_context=effective_payload.context,
            )
            self._event_log.record(
                event_type="tool.executed",
                session_id=session.session_id,
                payload={
                    "intent": decision.intent.value,
                    "result_status": tool_result.get("status"),
                },
            )
            followup_response = self._maybe_open_tool_followup(
                session=session,
                decision=decision,
                tool_result=tool_result,
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )
            if followup_response is not None:
                return followup_response
            self._clear_pending_clarification(session)
            self._set_state(session, SessionState.IDLE)
            return self._build_response(
                session=session,
                intent=decision.intent,
                classification=classification_with_pipeline,
                route="micro_tool",
                result=tool_result,
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )

        if target_owner == SessionOwner.MAIN and decision.intent in EMAIL_AGENT_INTENTS:
            tool_result = self._execute_fast_command(
                decision=decision,
                source_interface=effective_payload.source,
                requested_by_user_id=effective_payload.user_id,
                resolved_skill=resolved_skill,
                agent_id=active_agent_id,
                request_context=effective_payload.context,
            )
            self._event_log.record(
                event_type="tool.executed",
                session_id=session.session_id,
                payload={
                    "intent": decision.intent.value,
                    "result_status": tool_result.get("status"),
                    "sensitive_domain": "email",
                },
            )
            self._clear_pending_clarification(session)
            self._set_state(session, SessionState.IDLE)
            return self._build_response(
                session=session,
                intent=decision.intent,
                classification=classification_with_pipeline,
                route="main_skill",
                result=tool_result,
                request_text=raw_text,
                user_id=effective_payload.user_id,
            )

        main_request_text = effective_payload.text
        if decision.intent in {Intent.UNKNOWN, Intent.CONVERSATIONAL} and isinstance(contextual_followup, dict):
            rewritten_text = str(contextual_followup.get("rewritten_user_text") or "").strip()
            if rewritten_text:
                main_request_text = rewritten_text

        runtime_capability_catalog = self._runtime_capability_catalog(
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

        response = self._main_jarvis.respond(
            text=main_request_text,
            context=main_context,
        )
        commitment_response = self._handle_main_turn_commitment(
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
        if isinstance(plan, dict) and self._child_plan_denied(effective_context):
            response = {
                "status": "policy_denied",
                "message": self._CHILD_ACTION_DENIAL_MESSAGE,
                "policy_profile": effective_context.get("policy_profile"),
            }
            self._set_state(session, SessionState.IDLE)
        elif isinstance(plan, dict):
            if self._action_ticket_service is not None:
                started = self._action_ticket_service.begin_request(
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
                    self._session_store.save(session)
            self._event_log.record(
                event_type="main.plan.generated",
                session_id=session.session_id,
                payload={
                    "plan_type": plan.get("plan_type"),
                    "scope": plan.get("scope"),
                    "confidence": plan.get("confidence"),
                    "command_count": len(plan.get("commands") or []),
                },
            )
            execution = self._execute_main_plan(
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
                self._clear_pending_clarification(session)
            self._set_state(session, SessionState.IDLE)
        elif decision.intent in FAST_COMMAND_INTENTS:
            missing_fields = self._required_fields_for_intent(intent=decision.intent, entities=decision.entities)
            if not missing_fields:
                tool_result = self._execute_fast_command(
                    decision=decision,
                    source_interface=effective_payload.source,
                    requested_by_user_id=effective_payload.user_id,
                    resolved_skill=resolved_skill,
                    agent_id=active_agent_id,
                )
                self._event_log.record(
                    event_type="main.fast_fallback.executed",
                    session_id=session.session_id,
                    payload={
                        "intent": decision.intent.value,
                        "result_status": tool_result.get("status"),
                    },
                )
                response = dict(tool_result)
                response["executed_by"] = "main_fast_fallback"
                self._clear_pending_clarification(session)
                self._set_state(session, SessionState.IDLE)
        classification_with_pipeline, response = self._maybe_open_conversation_followup(
            session=session,
            decision=decision,
            classification=classification_with_pipeline,
            response=response,
            request_text=raw_text,
            working_context_payload=working_context_payload,
        )
        self._event_log.record(
            event_type="response.generated",
            session_id=session.session_id,
            payload={"route": "main_jarvis", "status": response.get("status")},
        )
        return self._build_response(
            session=session,
            intent=decision.intent,
            classification=classification_with_pipeline,
            route="main_jarvis",
            result=response,
            request_text=raw_text,
            user_id=effective_payload.user_id,
        )

    def _resolve_agent_context(
        self,
        payload: AskRequest,
        *,
        identity_binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        bound_agent_id = str((identity_binding or {}).get("agent_id") or "jarvis").strip().lower()
        bound_user_id = str((identity_binding or {}).get("user_id") or payload.user_id).strip()
        if self._skill_registry is None:
            return {
                "agent_id": bound_agent_id,
                "display_name": bound_agent_id,
                "wake_alias": None,
                "normalized_text": payload.text,
                "resolved_user_id": bound_user_id,
                "personality_doc_path": None,
            }
        resolved = self._skill_registry.resolve_agent_context(
            text=payload.text,
            fallback_user_id=bound_user_id,
            fallback_agent_id=bound_agent_id,
        )
        if identity_binding and (
            bool(identity_binding.get("age_band"))
            or str(identity_binding.get("policy_profile") or "").startswith("child_")
        ):
            profile = self._skill_registry.get_agent_profile(bound_agent_id) or {}
            resolved.update(
                {
                    "agent_id": bound_agent_id,
                    "display_name": str(profile.get("display_name") or bound_agent_id),
                    "resolved_user_id": bound_user_id,
                    "personality_doc_path": profile.get("personality_doc_path"),
                    "wake_alias": None,
                }
            )
        return resolved

    @staticmethod
    def _child_plan_denied(context: dict[str, Any]) -> bool:
        return bool(context.get("is_child")) and str(
            context.get("policy_profile") or ""
        ).strip().lower() == "child_conversation_only"

    @classmethod
    def _child_action_denied(cls, context: dict[str, Any], intent: Intent) -> bool:
        if not cls._child_plan_denied(context):
            return False
        return intent not in {Intent.CONVERSATIONAL, Intent.UNKNOWN, Intent.SYSTEM_WAKE, Intent.SYSTEM_SLEEP}

    def _handle_main_turn_commitment(
        self,
        *,
        response: dict[str, Any],
        payload: AskRequest,
        session: SessionRecord,
        effective_context: dict[str, Any],
        runtime_capability_catalog: list[dict[str, Any]],
        request_text: str,
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

        capability: dict[str, Any] | None = None
        for entry in runtime_capability_catalog:
            if not isinstance(entry, dict):
                continue
            documented = {
                str(item or "").strip().casefold()
                for item in entry.get("intents") or []
                if str(item or "").strip()
            }
            if intent.value in documented:
                capability = entry
                break

        eligible_intents = {
            str(item or "").strip().casefold()
            for item in (capability or {}).get("main_intents") or []
            if str(item or "").strip()
        }
        configured = (capability or {}).get("configured") is True
        authorized_here = (capability or {}).get("authorized_here") is True
        if intent.value not in eligible_intents or not configured or not authorized_here:
            access_note = str((capability or {}).get("access_note") or "").strip()
            message = access_note or "That action is not currently configured and authorized in this context."
            self._set_owner(session, SessionOwner.MAIN)
            self._set_state(session, SessionState.IDLE)
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
                    "catalog_eligible": intent.value in eligible_intents,
                },
            )
            return self._build_response(
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

        if self._child_action_denied(effective_context, intent):
            self._set_owner(session, SessionOwner.SYSTEM)
            self._set_state(session, SessionState.IDLE)
            return self._build_response(
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
                    "message": self._CHILD_ACTION_DENIAL_MESSAGE,
                    "policy_profile": effective_context.get("policy_profile"),
                },
                request_text=request_text,
                user_id=payload.user_id,
            )

        entities_raw = turn_decision.get("entities")
        entities = self._normalize_entities_for_intent(
            intent=intent,
            entities=dict(entities_raw) if isinstance(entities_raw, dict) else {},
        )
        missing_fields = self._required_fields_for_intent(intent=intent, entities=entities)
        decision_missing = turn_decision.get("missing_fields")
        if isinstance(decision_missing, list):
            missing_fields = self._merge_missing_fields(missing_fields, decision_missing)
        confidence_raw = turn_decision.get("confidence")
        confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) else 0.0
        reasoning = str(turn_decision.get("reasoning") or "main_turn_commitment").strip()
        resolved_skill = self._resolve_skill_for_intent(
            intent=intent,
            user_id=payload.user_id,
            agent_id=self._active_agent_id(session),
        )

        if mode == "clarify_action" or missing_fields:
            if not missing_fields:
                missing_fields = ["requested_detail"]
            question = str(turn_decision.get("question") or "").strip()
            if not question:
                question = self._clarification_question(intent=intent, field_name=missing_fields[0])
            self._store_pending_clarification(
                session=session,
                intent=intent,
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
            )
            self._arm_main_sticky_followup(session=session, reason="main_turn_commitment_clarification")
            self._set_owner(session, SessionOwner.MAIN)
            self._set_state(session, SessionState.AWAITING_CONFIRMATION)
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
            return self._build_response(
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

        committed = MicroDecision(
            intent=intent,
            confidence=max(0.0, min(confidence, 1.0)),
            entities=entities,
            ambiguity_flags=["main_turn_commitment"],
            recommended_owner=SessionOwner.MAIN,
            reasoning=reasoning,
        )
        if committed.confidence < self._main_low_confidence_floor:
            self._set_owner(session, SessionOwner.MAIN)
            self._set_state(session, SessionState.CONVERSATIONAL)
            return self._build_response(
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
                request_id=str(self._request_id_var.get() or payload.request_id or uuid4()),
                session_id=session.session_id,
                context_reference=session.context_reference,
                user_id=payload.user_id,
                agent_id=self._active_agent_id(session),
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

        self._set_owner(session, SessionOwner.MAIN)
        self._set_state(session, SessionState.ERROR_RECOVERY)
        tool_result = self._execute_fast_command(
            decision=committed,
            source_interface=payload.source,
            requested_by_user_id=payload.user_id,
            resolved_skill=resolved_skill,
            agent_id=self._active_agent_id(session),
            request_context=payload.context,
        )
        self._event_log.record(
            event_type="main.action.commitment.executed",
            session_id=session.session_id,
            payload={"intent": intent.value, "result_status": tool_result.get("status")},
        )
        followup_response = self._maybe_open_tool_followup(
            session=session,
            decision=committed,
            tool_result=tool_result,
            request_text=request_text,
            user_id=payload.user_id,
        )
        if followup_response is not None:
            return followup_response
        self._clear_pending_clarification(session)
        self._set_state(session, SessionState.IDLE)
        result = dict(tool_result)
        result["committed_by"] = "main_turn_decision"
        return self._build_response(
            session=session,
            intent=intent,
            classification=classification,
            route="main_jarvis_commitment",
            result=result,
            request_text=request_text,
            user_id=payload.user_id,
        )

    def _maybe_open_conversation_followup(
        self,
        *,
        session: SessionRecord,
        decision: MicroDecision,
        classification: dict[str, Any],
        response: dict[str, Any],
        request_text: str,
        working_context_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if decision.intent not in {Intent.CONVERSATIONAL, Intent.UNKNOWN}:
            return classification, response
        if self._pending_clarification(session) is not None:
            return classification, response

        status_value = str(response.get("status") or "").strip().lower()
        if status_value not in {"conversation"}:
            return classification, response
        question = str(response.get("question") or "").strip()
        message = str(response.get("message") or "").strip()
        if not question and message.endswith("?"):
            question = message
        if not question:
            return classification, response

        missing_fields = self._infer_conversation_pending_fields(question=question)
        if not missing_fields:
            return classification, response

        topic_hint = self._extract_contextual_topic_hint(working_context_payload)
        pending_entities: dict[str, Any] = {
            "conversation_question": question,
        }
        if topic_hint:
            pending_entities["topic_hint"] = topic_hint

        metadata = {
            "question_type": "disambiguation" if "topic_subject" in missing_fields else "confirmation",
            "source": "router._maybe_open_conversation_followup",
            "request_text": request_text,
        }
        self._store_pending_conversation(
            session=session,
            entities=pending_entities,
            missing_fields=missing_fields,
            question=question,
            metadata=metadata,
        )
        self._arm_main_sticky_followup(session=session, reason="conversation_clarification_pending")
        self._set_owner(session, SessionOwner.MAIN)
        self._set_state(session, SessionState.AWAITING_CONFIRMATION)

        updated_response = dict(response)
        updated_response["status"] = "needs_clarification"
        updated_response["question"] = question
        updated_response["missing_fields"] = missing_fields
        updated_response.setdefault("entities", pending_entities)
        updated_response["repair_source"] = "conversation_clarification"

        updated_classification = dict(classification)
        ambiguity_flags_raw = updated_classification.get("ambiguity_flags")
        ambiguity_flags = (
            [str(item) for item in ambiguity_flags_raw if str(item).strip()]
            if isinstance(ambiguity_flags_raw, list)
            else []
        )
        if "conversation_clarification_pending" not in ambiguity_flags:
            ambiguity_flags.append("conversation_clarification_pending")
        updated_classification["ambiguity_flags"] = ambiguity_flags
        updated_classification["repair_status"] = "needs_clarification"
        updated_classification["repair_source"] = "conversation_clarification"
        reasoning = str(updated_classification.get("reasoning") or "").strip()
        if reasoning:
            updated_classification["reasoning"] = f"{reasoning}_conversation_clarification_pending"
        else:
            updated_classification["reasoning"] = "conversation_clarification_pending"
        return updated_classification, updated_response

    def _store_pending_conversation(
        self,
        *,
        session: SessionRecord,
        entities: dict[str, Any],
        missing_fields: list[str],
        question: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        before_snapshot = self._pending_interaction_snapshot(session=session)
        pending = self._pending_interaction_manager.set_pending_interaction(
            session=session,
            intent=Intent.CONVERSATIONAL.value,
            entities=dict(entities),
            missing_fields=[str(item).strip() for item in missing_fields if str(item).strip()],
            question=question,
            kind="conversation_clarification",
            status="pending",
            metadata=dict(metadata or {}),
        )
        session.touch()
        self._session_store.save(session)
        self._record_pending_interaction_transition(
            session=session,
            action="set",
            before=before_snapshot,
            after=self._pending_snapshot_from_object(pending),
            reason="router._store_pending_conversation",
        )

    @staticmethod
    def _infer_conversation_pending_fields(*, question: str) -> list[str]:
        cleaned = re.sub(r"\s+", " ", str(question or "").strip())
        if not cleaned:
            return []
        if CONVERSATION_PENDING_QUESTION_PATTERN.match(cleaned):
            return ["topic_subject"]
        if CONVERSATION_PENDING_CONFIRM_PATTERN.match(cleaned):
            return ["confirmation"]
        return []

    def _infer_contextual_followup(self, *, text: str, working_context: dict[str, Any]) -> dict[str, Any] | None:
        cleaned = re.sub(r"\s+", " ", str(text or "").strip())
        if not cleaned:
            return None
        followup_signal = self._looks_like_contextual_followup_text(
            cleaned,
            working_context=working_context,
        )
        if not followup_signal:
            return None
        topic_hint = self._extract_contextual_topic_hint(working_context)
        if not topic_hint:
            return None
        lowered = cleaned.lower()
        if topic_hint.lower() in lowered:
            return None
        rewritten = f"For {topic_hint}, {cleaned}"
        return {
            "resolved": True,
            "confidence": 0.74 if "pronoun" in followup_signal else 0.66,
            "signal": followup_signal,
            "active_topic": topic_hint,
            "rewritten_user_text": rewritten,
        }

    def _extract_contextual_topic_hint(self, working_context: dict[str, Any]) -> str | None:
        entity_hints = working_context.get("entity_hints")
        if isinstance(entity_hints, list):
            for entity in entity_hints:
                if not isinstance(entity, dict):
                    continue
                domain = str(entity.get("domain") or "").strip().lower()
                entity_type = str(entity.get("entity_type") or "").strip().lower()
                display_name = str(entity.get("display_name") or "").strip()
                if not display_name:
                    continue
                if domain == "conversation" and entity_type in {"topic", "entity", "subject"}:
                    return self._preferred_conversation_topic_hint(entity=entity, fallback=display_name)
        summary = working_context.get("session_summary")
        if isinstance(summary, dict):
            important_entities = summary.get("important_entities")
            if isinstance(important_entities, list):
                for item in important_entities:
                    text = str(item or "").strip()
                    if not text or ":" not in text:
                        continue
                    domain, value = text.split(":", 1)
                    if str(domain).strip().lower() != "conversation":
                        continue
                    candidate = str(value).strip()
                    if candidate:
                        return candidate
        return None

    @staticmethod
    def _preferred_conversation_topic_hint(*, entity: dict[str, Any], fallback: str) -> str:
        aliases = entity.get("aliases")
        if isinstance(aliases, list):
            candidates = [str(item).strip() for item in aliases if str(item).strip()]
            for candidate in reversed(candidates):
                token_count = len([token for token in candidate.split(" ") if token])
                if 1 <= token_count <= 3 and len(candidate) >= 4:
                    return candidate
        return fallback

    def _looks_like_contextual_followup_text(self, text: str, *, working_context: dict[str, Any]) -> str | None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return None
        lowered = cleaned.lower()
        if lowered in {"thanks", "thank you", "ok", "okay", "cool", "nice", "great"}:
            return None
        if FOLLOWUP_PRONOUN_PATTERN.match(cleaned):
            return "pronoun_question"
        if FOLLOWUP_ELLIPTICAL_PATTERN.match(cleaned):
            return "elliptical_question"
        token_count = len([token for token in re.split(r"\s+", lowered) if token])
        if token_count <= 3 and re.fullmatch(r"[a-z0-9' -]+", lowered):
            if self._recent_turns_have_clarification_question(working_context=working_context):
                return "short_followup_after_question"
            return "short_noun_phrase"
        if (
            lowered.endswith("?")
            and token_count <= 8
            and self._recent_turns_have_clarification_question(working_context=working_context)
        ):
            return "short_question_after_question"
        return None

    @staticmethod
    def _recent_turns_have_clarification_question(*, working_context: dict[str, Any]) -> bool:
        recent_turns = working_context.get("recent_turns")
        if not isinstance(recent_turns, list):
            return False
        for turn in reversed(recent_turns):
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role") or "").strip().lower()
            if role == "assistant":
                text = str(turn.get("text") or "").strip()
                return text.endswith("?")
        return False

    @staticmethod
    def _channel_key_for_payload(payload: AskRequest) -> str | None:
        auto_channel = bool(payload.context.get("auto_channel_session"))
        if not auto_channel:
            return None
        user_id = str(payload.user_id or "").strip() or "local_user"
        explicit_channel = str(payload.context.get("session_channel") or "").strip().lower()
        mode_channel = str(payload.context.get("mode") or "").strip().lower()
        source_channel = str(payload.source or "").strip().lower()
        scope = str(payload.context.get("channel_session_scope") or "").strip().lower()
        channel = explicit_channel or mode_channel or source_channel or "default"
        if scope in {"shared", "channel"}:
            return channel
        return f"{user_id}:{channel}"

    @staticmethod
    def _micro_command_enabled(payload: AskRequest) -> bool:
        """Discord enters Micro only through an explicit adapter-recorded prefix."""

        if str(payload.source or "").strip().lower() != "discord":
            return True
        return payload.context.get("micro_command_explicit") is True

    def _resolve_skill_for_intent(
        self,
        *,
        intent: Intent,
        user_id: str,
        agent_id: str,
    ) -> dict[str, Any] | None:
        return self._authorized_skill_executor.resolve(
            intent=intent.value,
            user_id=user_id,
            agent_id=agent_id,
        )

    def _record_skill_run(
        self,
        *,
        skill: dict[str, Any] | None,
        session_id: str | None,
        user_id: str,
        intent: Intent,
        route: str,
        status: str,
        confidence: float | None,
    ) -> None:
        if self._skill_registry is None:
            return
        self._skill_registry.record_skill_run(
            skill=skill,
            session_id=session_id,
            user_id=user_id,
            intent=intent.value,
            route=route,
            status=status,
            confidence=confidence,
        )

    @staticmethod
    def _with_pipeline_metadata(
        *,
        classification: dict[str, Any],
        pipeline: PipelineDecision,
        routing_reasons: list[str] | None = None,
    ) -> dict[str, Any]:
        enriched = dict(classification)
        enriched["request_classification"] = pipeline.request_classification.value
        enriched["execution_path"] = pipeline.execution_path.value
        enriched["requires_validation"] = bool(pipeline.requires_validation)
        enriched["pipeline_reason"] = pipeline.reason
        if routing_reasons:
            enriched["routing_reasons"] = [str(item) for item in routing_reasons if str(item).strip()]
        return enriched

    @staticmethod
    def _should_attempt_main_repair(decision: MicroDecision) -> bool:
        if decision.intent == Intent.HOME_SET_SWITCH:
            action = str(decision.entities.get("action") or "").strip().lower()
            switch_name = str(decision.entities.get("switch_name") or "").strip().lower()
            scope = str(decision.entities.get("scope") or "").strip().lower()
            if action in {"on", "off"} and (scope == "all" or switch_name == "all lights"):
                return False
        if decision.intent in {
            Intent.CALENDAR_ADD_EVENT,
            Intent.CALENDAR_UPDATE_EVENT,
            Intent.CALENDAR_DELETE_EVENT,
        }:
            return True
        if decision.intent == Intent.UNKNOWN:
            return True
        if decision.intent == Intent.CONVERSATIONAL:
            return False
        if decision.intent in FAST_COMMAND_INTENTS and decision.confidence < 0.55:
            return True
        if (
            decision.intent in FAST_COMMAND_INTENTS
            and decision.ambiguity_flags
            and decision.recommended_owner == SessionOwner.MAIN
        ):
            actionable_flags = [
                str(flag).strip().lower()
                for flag in decision.ambiguity_flags
                if str(flag).strip().lower() not in {"main_sticky_followup"}
            ]
            if actionable_flags:
                return True
        return False

    def _attempt_main_repair(
        self,
        payload: AskRequest,
        session: SessionRecord,
        micro_decision: MicroDecision,
        required_missing_fields: list[str] | None = None,
        working_context_payload: dict[str, Any] | None = None,
        contextual_followup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        missing_fields = [str(item) for item in (required_missing_fields or []) if str(item).strip()]
        if not missing_fields and not self._should_attempt_main_repair(micro_decision):
            return None

        repair_working_context = (
            dict(working_context_payload)
            if isinstance(working_context_payload, dict)
            else self._build_working_context_packet(
                session=session,
                user_id=payload.user_id,
                request_text=payload.text,
                route_hint="main_repair",
                intent_hint=micro_decision.intent.value,
            ).to_dict()
        )
        repair_agent_id = str(payload.context.get("agent_id") or "jarvis").strip().lower() or "jarvis"
        runtime_capability_catalog = self._runtime_capability_catalog(
            payload=payload,
            agent_id=repair_agent_id,
        )
        repair = self._main_jarvis.repair_action(
            text=payload.text,
            context={
                "micro_intent": micro_decision.intent.value,
                "micro_confidence": micro_decision.confidence,
                "micro_entities": micro_decision.entities,
                "micro_ambiguity_flags": micro_decision.ambiguity_flags,
                "required_missing_fields": missing_fields,
                "runtime_skill_intents": [micro_decision.intent.value],
                "runtime_capability_catalog": runtime_capability_catalog,
                "working_context": repair_working_context,
                "session_summary": repair_working_context.get("session_summary"),
                "recent_turns": repair_working_context.get("recent_turns"),
                "entity_hints": repair_working_context.get("entity_hints"),
                "pending_interaction": repair_working_context.get("pending_interaction"),
                "budget_metadata": repair_working_context.get("budget_metadata"),
                "contextual_followup": contextual_followup if isinstance(contextual_followup, dict) else None,
                "agent_id": repair_agent_id,
                "agent_display_name": str(payload.context.get("agent_display_name") or "Jarvis"),
                "requested_by_user_id": payload.user_id,
            },
        )
        self._event_log.record(
            event_type="main.repair.attempted",
            session_id=session.session_id,
            payload={
                "micro_intent": micro_decision.intent.value,
                "micro_confidence": micro_decision.confidence,
                "repair_status": repair.get("status"),
                "repair_intent": repair.get("intent"),
                "repair_source": repair.get("source"),
                "required_missing_fields": missing_fields,
            },
        )
        repair_status = str(repair.get("status") or "").strip().lower()
        if repair_status == "resolved_action":
            repaired = self._repair_decision_from_main(repair, micro_decision)
            if repaired is None:
                return None
            repaired = self._resolve_followup_entities(session=session, decision=repaired)
            if self._child_action_denied(payload.context, repaired.intent):
                self._cancel_pending_interaction(
                    session=session,
                    reason="identity_policy_denied_repaired_action",
                )
                self._set_owner(session, SessionOwner.SYSTEM)
                self._set_state(session, SessionState.IDLE)
                return self._build_response(
                    session=session,
                    intent=repaired.intent,
                    classification={
                        **repaired.to_dict(),
                        "recovered_from": micro_decision.to_dict(),
                        "repair_status": "policy_denied",
                        "repair_source": repair.get("source"),
                    },
                    route="identity_policy",
                    result={
                        "status": "policy_denied",
                        "message": self._CHILD_ACTION_DENIAL_MESSAGE,
                        "policy_profile": payload.context.get("policy_profile"),
                    },
                    request_text=payload.text,
                    user_id=payload.user_id,
                )
            if repaired.intent == Intent.CALENDAR_ADD_EVENT:
                invitee_names = self._extract_calendar_invitee_names(payload.text)
                entities = dict(repaired.entities)
                if invitee_names:
                    entities["invitee_names"] = invitee_names
                    entities["invite_explicit"] = True
                else:
                    entities.pop("invitee_names", None)
                    entities.pop("invite_explicit", None)
                repaired = MicroDecision(
                    intent=repaired.intent,
                    confidence=repaired.confidence,
                    entities=entities,
                    ambiguity_flags=list(repaired.ambiguity_flags),
                    recommended_owner=repaired.recommended_owner,
                    reasoning=f"{repaired.reasoning}_invitees_policy_applied",
                )

                if (
                    micro_decision.intent == Intent.CALENDAR_ADD_EVENT
                    and micro_decision.confidence >= max(repaired.confidence, 0.85)
                ):
                    micro_entities = self._normalize_entities_for_intent(
                        intent=Intent.CALENDAR_ADD_EVENT,
                        entities=dict(micro_decision.entities),
                    )
                    micro_missing = self._required_fields_for_intent(
                        intent=Intent.CALENDAR_ADD_EVENT,
                        entities=micro_entities,
                    )
                    if not micro_missing:
                        if invitee_names:
                            micro_entities["invitee_names"] = invitee_names
                            micro_entities["invite_explicit"] = True
                        else:
                            micro_entities.pop("invitee_names", None)
                            micro_entities.pop("invite_explicit", None)
                        repaired = MicroDecision(
                            intent=Intent.CALENDAR_ADD_EVENT,
                            confidence=max(repaired.confidence, micro_decision.confidence),
                            entities=micro_entities,
                            ambiguity_flags=list(repaired.ambiguity_flags),
                            recommended_owner=SessionOwner.MAIN,
                            reasoning=f"{repaired.reasoning}_using_high_conf_micro_calendar_entities",
                        )

            repaired_missing = self._required_fields_for_intent(
                intent=repaired.intent,
                entities=repaired.entities,
            )
            if repaired_missing:
                micro_missing = self._required_fields_for_intent(
                    intent=micro_decision.intent,
                    entities=micro_decision.entities,
                )
                if repaired.intent == micro_decision.intent and not micro_missing:
                    repaired = MicroDecision(
                        intent=micro_decision.intent,
                        confidence=max(repaired.confidence, micro_decision.confidence),
                        entities=dict(micro_decision.entities),
                        ambiguity_flags=list(repaired.ambiguity_flags),
                        recommended_owner=SessionOwner.MAIN,
                        reasoning=f"{repaired.reasoning}_using_micro_entities",
                    )
                    repaired_missing = []
            if repaired_missing:
                question = self._clarification_question(intent=repaired.intent, field_name=repaired_missing[0])
                self._store_pending_clarification(
                    session=session,
                    intent=repaired.intent,
                    entities=repaired.entities,
                    missing_fields=repaired_missing,
                    question=question,
                )
                self._arm_main_sticky_followup(session=session, reason="main_repair_missing_fields")
                self._set_owner(session, SessionOwner.MAIN)
                self._set_state(session, SessionState.AWAITING_CONFIRMATION)
                classification = repaired.to_dict()
                classification["recovered_from"] = micro_decision.to_dict()
                classification["repair_status"] = "needs_clarification"
                classification["repair_source"] = repair.get("source")
                result = {
                    "status": "needs_clarification",
                    "message": "I can do that, but I still need one detail before I schedule it.",
                    "question": question,
                    "missing_fields": repaired_missing,
                    "entities": repaired.entities,
                    "repaired_by": "main_jarvis",
                    "repair_reasoning": str(repair.get("reasoning") or ""),
                    "repair_confidence": repair.get("confidence"),
                    "repair_source": repair.get("source"),
                }
                return self._build_response(
                    session=session,
                    intent=repaired.intent,
                    classification=classification,
                    route="main_jarvis_repair",
                    result=result,
                    request_text=payload.text,
                    user_id=payload.user_id,
                )

            confidence_clarification_response = self._maybe_require_confidence_clarification(
                payload=payload,
                session=session,
                micro_decision=micro_decision,
                repaired_decision=repaired,
                repair=repair,
            )
            if confidence_clarification_response is not None:
                return confidence_clarification_response

            self._set_owner(session, SessionOwner.MAIN)
            self._set_state(session, SessionState.ERROR_RECOVERY)
            tool_result = self._execute_fast_command(
                decision=repaired,
                source_interface=payload.source,
                requested_by_user_id=payload.user_id,
                agent_id=str(payload.context.get("agent_id") or "jarvis"),
                request_context=payload.context,
            )
            self._event_log.record(
                event_type="main.repair.executed",
                session_id=session.session_id,
                payload={
                    "intent": repaired.intent.value,
                    "result_status": tool_result.get("status"),
                },
            )
            self._clear_pending_clarification(session)
            self._set_state(session, SessionState.IDLE)
            classification = repaired.to_dict()
            classification["recovered_from"] = micro_decision.to_dict()
            classification["repair_status"] = "resolved_action"
            result = dict(tool_result)
            result["repaired_by"] = "main_jarvis"
            result["repair_reasoning"] = str(repair.get("reasoning") or "")
            result["repair_confidence"] = repair.get("confidence")
            result["repair_source"] = repair.get("source")
            return self._build_response(
                session=session,
                intent=repaired.intent,
                classification=classification,
                route="main_jarvis_repair",
                result=result,
                request_text=payload.text,
                user_id=payload.user_id,
            )

        if repair_status == "needs_clarification":
            maybe_intent = self._coerce_intent(str(repair.get("intent") or ""))
            intent = maybe_intent or Intent.UNKNOWN
            pending_entities = repair.get("entities")
            if not isinstance(pending_entities, dict):
                pending_entities = {}
            if maybe_intent is not None:
                pending_entities = self._normalize_entities_for_intent(intent=maybe_intent, entities=pending_entities)
            pending_missing = repair.get("missing_fields")
            if not isinstance(pending_missing, list):
                pending_missing = []
            if maybe_intent is not None:
                self._store_pending_clarification(
                    session=session,
                    intent=maybe_intent,
                    entities=pending_entities,
                    missing_fields=[str(item) for item in pending_missing if str(item).strip()],
                    question=str(repair.get("question") or "").strip() or None,
                )
            self._arm_main_sticky_followup(session=session, reason="main_repair_needs_clarification")
            self._set_owner(session, SessionOwner.MAIN)
            self._set_state(session, SessionState.AWAITING_CONFIRMATION)
            classification = micro_decision.to_dict()
            classification["repair_status"] = "needs_clarification"
            classification["repair_candidate_intent"] = repair.get("intent")
            classification["repair_reasoning"] = repair.get("reasoning")
            classification["repair_source"] = repair.get("source")
            result = dict(repair)
            result["repaired_by"] = "main_jarvis"
            return self._build_response(
                session=session,
                intent=intent,
                classification=classification,
                route="main_jarvis_repair",
                result=result,
                request_text=payload.text,
                user_id=payload.user_id,
            )

        if repair_status == "not_actionable":
            if self._should_surface_not_actionable(repair=repair, micro_decision=micro_decision):
                message = str(repair.get("message") or "").strip()
                if not message:
                    message = "I understand the intent, but that capability is not wired yet."
                self._set_owner(session, SessionOwner.MAIN)
                self._set_state(session, SessionState.IDLE)
                classification = micro_decision.to_dict()
                classification["repair_status"] = "not_actionable"
                classification["repair_reasoning"] = repair.get("reasoning")
                classification["repair_source"] = repair.get("source")
                inferred_intent = str(repair.get("inferred_intent") or "").strip() or None
                inferred_entities = repair.get("inferred_entities")
                if not isinstance(inferred_entities, dict):
                    inferred_entities = {}
                if inferred_intent is not None:
                    classification["repair_inferred_intent"] = inferred_intent
                if inferred_entities:
                    classification["repair_inferred_entities"] = inferred_entities
                result = {
                    "status": "not_actionable",
                    "message": message,
                    "repaired_by": "main_jarvis",
                    "repair_source": repair.get("source"),
                    "inferred_intent": inferred_intent,
                    "inferred_entities": inferred_entities,
                }
                return self._build_response(
                    session=session,
                    intent=Intent.CONVERSATIONAL,
                    classification=classification,
                    route="main_jarvis_repair",
                    result=result,
                    request_text=payload.text,
                    user_id=payload.user_id,
                )
            fallback_response = self._fallback_repair_to_missing_fields_clarification(
                payload=payload,
                session=session,
                micro_decision=micro_decision,
                preferred_missing_fields=missing_fields,
                fallback_reason="main_repair_not_actionable_missing_fields_fallback",
            )
            if fallback_response is not None:
                return fallback_response
            return None

        fallback_response = self._fallback_repair_to_missing_fields_clarification(
            payload=payload,
            session=session,
            micro_decision=micro_decision,
            preferred_missing_fields=missing_fields,
            fallback_reason="main_repair_unknown_status_missing_fields_fallback",
        )
        if fallback_response is not None:
            return fallback_response

        return None

    def _fallback_repair_to_missing_fields_clarification(
        self,
        *,
        payload: AskRequest,
        session: SessionRecord,
        micro_decision: MicroDecision,
        preferred_missing_fields: list[str] | None,
        fallback_reason: str,
    ) -> dict[str, Any] | None:
        if micro_decision.intent not in FAST_COMMAND_INTENTS:
            return None

        fallback_entities = self._normalize_entities_for_intent(
            intent=micro_decision.intent,
            entities=dict(micro_decision.entities),
        )
        fallback_missing = [str(item) for item in (preferred_missing_fields or []) if str(item).strip()]
        if not fallback_missing:
            fallback_missing = self._required_fields_for_intent(
                intent=micro_decision.intent,
                entities=fallback_entities,
            )
        if not fallback_missing:
            return None

        question = self._clarification_question(
            intent=micro_decision.intent,
            field_name=fallback_missing[0],
        )
        self._store_pending_clarification(
            session=session,
            intent=micro_decision.intent,
            entities=fallback_entities,
            missing_fields=fallback_missing,
            question=question,
        )
        self._arm_main_sticky_followup(session=session, reason=fallback_reason)
        self._set_owner(session, SessionOwner.MAIN)
        self._set_state(session, SessionState.AWAITING_CONFIRMATION)

        classification = micro_decision.to_dict()
        classification["repair_status"] = "needs_clarification"
        classification["repair_candidate_intent"] = micro_decision.intent.value
        classification["repair_reasoning"] = fallback_reason
        classification["repair_source"] = "fallback"
        result = {
            "status": "needs_clarification",
            "message": "I can do that, but I still need one detail before I can continue.",
            "question": question,
            "missing_fields": fallback_missing,
            "entities": fallback_entities,
            "repaired_by": "main_jarvis",
            "repair_source": "fallback",
        }
        return self._build_response(
            session=session,
            intent=micro_decision.intent,
            classification=classification,
            route="main_jarvis_repair",
            result=result,
            request_text=payload.text,
            user_id=payload.user_id,
        )

    @staticmethod
    def _should_surface_not_actionable(
        *,
        repair: dict[str, Any],
        micro_decision: MicroDecision,
    ) -> bool:
        inferred_intent = str(repair.get("inferred_intent") or "").strip().lower()
        if inferred_intent:
            return True

        reasoning = str(repair.get("reasoning") or "").strip().lower()
        if any(
            marker in reasoning
            for marker in {
                "not_supported",
                "not_wired",
                "calendar_sync",
                "capability_gap",
                "cancel",
            }
        ):
            return True

        message = str(repair.get("message") or "").strip().lower()
        if message:
            capability_markers = {
                "not wired",
                "not supported",
                "not available",
                "i cannot",
                "can't",
                "cannot",
                "unsupported",
            }
            if any(marker in message for marker in capability_markers):
                return True
            if "supported action" in message and micro_decision.intent in {Intent.UNKNOWN, Intent.CONVERSATIONAL}:
                return False

        return False

    def _maybe_open_tool_followup(
        self,
        session: SessionRecord,
        decision: MicroDecision,
        tool_result: dict[str, Any],
        request_text: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        status = str(tool_result.get("status") or "").strip().lower()
        if status == "ok":
            return None

        entities = self._normalize_entities_for_intent(intent=decision.intent, entities=dict(decision.entities))
        missing_fields = tool_result.get("missing_fields")
        if not isinstance(missing_fields, list):
            missing_fields = []
        missing_fields = [str(item) for item in missing_fields if str(item).strip()]
        question = str(tool_result.get("question") or "").strip() or None
        registry = self._entity_registry_manager.get_registry(session=session)
        intent_value = decision.intent.value
        for contract in self._skill_context_contracts:
            if not contract.supports_intent(intent=intent_value):
                continue
            try:
                shaped = contract.shape_tool_followup(
                    intent=intent_value,
                    status=status,
                    tool_result=dict(tool_result),
                    entities=dict(entities),
                    missing_fields=list(missing_fields),
                    question=question,
                    registry=registry,
                )
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                self._event_log.record(
                    event_type="context.contract.shape_tool_followup.failed",
                    session_id=session.session_id,
                    payload={
                        "contract_id": str(getattr(contract, "contract_id", "") or ""),
                        "intent": intent_value,
                        "status": status,
                        "error": str(exc),
                    },
                )
                continue
            if not isinstance(shaped, dict):
                continue
            shaped_entities = shaped.get("entities")
            if isinstance(shaped_entities, dict):
                entities = dict(shaped_entities)
            shaped_missing = shaped.get("missing_fields")
            if isinstance(shaped_missing, list):
                deduped_missing: list[str] = []
                seen_missing: set[str] = set()
                for item in shaped_missing:
                    cleaned = str(item).strip()
                    if not cleaned:
                        continue
                    lowered = cleaned.lower()
                    if lowered in seen_missing:
                        continue
                    deduped_missing.append(cleaned)
                    seen_missing.add(lowered)
                missing_fields = deduped_missing
            if "question" in shaped and shaped.get("question") is None:
                question = None
            elif isinstance(shaped.get("question"), str):
                question = str(shaped.get("question")).strip() or None

        if not missing_fields:
            missing_fields = self._required_fields_for_intent(intent=decision.intent, entities=entities)
        if not missing_fields:
            return None

        if question is None:
            question = self._clarification_question(intent=decision.intent, field_name=missing_fields[0])

        self._store_pending_clarification(
            session=session,
            intent=decision.intent,
            entities=entities,
            missing_fields=missing_fields,
            question=question,
        )
        self._arm_main_sticky_followup(session=session, reason="tool_followup_required")
        self._set_owner(session, SessionOwner.MAIN)
        self._set_state(session, SessionState.AWAITING_CONFIRMATION)

        classification = decision.to_dict()
        classification["repair_status"] = "needs_clarification"
        classification["repair_reasoning"] = "tool_followup_required"
        classification["repair_source"] = "tool_result"

        result = dict(tool_result)
        result["question"] = question
        result["missing_fields"] = missing_fields
        result["entities"] = entities
        result["repaired_by"] = "main_jarvis"
        result["repair_source"] = "tool_result"

        return self._build_response(
            session=session,
            intent=decision.intent,
            classification=classification,
            route="main_jarvis_repair",
            result=result,
            request_text=request_text,
            user_id=user_id,
        )

    def _resolve_followup_entities(self, session: SessionRecord, decision: MicroDecision) -> MicroDecision:
        registry = self._entity_registry_manager.get_registry(session=session)
        intent_value = decision.intent.value
        for contract in self._skill_context_contracts:
            if not contract.supports_intent(intent=intent_value):
                continue
            try:
                decision = contract.resolve_followup(
                    decision=decision,
                    registry=registry,
                    resolver=self._reference_resolver,
                    required_fields_for_intent=self._required_fields_for_intent,
                    has_blocking_ambiguity=self._has_blocking_ambiguity,
                )
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                self._event_log.record(
                    event_type="context.contract.resolve_followup.failed",
                    session_id=session.session_id,
                    payload={
                        "contract_id": str(getattr(contract, "contract_id", "") or ""),
                        "intent": intent_value,
                        "error": str(exc),
                    },
                )
        return decision

    def _normalize_decision_entities(self, decision: MicroDecision) -> MicroDecision:
        decision.entities = self._normalize_entities_for_intent(intent=decision.intent, entities=decision.entities)
        return decision

    @staticmethod
    def _has_blocking_ambiguity(decision: MicroDecision) -> bool:
        blocking_flags = {
            "unknown_intent",
            "model_only",
            "bulk_scope_requires_planning",
            "compound_list_create_add",
            "deictic_list_reference",
            "deictic_event_reference",
        }
        for raw_flag in decision.ambiguity_flags:
            flag = str(raw_flag).strip().lower()
            if flag in blocking_flags:
                return True
        if decision.intent == Intent.HOME_SET_SWITCH:
            scope = str(decision.entities.get("scope") or "").strip().lower()
            if scope == "all":
                return True
        return False

    @staticmethod
    def _normalize_entities_for_intent(intent: Intent, entities: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(entities)
        if intent == Intent.CALENDAR_ADD_EVENT:
            event_title = JarvisRouter._pick_first_text(
                normalized,
                ["event_title", "event_name", "title", "name", "subject", "event"],
            )
            when_hint = JarvisRouter._pick_first_text(
                normalized,
                ["when_hint", "when", "start_time", "start", "start_at", "time", "date", "datetime"],
            )
            person_name = JarvisRouter._normalize_calendar_person_reference(
                normalized.get("person_name")
                or normalized.get("person")
                or normalized.get("owner")
                or normalized.get("calendar_owner")
            )
            invitee_names = JarvisRouter._coerce_name_list(
                normalized.get("invitee_names")
                or normalized.get("invitees")
                or normalized.get("attendees")
                or normalized.get("guests")
            )
            if event_title:
                normalized["event_title"] = event_title
            if when_hint:
                normalized["when_hint"] = when_hint
            if invitee_names:
                normalized["invitee_names"] = invitee_names
            normalized.pop("person_name", None)
            return normalized

        if intent == Intent.CALENDAR_VIEW:
            window = JarvisRouter._pick_first_text(normalized, ["window", "range", "period"]) or "daily"
            window_clean = window.strip().lower()
            if window_clean not in {"daily", "weekly"}:
                if "week" in window_clean:
                    window_clean = "weekly"
                else:
                    window_clean = "daily"

            person_name = JarvisRouter._normalize_calendar_person_reference(
                normalized.get("person_name")
                or normalized.get("person")
                or normalized.get("owner")
                or normalized.get("calendar_owner")
            )
            normalized["window"] = window_clean
            if person_name:
                normalized["person_name"] = person_name
            else:
                normalized.pop("person_name", None)
            return normalized

        if intent in {Intent.CALENDAR_UPDATE_EVENT, Intent.CALENDAR_DELETE_EVENT}:
            event_reference = JarvisRouter._pick_first_text(
                normalized,
                ["event_reference", "event_name", "event_title", "event", "title", "name", "reference"],
            )
            event_id = JarvisRouter._pick_first_text(normalized, ["event_id", "google_event_id"])
            calendar_id = JarvisRouter._pick_first_text(normalized, ["calendar_id", "host_calendar_id"])
            if event_reference:
                normalized["event_reference"] = event_reference
            if event_id:
                normalized["event_id"] = event_id
            if calendar_id:
                normalized["calendar_id"] = calendar_id
            if intent == Intent.CALENDAR_UPDATE_EVENT:
                new_title = JarvisRouter._pick_first_text(
                    normalized,
                    ["new_event_title", "new_title", "updated_title", "replacement_title", "rename_to"],
                )
                new_when = JarvisRouter._pick_first_text(
                    normalized,
                    ["new_when_hint", "new_when", "new_time", "when_hint", "when", "start_time", "date"],
                )
                if new_title:
                    normalized["new_event_title"] = new_title
                if new_when:
                    normalized["new_when_hint"] = new_when
                raw_all_day = normalized.get("all_day")
                if isinstance(raw_all_day, str):
                    bool_value = raw_all_day.strip().casefold()
                    if bool_value in {"true", "1", "yes", "on", "all_day", "all-day", "all day"}:
                        normalized["all_day"] = True
                    elif bool_value in {"false", "0", "no", "off", "timed"}:
                        normalized["all_day"] = False
            return normalized

        if intent == Intent.HOME_SET_SWITCH:
            switch_name = JarvisRouter._pick_first_text(normalized, ["switch_name", "switch", "device", "light"])
            action = JarvisRouter._pick_first_text(normalized, ["action", "state"])
            if switch_name:
                normalized["switch_name"] = switch_name
            if action:
                normalized["action"] = action
            return normalized

        if intent in {Intent.LIST_ADD_ITEM, Intent.LIST_REMOVE_ITEM, Intent.LIST_MARK_ITEM_DONE}:
            item_text = JarvisRouter._pick_first_text(normalized, ["item_text", "item"])
            list_name = JarvisRouter._pick_first_text(normalized, ["list_name", "list"])
            completion_mode = JarvisRouter._pick_first_text(normalized, ["completion_mode", "mode", "mark_mode"])
            if item_text:
                normalized["item_text"] = item_text
            if list_name:
                normalized["list_name"] = list_name
            if completion_mode:
                normalized["completion_mode"] = completion_mode
            return normalized

        if intent in {Intent.LIST_GET_ITEMS, Intent.LIST_CREATE_LIST, Intent.LIST_DELETE_LIST}:
            list_name = JarvisRouter._pick_first_text(normalized, ["list_name", "list"])
            if list_name:
                normalized["list_name"] = list_name
            return normalized

        return normalized

    @staticmethod
    def _pick_first_text(container: dict[str, Any], keys: list[str]) -> str | None:
        for key in keys:
            value = container.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    @staticmethod
    def _coerce_name_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            cleaned = re.sub(r"^\s*invite(?:\s+to)?\s+", "", value.strip(), flags=re.IGNORECASE)
            parts = re.split(r"\s*(?:,| and | & )\s*", cleaned)
            names = [part.strip(" .") for part in parts if part.strip(" .")]
            return JarvisRouter._dedupe_names(names)
        if isinstance(value, list):
            names = [str(item).strip(" .") for item in value if str(item).strip(" .")]
            return JarvisRouter._dedupe_names(names)
        return []

    @staticmethod
    def _normalize_calendar_person_reference(value: Any) -> str | None:
        if value is None:
            return None

        candidate: str | None = None
        if isinstance(value, list):
            for item in value:
                text = str(item).strip(" []'\"")
                if text:
                    candidate = text
                    break
        else:
            candidate = str(value).strip()

        if not candidate:
            return None

        list_repr_match = re.fullmatch(r"\[\s*['\"]?(?P<value>[^'\"]+)['\"]?\s*\]", candidate)
        if list_repr_match:
            candidate = str(list_repr_match.group("value") or "").strip()

        candidate = re.sub(r"\bcalendar\b", "", candidate, flags=re.IGNORECASE).strip(" ,.-")
        candidate = re.sub(r"^(?:for|on|in|at|to)\s+", "", candidate, flags=re.IGNORECASE).strip(" ,.-")
        if not candidate:
            return None

        normalized = re.sub(r"[^a-z0-9\s_-]+", " ", candidate.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return None

        direct_default_aliases = {"my", "our", "me", "us", "the", "house", "home", "household"}
        if normalized in direct_default_aliases:
            return None

        neutral_tokens = {"my", "our", "me", "us", "the", "on", "in", "at", "for", "to", "house", "home"}
        tokens = [token for token in normalized.split() if token]
        if tokens and all(token in neutral_tokens for token in tokens):
            return None

        return candidate

    @staticmethod
    def _dedupe_names(names: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for name in names:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(name)
        return deduped

    def _repair_decision_from_main(
        self,
        repair: dict[str, Any],
        micro_decision: MicroDecision,
    ) -> MicroDecision | None:
        intent = self._coerce_intent(str(repair.get("intent") or ""))
        if intent is None or intent not in MAIN_ACTION_INTENTS:
            return None
        confidence_raw = repair.get("confidence")
        confidence = 0.6
        if isinstance(confidence_raw, (int, float)):
            confidence = max(0.0, min(float(confidence_raw), 1.0))
        entities = repair.get("entities")
        if not isinstance(entities, dict):
            entities = {}
        entities = self._normalize_entities_for_intent(intent=intent, entities=entities)
        reasoning = str(repair.get("reasoning") or "main_repair_resolved_action")
        return MicroDecision(
            intent=intent,
            confidence=confidence,
            entities=entities,
            ambiguity_flags=["resolved_via_main_repair", f"original_{micro_decision.intent.value}"],
            recommended_owner=SessionOwner.MAIN,
            reasoning=reasoning,
        )

    def _maybe_require_confidence_clarification(
        self,
        *,
        payload: AskRequest,
        session: SessionRecord,
        micro_decision: MicroDecision,
        repaired_decision: MicroDecision,
        repair: dict[str, Any],
    ) -> dict[str, Any] | None:
        gate_reason = self._confidence_gate_reason(
            micro_decision=micro_decision,
            repaired_decision=repaired_decision,
        )
        if gate_reason is None:
            return None

        clarification_entities = dict(repaired_decision.entities)
        clarification_field = self._default_clarification_field_for_intent(repaired_decision.intent)
        missing_fields: list[str] = []
        if clarification_field:
            clarification_entities.pop(clarification_field, None)
            missing_fields = [clarification_field]

        if missing_fields:
            question = self._clarification_question(intent=repaired_decision.intent, field_name=missing_fields[0])
            self._store_pending_clarification(
                session=session,
                intent=repaired_decision.intent,
                entities=clarification_entities,
                missing_fields=missing_fields,
                question=question,
            )
            self._set_state(session, SessionState.AWAITING_CONFIRMATION)
        else:
            question = "Can you restate that with the exact action and target so I do not run the wrong command?"
            self._set_state(session, SessionState.CONVERSATIONAL)

        self._arm_main_sticky_followup(session=session, reason=f"confidence_gate:{gate_reason}")
        self._set_owner(session, SessionOwner.MAIN)
        classification = repaired_decision.to_dict()
        classification["recovered_from"] = micro_decision.to_dict()
        classification["repair_status"] = "needs_clarification"
        classification["repair_source"] = repair.get("source")
        classification["repair_reasoning"] = repair.get("reasoning")
        classification["confidence_gate"] = gate_reason
        result = {
            "status": "needs_clarification",
            "message": "I want to double-check before I run that.",
            "question": question,
            "missing_fields": missing_fields,
            "entities": clarification_entities,
            "repaired_by": "main_jarvis",
            "repair_reasoning": str(repair.get("reasoning") or ""),
            "repair_confidence": repair.get("confidence"),
            "repair_source": repair.get("source"),
            "confidence_gate": gate_reason,
        }
        return self._build_response(
            session=session,
            intent=repaired_decision.intent,
            classification=classification,
            route="main_jarvis_repair",
            result=result,
            request_text=payload.text,
            user_id=payload.user_id,
        )

    def _confidence_gate_reason(
        self,
        *,
        micro_decision: MicroDecision,
        repaired_decision: MicroDecision,
    ) -> str | None:
        confidence = max(0.0, min(float(repaired_decision.confidence), 1.0))
        reasoning = str(repaired_decision.reasoning or "").strip().lower()
        if "asr_recovery" in reasoning and confidence >= 0.65:
            return None
        ambiguity_flags = self._meaningful_ambiguity_flags(
            micro_flags=micro_decision.ambiguity_flags,
            repaired_flags=repaired_decision.ambiguity_flags,
        )
        if confidence < self._main_low_confidence_floor:
            return "low_confidence"
        if self._is_high_risk_bulk_write(repaired_decision) and confidence < self._main_high_risk_confidence_threshold:
            return "high_risk_low_confidence"
        if confidence < self._main_conversational_confidence_threshold and ambiguity_flags:
            return "ambiguous_mid_confidence"
        return None

    @staticmethod
    def _meaningful_ambiguity_flags(*, micro_flags: list[str], repaired_flags: list[str]) -> list[str]:
        combined: list[str] = []
        seen: set[str] = set()
        for raw in [*micro_flags, *repaired_flags]:
            flag = str(raw).strip().lower()
            if not flag:
                continue
            if flag.startswith("original_"):
                continue
            if flag in NON_BLOCKING_AMBIGUITY_FLAGS:
                continue
            if flag in seen:
                continue
            seen.add(flag)
            combined.append(flag)
        return combined

    @staticmethod
    def _is_high_risk_bulk_write(decision: MicroDecision) -> bool:
        if decision.intent != Intent.HOME_SET_SWITCH:
            return False
        scope = str(decision.entities.get("scope") or "").strip().lower()
        switch_name = str(decision.entities.get("switch_name") or "").strip().lower()
        return scope == "all" or switch_name == "all lights"

    @staticmethod
    def _default_clarification_field_for_intent(intent: Intent) -> str | None:
        if intent == Intent.HOME_SET_SWITCH:
            return "switch_name"
        if intent in {
            Intent.LIST_CREATE_LIST,
            Intent.LIST_ADD_ITEM,
            Intent.LIST_GET_ITEMS,
            Intent.LIST_DELETE_LIST,
            Intent.LIST_REMOVE_ITEM,
            Intent.LIST_MARK_ITEM_DONE,
        }:
            return "list_name"
        if intent == Intent.CALENDAR_ADD_EVENT:
            return "when_hint"
        if intent in {Intent.CALENDAR_UPDATE_EVENT, Intent.CALENDAR_DELETE_EVENT}:
            return "event_reference"
        return None

    @staticmethod
    def _coerce_intent(raw_intent: str) -> Intent | None:
        cleaned = raw_intent.strip().lower()
        for intent in Intent:
            if intent.value == cleaned:
                return intent
        return None

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
                r"(?:please\s+)?(?:"
                r"never\s*mind|"
                r"cancel(?:\s+(?:it|that|this))?|"
                r"forget\s+(?:it|that)|"
                r"scratch\s+that|"
                r"stop|"
                r"abort|"
                r"disregard\s+that|"
                r"no\s+thanks|"
                r"no\s+thank\s+you"
                r")",
                cleaned,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _looks_like_exit_skill_phrase(text: str) -> bool:
        cleaned = re.sub(r"[^a-z0-9\s]", " ", text.strip().lower())
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return False

        direct_phrases = {
            "exit skill",
            "exit this skill",
            "exit current skill",
            "leave skill",
            "leave this skill",
            "stop this skill",
            "cancel this skill",
            "go back to listening",
            "return to listening",
            "back to listening",
            "listening mode",
        }
        if cleaned in direct_phrases:
            return True

        return bool(
            re.fullmatch(
                r"(?:please\s+)?(?:jarvis\s+)?(?:"
                r"(?:exit|leave|stop|cancel)\s+(?:this\s+|current\s+)?skill|"
                r"(?:go\s+back|return)\s+to\s+listening(?:\s+mode)?|"
                r"back\s+to\s+listening(?:\s+mode)?"
                r")(?:\s+please)?",
                cleaned,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _looks_like_calendar_add_phrase(text: str) -> bool:
        cleaned = re.sub(r"\s+", " ", text.strip().lower())
        if not cleaned:
            return False

        if re.search(r"\b(sync|resync|re-sync|synchroni[sz]e|refresh|reload)\b", cleaned):
            return False

        has_add_verb = bool(re.search(r"\b(add|create|schedule|book|put|set up)\b", cleaned))
        if not has_add_verb:
            return False

        if "calendar" in cleaned:
            return True

        return bool(re.search(r"\b(event|meeting|appointment)\b", cleaned))

    @staticmethod
    def _looks_like_general_topic_shift(text: str) -> bool:
        cleaned = re.sub(r"\s+", " ", text.strip().lower())
        if not cleaned:
            return False

        # Keep terse confirmations in the current clarification flow.
        if re.fullmatch(
            r"(?:yes|yeah|yep|yup|ok|okay|sure|correct|right|exactly|affirmative)"
            r"(?:[\s,.:;-]+(?:please|thanks|thank you))?",
            cleaned,
            flags=re.IGNORECASE,
        ):
            return False

        if cleaned.endswith("?"):
            return True

        return bool(
            re.match(
                r"^(?:who|what|when|where|why|how|are you|do you|"
                r"can you|could you|would you|tell me|explain|help me understand)\b",
                cleaned,
                flags=re.IGNORECASE,
            )
        )

    def _handle_pending_clarification(
        self,
        payload: AskRequest,
        session: SessionRecord,
    ) -> dict[str, Any] | None:
        pending = self._pending_clarification(session)
        if pending is None:
            return None

        intent = self._coerce_intent(str(pending.get("intent") or ""))
        if intent is None:
            self._clear_pending_clarification(session)
            return None

        if self._looks_like_cancel_phrase(payload.text):
            self._cancel_pending_interaction(
                session=session,
                reason="user_cancelled_pending_flow",
            )
            self._clear_main_sticky_followup(session)
            self._set_owner(session, SessionOwner.MAIN)
            self._set_state(session, SessionState.IDLE)
            classification = {
                "intent": Intent.CONVERSATIONAL.value,
                "confidence": 0.98,
                "entities": {},
                "ambiguity_flags": ["cancelled_pending_clarification"],
                "recommended_owner": SessionOwner.MAIN.value,
                "reasoning": "user_cancelled_pending_flow",
                "cancelled_intent": intent.value,
            }
            result = {
                "status": "cancelled",
                "message": "Okay, cancelled. I did not make any changes.",
                "cancelled_intent": intent.value,
            }
            return self._build_response(
                session=session,
                intent=Intent.CONVERSATIONAL,
                classification=classification,
                route="main_jarvis_repair",
                result=result,
                request_text=payload.text,
                user_id=payload.user_id,
            )

        if self._should_interrupt_pending_clarification(
            payload=payload,
            session=session,
            pending_intent=intent,
        ):
            self._clear_pending_clarification(session)
            self._set_state(session, SessionState.IDLE)
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
        if not isinstance(entities, dict):
            entities = {}
        merged_entities = self._normalize_entities_for_intent(intent=intent, entities=dict(entities))
        pending_missing = pending.get("missing_fields")
        if not isinstance(pending_missing, list):
            pending_missing = []
        pending_missing = [str(item) for item in pending_missing if str(item).strip()]
        is_conversation_pending = self._is_conversation_pending_flow(
            pending=pending,
            intent=intent,
        )

        model_updates: dict[str, Any] = {}
        if not is_conversation_pending:
            model_updates = self._extract_clarification_updates_with_main_repair(
                session=session,
                payload=payload,
                intent=intent,
                missing_fields=pending_missing,
                current_entities=merged_entities,
            )
        safe_context_updates = self._extract_safe_contextual_clarification_updates(
            session=session,
            intent=intent,
            text=payload.text,
            missing_fields=pending_missing,
            current_entities=merged_entities,
        )
        updates: dict[str, Any] = {}
        for key, value in safe_context_updates.items():
            if value is None:
                continue
            updates[key] = value
        for key, value in model_updates.items():
            if value is None:
                continue
            updates[key] = value
        for key, value in updates.items():
            if value is None:
                continue
            if isinstance(value, str):
                trimmed = value.strip()
                if not trimmed:
                    continue
                merged_entities[key] = trimmed
            else:
                merged_entities[key] = value

        merged_entities = self._normalize_entities_for_intent(intent=intent, entities=merged_entities)
        if is_conversation_pending:
            missing_fields = self._pending_fields_remaining(
                pending_fields=pending_missing,
                entities=merged_entities,
            )
        else:
            missing_fields = self._required_fields_for_intent(intent=intent, entities=merged_entities)
            missing_fields = self._merge_missing_fields(
                missing_fields,
                self._pending_fields_remaining(
                    pending_fields=pending_missing,
                    entities=merged_entities,
                ),
            )
        if missing_fields:
            question = str(pending.get("question") or "").strip() or None
            if question is None:
                question = self._clarification_question(intent=intent, field_name=missing_fields[0])
            continued = self._continue_pending_interaction(
                session=session,
                entities=merged_entities,
                missing_fields=missing_fields,
                question=question,
            )
            if not continued:
                self._store_pending_clarification(
                    session=session,
                    intent=intent,
                    entities=merged_entities,
                    missing_fields=missing_fields,
                    question=question,
                )
            self._arm_main_sticky_followup(session=session, reason="pending_clarification_continue")
            self._set_owner(session, SessionOwner.MAIN)
            self._set_state(session, SessionState.AWAITING_CONFIRMATION)
            classification = {
                "intent": intent.value,
                "confidence": 0.64,
                "entities": merged_entities,
                "ambiguity_flags": ["clarification_pending"],
                "recommended_owner": SessionOwner.MAIN.value,
                "reasoning": "pending_clarification_continue",
                "repair_status": "needs_clarification",
            }
            result = {
                "status": "needs_clarification",
                "message": "Thanks. I still need one detail before I can run that.",
                "question": question,
                "missing_fields": missing_fields,
                "entities": merged_entities,
                "repaired_by": "main_jarvis",
                "repair_source": "clarification_followup",
            }
            return self._build_response(
                session=session,
                intent=intent,
                classification=classification,
                route="main_jarvis_repair",
                result=result,
                request_text=payload.text,
                user_id=payload.user_id,
            )

        if is_conversation_pending:
            return self._complete_pending_conversation_followup(
                session=session,
                payload=payload,
                intent=intent,
                merged_entities=merged_entities,
                pending=pending,
            )

        repaired = MicroDecision(
            intent=intent,
            confidence=0.8,
            entities=merged_entities,
            ambiguity_flags=["clarification_completed"],
            recommended_owner=SessionOwner.MAIN,
            reasoning="pending_clarification_completed",
        )
        resolved_skill = self._resolve_skill_for_intent(
            intent=repaired.intent,
            user_id=payload.user_id,
            agent_id=self._active_agent_id(session),
        )
        if self._action_ticket_service is not None:
            started = self._action_ticket_service.begin_request(
                request_id=str(self._request_id_var.get() or payload.request_id or uuid4()),
                session_id=session.session_id,
                context_reference=session.context_reference,
                user_id=payload.user_id,
                agent_id=self._active_agent_id(session),
                source=payload.source,
                intent=repaired.intent.value,
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
        self._set_owner(session, SessionOwner.MAIN)
        self._set_state(session, SessionState.ERROR_RECOVERY)
        tool_result = self._execute_fast_command(
            decision=repaired,
            source_interface=payload.source,
            requested_by_user_id=payload.user_id,
            resolved_skill=resolved_skill,
            agent_id=self._active_agent_id(session),
            request_context=payload.context,
        )
        self._event_log.record(
            event_type="main.repair.clarification.executed",
            session_id=session.session_id,
            payload={
                "intent": repaired.intent.value,
                "result_status": tool_result.get("status"),
            },
        )
        followup_response = self._maybe_open_tool_followup(
            session=session,
            decision=repaired,
            tool_result=tool_result,
            request_text=payload.text,
            user_id=payload.user_id,
        )
        if followup_response is not None:
            return followup_response
        self._clear_pending_clarification(session)
        self._set_state(session, SessionState.IDLE)
        classification = repaired.to_dict()
        classification["repair_status"] = "resolved_action"
        classification["repair_source"] = "clarification_followup"
        result = dict(tool_result)
        result["repaired_by"] = "main_jarvis"
        result["repair_source"] = "clarification_followup"
        return self._build_response(
            session=session,
            intent=repaired.intent,
            classification=classification,
            route="main_jarvis_repair",
            result=result,
            request_text=payload.text,
            user_id=payload.user_id,
        )

    @staticmethod
    def _is_conversation_pending_flow(*, pending: dict[str, Any], intent: Intent) -> bool:
        kind_value = str(pending.get("kind") or "").strip().lower()
        if kind_value.startswith("conversation"):
            return True
        return intent in {Intent.CONVERSATIONAL, Intent.UNKNOWN}

    def _pending_fields_remaining(self, *, pending_fields: list[str], entities: dict[str, Any]) -> list[str]:
        remaining: list[str] = []
        for field_name in pending_fields:
            cleaned = str(field_name).strip()
            if not cleaned:
                continue
            value = entities.get(cleaned)
            if self._entity_value_present(value):
                continue
            remaining.append(cleaned)
        return self._normalize_missing_field_list(remaining)

    def _complete_pending_conversation_followup(
        self,
        *,
        session: SessionRecord,
        payload: AskRequest,
        intent: Intent,
        merged_entities: dict[str, Any],
        pending: dict[str, Any],
    ) -> dict[str, Any]:
        pending_question = str(pending.get("question") or "").strip()
        pending_metadata = pending.get("metadata")
        if not isinstance(pending_metadata, dict):
            pending_metadata = {}
        topic_subject = (
            str(merged_entities.get("topic_subject") or "").strip()
            or str(merged_entities.get("topic_entity") or "").strip()
        )
        confirmation = str(merged_entities.get("confirmation") or "").strip().lower()
        followup_prompt = self._conversation_followup_prompt(
            user_text=payload.text,
            pending_question=pending_question,
            topic_subject=topic_subject,
            confirmation=confirmation,
        )
        working_context = self._build_working_context_packet(
            session=session,
            user_id=payload.user_id,
            request_text=payload.text,
            route_hint="main_pending_conversation_followup",
            intent_hint=intent.value,
        ).to_dict()
        pending_agent_id = str(payload.context.get("agent_id") or "jarvis").strip().lower() or "jarvis"
        response = self._main_jarvis.respond(
            text=followup_prompt,
            context={
                "micro_intent": Intent.CONVERSATIONAL.value,
                "micro_confidence": 0.72,
                "micro_entities": merged_entities,
                "micro_ambiguity_flags": ["conversation_clarification_completed"],
                "runtime_skill_intents": [Intent.CONVERSATIONAL.value],
                "runtime_capability_catalog": self._runtime_capability_catalog(
                    payload=payload,
                    agent_id=pending_agent_id,
                ),
                "working_context": working_context,
                "session_summary": working_context.get("session_summary"),
                "recent_turns": working_context.get("recent_turns"),
                "entity_hints": working_context.get("entity_hints"),
                "pending_interaction": working_context.get("pending_interaction"),
                "budget_metadata": working_context.get("budget_metadata"),
                "pending_conversation": {
                    "question_type": str(pending_metadata.get("question_type") or "").strip() or None,
                    "question": pending_question or None,
                    "resolved_context": merged_entities,
                },
                "agent_id": pending_agent_id,
                "agent_display_name": str(payload.context.get("agent_display_name") or "Jarvis"),
                "requested_by_user_id": payload.user_id,
            },
        )
        if not isinstance(response, dict):
            response = {
                "status": "conversation",
                "message": str(response or "").strip() or "I can continue from that clarification now.",
            }
        response = dict(response)
        response["repair_source"] = "clarification_followup"
        response.setdefault("conversation_subject", topic_subject or None)
        self._clear_pending_clarification(session)
        self._clear_main_sticky_followup(session)
        self._set_owner(session, SessionOwner.MAIN)
        result_status = str(response.get("status") or "").strip().lower()
        if result_status in {"conversation", "planned"}:
            self._set_state(session, SessionState.CONVERSATIONAL)
        else:
            self._set_state(session, SessionState.IDLE)
        classification = {
            "intent": Intent.CONVERSATIONAL.value,
            "confidence": 0.76,
            "entities": merged_entities,
            "ambiguity_flags": ["conversation_clarification_completed"],
            "recommended_owner": SessionOwner.MAIN.value,
            "reasoning": "pending_conversation_completed",
            "repair_status": "conversation_resolved",
            "repair_source": "clarification_followup",
        }
        return self._build_response(
            session=session,
            intent=Intent.CONVERSATIONAL,
            classification=classification,
            route="main_jarvis_repair",
            result=response,
            request_text=payload.text,
            user_id=payload.user_id,
        )

    @staticmethod
    def _conversation_followup_prompt(
        *,
        user_text: str,
        pending_question: str,
        topic_subject: str,
        confirmation: str,
    ) -> str:
        cleaned_user = re.sub(r"\s+", " ", str(user_text or "").strip())
        if pending_question and topic_subject:
            return (
                f"Question to continue: {pending_question}\n"
                f"Resolved subject: {topic_subject}\n"
                f"User follow-up: {cleaned_user or topic_subject}"
            )
        if pending_question and confirmation in {"yes", "no"}:
            return (
                f"Question to continue: {pending_question}\n"
                f"User confirmation: {confirmation}\n"
                f"User follow-up: {cleaned_user or confirmation}"
            )
        if pending_question:
            return (
                f"Question to continue: {pending_question}\n"
                f"User follow-up: {cleaned_user}"
            )
        return cleaned_user

    def _should_interrupt_pending_clarification(
        self,
        *,
        payload: AskRequest,
        session: SessionRecord,
        pending_intent: Intent,
    ) -> bool:
        if not self._micro_command_enabled(payload):
            return self._looks_like_general_topic_shift(payload.text)

        candidate = self._micro_jarvis.interpret(
            text=payload.text,
            context={
                "session_state": session.state.value,
                "session_owner": session.owner.value,
                "pending_intent": pending_intent.value,
                "execution_origin": "pending_clarification_probe",
            },
        )
        candidate = self._resolve_followup_entities(session=session, decision=candidate)
        candidate = self._normalize_decision_entities(candidate)

        if candidate.intent in {Intent.SYSTEM_SLEEP, Intent.SYSTEM_WAKE}:
            return True
        if candidate.intent in {Intent.UNKNOWN, Intent.CONVERSATIONAL}:
            return self._looks_like_general_topic_shift(payload.text)
        if candidate.intent == pending_intent:
            return False
        if candidate.confidence < 0.72:
            return False
        if candidate.intent in FAST_COMMAND_INTENTS:
            missing = self._required_fields_for_intent(intent=candidate.intent, entities=candidate.entities)
            if missing:
                return False
        return True

    def _required_fields_for_intent(self, intent: Intent, entities: dict[str, Any]) -> list[str]:
        intent_value = intent.value
        missing: list[str] = []
        saw_required_hook = False
        for contract in self._skill_context_contracts:
            if not contract.supports_intent(intent=intent_value):
                continue
            required_hook = getattr(contract, "required_fields", None)
            if not callable(required_hook):
                continue
            try:
                contract_required = required_hook(
                    intent=intent_value,
                    entities=dict(entities),
                    resolver=self._reference_resolver,
                )
            except Exception:  # pragma: no cover - defensive contract isolation
                continue
            if not isinstance(contract_required, list):
                continue
            saw_required_hook = True
            missing = self._merge_missing_fields(missing, contract_required)

        if not saw_required_hook:
            missing = self._fallback_required_fields_for_intent(intent=intent, entities=entities)

        missing = self._normalize_missing_field_list(missing)
        for contract in self._skill_context_contracts:
            if not contract.supports_intent(intent=intent_value):
                continue
            refine_hook = getattr(contract, "refine_missing_fields", None)
            if not callable(refine_hook):
                continue
            try:
                refined = refine_hook(
                    intent=intent_value,
                    entities=dict(entities),
                    missing_fields=list(missing),
                    resolver=self._reference_resolver,
                )
            except Exception:  # pragma: no cover - defensive contract isolation
                continue
            if isinstance(refined, list):
                missing = self._normalize_missing_field_list(refined)
        return self._normalize_missing_field_list(missing)

    @staticmethod
    def _merge_missing_fields(base: list[str], candidate: list[str]) -> list[str]:
        merged = [str(item).strip() for item in base if str(item).strip()]
        seen = {item.lower() for item in merged}
        for raw in candidate:
            field_name = str(raw).strip()
            if not field_name:
                continue
            lowered = field_name.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            merged.append(field_name)
        return merged

    @staticmethod
    def _normalize_missing_field_list(value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            field_name = str(raw).strip()
            if not field_name:
                continue
            lowered = field_name.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(field_name)
        return normalized

    @staticmethod
    def _fallback_required_fields_for_intent(intent: Intent, entities: dict[str, Any]) -> list[str]:
        del intent
        del entities
        return []

    def _extract_safe_contextual_clarification_updates(
        self,
        *,
        session: SessionRecord,
        intent: Intent,
        text: str,
        missing_fields: list[str],
        current_entities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not text.strip() or not missing_fields:
            return {}
        intent_value = intent.value
        updates: dict[str, Any] = {}
        entities = current_entities if isinstance(current_entities, dict) else {}
        for contract in self._skill_context_contracts:
            if not contract.supports_intent(intent=intent_value):
                continue
            try:
                contract_updates = contract.continue_pending_interaction(
                    intent=intent_value,
                    text=text,
                    missing_fields=list(missing_fields),
                    current_entities=dict(entities),
                )
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                self._event_log.record(
                    event_type="context.contract.continue_pending_interaction.failed",
                    session_id=session.session_id,
                    payload={
                        "contract_id": str(getattr(contract, "contract_id", "") or ""),
                        "intent": intent_value,
                        "error": str(exc),
                    },
                )
                continue
            if not isinstance(contract_updates, dict):
                continue
            for key, value in contract_updates.items():
                if value is None:
                    continue
                updates[str(key)] = value
        return updates

    def _extract_clarification_updates_with_main_repair(
        self,
        *,
        session: SessionRecord,
        payload: AskRequest,
        intent: Intent,
        missing_fields: list[str],
        current_entities: dict[str, Any],
    ) -> dict[str, Any]:
        text = payload.text
        working_context = self._build_working_context_packet(
            session=session,
            user_id=session.user_id,
            request_text=text,
            route_hint="main_repair_clarification",
            intent_hint=intent.value,
        ).to_dict()
        repair = self._main_jarvis.repair_action(
            text=text,
            context={
                "micro_intent": intent.value,
                "micro_confidence": 0.0,
                "micro_entities": current_entities,
                "micro_ambiguity_flags": ["pending_clarification_main_resolution"],
                "required_missing_fields": missing_fields,
                "pending_intent": intent.value,
                "pending_entities": current_entities,
                "pending_missing_fields": missing_fields,
                "runtime_skill_intents": [intent.value],
                "runtime_capability_catalog": self._runtime_capability_catalog(
                    payload=payload,
                    agent_id=self._active_agent_id(session),
                ),
                "working_context": working_context,
                "session_summary": working_context.get("session_summary"),
                "recent_turns": working_context.get("recent_turns"),
                "entity_hints": working_context.get("entity_hints"),
                "pending_interaction": working_context.get("pending_interaction"),
                "budget_metadata": working_context.get("budget_metadata"),
                "agent_id": self._active_agent_id(session),
                "agent_display_name": str(
                    session.context_reference.get("agent_display_name")
                    or session.context_reference.get("active_agent_id")
                    or "Jarvis"
                ),
                "requested_by_user_id": session.user_id,
            },
        )
        repair_status = str(repair.get("status") or "").strip().lower()
        repair_source = str(repair.get("source") or "").strip().lower()
        repair_intent = self._coerce_intent(str(repair.get("intent") or ""))
        self._event_log.record(
            event_type="main.repair.clarification.attempted",
            session_id=session.session_id,
            payload={
                "pending_intent": intent.value,
                "repair_status": repair_status,
                "repair_intent": repair_intent.value if repair_intent is not None else None,
                "repair_source": repair_source or None,
                "required_missing_fields": missing_fields,
            },
        )
        if (
            not self._main_pending_clarification_heuristic_fallback_enabled
            and repair_source.startswith("heuristic")
        ):
            return {}
        if repair_status not in {"resolved_action", "needs_clarification"}:
            return {}
        if repair_intent != intent:
            return {}

        entities = repair.get("entities")
        if not isinstance(entities, dict):
            return {}
        normalized = self._normalize_entities_for_intent(intent=intent, entities=entities)

        allowed_fields = set(missing_fields)
        if intent == Intent.CALENDAR_ADD_EVENT:
            allowed_fields.update({"invitee_names", "invite_explicit"})
        updates: dict[str, Any] = {}
        for field_name in allowed_fields:
            value = normalized.get(field_name)
            if not self._entity_value_present(value):
                continue
            updates[field_name] = value
        if intent == Intent.CALENDAR_ADD_EVENT:
            explicit_invitees = self._extract_calendar_invitee_names(text)
            if explicit_invitees:
                updates["invitee_names"] = explicit_invitees
                updates["invite_explicit"] = True
            else:
                updates.pop("invitee_names", None)
                updates.pop("invite_explicit", None)
        return updates

    @staticmethod
    def _entity_value_present(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, list):
            return any(str(item).strip() for item in value)
        return True

    def _clarification_question(self, intent: Intent, field_name: str) -> str:
        intent_value = intent.value
        cleaned_field = str(field_name or "").strip() or "that field"
        for contract in self._skill_context_contracts:
            if not contract.supports_intent(intent=intent_value):
                continue
            question_hook = getattr(contract, "clarification_question", None)
            if not callable(question_hook):
                continue
            try:
                candidate = question_hook(intent=intent_value, field_name=cleaned_field)
            except Exception:  # pragma: no cover - defensive contract isolation
                continue
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return f"What should I use for `{cleaned_field}`?"

    @staticmethod
    def _extract_calendar_invitee_names(text: str) -> list[str]:
        invite_patterns = [
            r"\binvite(?:\s+(?P<names>[a-z][a-z\s,'&.-]+))?$",
            r"\binvite\s+(?P<names>[a-z][a-z\s,'&.-]+)\b",
            r"\bsend(?:\s+it)?\s+to\s+(?P<names>[a-z][a-z\s,'&.-]+)\b",
            r"\bsend\s+invite(?:s)?\s+to\s+(?P<names>[a-z][a-z\s,'&.-]+)\b",
            r"\badd\s+attendee(?:s)?\s+(?:to\s+this\s+event\s+)?(?P<names>[a-z][a-z\s,'&.-]+)\b",
        ]
        names_text = ""
        for pattern in invite_patterns:
            match = re.search(pattern, text.strip(), flags=re.IGNORECASE)
            if not match:
                continue
            names_text = str(match.groupdict().get("names") or "").strip(" .")
            if names_text:
                break
        if not names_text:
            return []
        names_text = re.sub(r"^(?:to\s+)?", "", names_text, flags=re.IGNORECASE).strip()
        parts = re.split(r"\s*(?:,| and | & )\s*", names_text)
        names = [
            part.strip(" .,'\"")
            for part in parts
            if part.strip(" .,'\"")
            and part.strip(" .,'\"").lower() not in {"him", "her", "them", "everyone", "all"}
        ]
        return JarvisRouter._dedupe_names(names)

    def _legacy_main_handoff_context(
        self,
        *,
        session: SessionRecord,
        intent: str | None = None,
        route: str | None = None,
    ) -> dict[str, Any]:
        registry = self._entity_registry_manager.get_registry(session=session)
        context_reference = dict(session.context_reference)
        runtime_context = self._runtime_main_handoff_context()
        hints: dict[str, Any] = {}
        for contract in self._skill_context_contracts:
            try:
                contract_hints = contract.legacy_main_handoff_hints(
                    registry=registry,
                    context_reference=context_reference,
                    runtime_context=runtime_context,
                    intent=intent,
                    route=route,
                )
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                self._event_log.record(
                    event_type="context.contract.legacy_main_handoff_hints.failed",
                    session_id=session.session_id,
                    payload={
                        "contract_id": str(getattr(contract, "contract_id", "") or ""),
                        "error": str(exc),
                    },
                )
                continue
            if not isinstance(contract_hints, dict):
                continue
            for key, value in contract_hints.items():
                field_name = str(key).strip()
                if not field_name:
                    continue
                if value is None:
                    continue
                hints[field_name] = value
        return hints

    def _runtime_main_handoff_context(self) -> dict[str, Any]:
        return {
            "available_switches": self._home_service.list_switches(),
        }

    @staticmethod
    def _active_agent_id(session: SessionRecord) -> str:
        value = session.context_reference.get("active_agent_id")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
        return "jarvis"

    @staticmethod
    def _main_sticky_followup_turns_remaining(session: SessionRecord) -> int:
        value = session.context_reference.get("main_sticky_followup_turns_remaining")
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        return 0

    def _arm_main_sticky_followup(
        self,
        *,
        session: SessionRecord,
        reason: str,
        turns: int | None = None,
    ) -> None:
        configured_turns = self._main_sticky_followup_turns if turns is None else int(turns)
        if configured_turns <= 0:
            return
        context_reference = dict(session.context_reference)
        context_reference["main_sticky_followup_turns_remaining"] = max(1, configured_turns)
        context_reference["main_sticky_followup_reason"] = str(reason or "clarification")
        session.context_reference = context_reference
        session.touch()
        self._session_store.save(session)

    def _consume_main_sticky_followup_turn(self, session: SessionRecord) -> int:
        remaining = self._main_sticky_followup_turns_remaining(session)
        if remaining <= 0:
            return 0
        next_remaining = max(0, remaining - 1)
        context_reference = dict(session.context_reference)
        if next_remaining > 0:
            context_reference["main_sticky_followup_turns_remaining"] = next_remaining
        else:
            context_reference.pop("main_sticky_followup_turns_remaining", None)
            context_reference.pop("main_sticky_followup_reason", None)
        session.context_reference = context_reference
        session.touch()
        self._session_store.save(session)
        return next_remaining

    def _clear_main_sticky_followup(self, session: SessionRecord) -> None:
        if self._main_sticky_followup_turns_remaining(session) <= 0:
            return
        context_reference = dict(session.context_reference)
        context_reference.pop("main_sticky_followup_turns_remaining", None)
        context_reference.pop("main_sticky_followup_reason", None)
        session.context_reference = context_reference
        session.touch()
        self._session_store.save(session)

    def _apply_main_sticky_followup(self, *, session: SessionRecord, decision: MicroDecision) -> MicroDecision:
        remaining = self._main_sticky_followup_turns_remaining(session)
        if remaining <= 0:
            return decision
        if decision.intent in {Intent.SYSTEM_SLEEP, Intent.SYSTEM_WAKE}:
            self._clear_main_sticky_followup(session)
            return decision
        if decision.recommended_owner != SessionOwner.MAIN:
            decision.recommended_owner = SessionOwner.MAIN
        if "main_sticky_followup" not in decision.ambiguity_flags:
            decision.ambiguity_flags.append("main_sticky_followup")
        if decision.reasoning:
            decision.reasoning = f"{decision.reasoning}_main_sticky_followup"
        else:
            decision.reasoning = "main_sticky_followup"
        next_remaining = self._consume_main_sticky_followup_turn(session)
        self._event_log.record(
            event_type="main.sticky_followup.consumed",
            session_id=session.session_id,
            payload={
                "remaining_before": remaining,
                "remaining_after": next_remaining,
                "intent": decision.intent.value,
            },
        )
        return decision

    @staticmethod
    def _main_agent_token_session(session: SessionRecord) -> dict[str, Any]:
        raw = session.context_reference.get("main_agent_token_session")
        if isinstance(raw, dict):
            return dict(raw)
        return {"turn_summaries": [], "total_turns": 0}

    def _update_main_agent_token_session(
        self,
        *,
        session: SessionRecord,
        goal_text: str,
        execution: dict[str, Any],
    ) -> None:
        if not self._main_agent_token_session_enabled:
            return

        current = self._main_agent_token_session(session)
        existing_summaries = current.get("turn_summaries")
        if not isinstance(existing_summaries, list):
            existing_summaries = []
        summaries = [str(item).strip() for item in existing_summaries if str(item).strip()]

        requested_count = int(execution.get("requested_count") or 0)
        success_count = int(execution.get("success_count") or 0)
        loop_state = str(execution.get("loop_state") or "").strip() or "UNKNOWN"
        status = str(execution.get("status") or "").strip() or "unknown"
        agent_loop = execution.get("agent_loop")
        terminal_message = ""
        context_budget: dict[str, Any] = {}
        if isinstance(agent_loop, dict):
            terminal_message = str(agent_loop.get("terminal_message") or "").strip()
            maybe_budget = agent_loop.get("context_budget")
            if isinstance(maybe_budget, dict):
                context_budget = maybe_budget

        summary = (
            f"goal={self._truncate_for_token_session(goal_text, 72)} | "
            f"status={status} | loop={loop_state} | steps={success_count}/{requested_count}"
        )
        if terminal_message:
            summary = f"{summary} | note={self._truncate_for_token_session(terminal_message, 72)}"
        summaries.append(summary)
        if len(summaries) > self._main_agent_token_session_max_turns:
            summaries = summaries[-self._main_agent_token_session_max_turns :]

        updated = {
            "turn_summaries": summaries,
            "total_turns": int(current.get("total_turns") or 0) + 1,
            "last_status": status,
            "last_loop_state": loop_state,
            "last_used_tokens_estimate": int(context_budget.get("used_tokens_estimate") or 0),
            "last_max_tokens_estimate": int(context_budget.get("max_tokens_estimate") or 0),
            "last_trimmed": bool(context_budget.get("trimmed")),
            "last_compaction": context_budget.get("compaction") if isinstance(context_budget, dict) else None,
        }
        context_reference = dict(session.context_reference)
        context_reference["main_agent_token_session"] = updated
        session.context_reference = context_reference
        session.touch()
        self._session_store.save(session)

    @staticmethod
    def _truncate_for_token_session(value: str, limit: int) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(cleaned) <= limit:
            return cleaned
        return f"{cleaned[: max(0, limit - 3)]}..."

    def _update_session_context_from_result(
        self,
        *,
        session: SessionRecord,
        intent: Intent,
        result: dict[str, Any],
    ) -> None:
        status = str(result.get("status") or "").strip().lower()
        if status not in {"ok", "partial"}:
            return
        intent_value = intent.value
        emitted_entities: list[dict[str, Any]] = []
        for contract in self._skill_context_contracts:
            if not contract.supports_intent(intent=intent_value):
                continue
            try:
                emitted_entities.extend(contract.emit_context_updates(intent=intent_value, result=result))
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                self._event_log.record(
                    event_type="context.contract.emit_context_updates.failed",
                    session_id=session.session_id,
                    payload={
                        "contract_id": str(getattr(contract, "contract_id", "") or ""),
                        "intent": intent_value,
                        "status": status,
                        "error": str(exc),
                    },
                )

        if not emitted_entities:
            return
        update = self._entity_registry_manager.record_entities(
            session=session,
            entities=emitted_entities,
        )
        if not bool(update.get("updated")):
            return
        session.touch()
        self._session_store.save(session)
        self._event_log.record(
            event_type="context.entity_registry.updated",
            session_id=session.session_id,
            payload={
                "intent": intent_value,
                "status": status,
                "upserted_count": int(update.get("upserted_count") or 0),
                "total_entities": int(update.get("total_entities") or 0),
                "emitted_entities": [
                    {
                        "domain": str(item.get("domain") or "").strip().lower(),
                        "entity_type": str(item.get("entity_type") or "").strip().lower(),
                        "display_name": str(item.get("display_name") or "").strip(),
                    }
                    for item in emitted_entities
                    if isinstance(item, dict)
                ],
            },
        )

    def _store_pending_clarification(
        self,
        session: SessionRecord,
        intent: Intent,
        entities: dict[str, Any],
        missing_fields: list[str],
        question: str | None,
        *,
        kind: str = "missing_field",
        skill_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        before_snapshot = self._pending_interaction_snapshot(session=session)
        pending_metadata = {"source": "router._store_pending_clarification"}
        if isinstance(metadata, dict):
            pending_metadata.update(metadata)
        pending = self._pending_interaction_manager.set_pending_interaction(
            session=session,
            intent=intent.value,
            entities=dict(entities),
            missing_fields=[str(item) for item in missing_fields if str(item).strip()],
            question=question,
            kind=kind,
            status="pending",
            skill_id=skill_id,
            metadata=pending_metadata,
        )
        session.touch()
        self._session_store.save(session)
        self._record_pending_interaction_transition(
            session=session,
            action="set",
            before=before_snapshot,
            after=self._pending_snapshot_from_object(pending),
            reason="router._store_pending_clarification",
        )

    def _clear_pending_clarification(self, session: SessionRecord) -> None:
        before_snapshot = self._pending_interaction_snapshot(session=session)
        cleared = self._pending_interaction_manager.clear_pending_interaction(session=session)
        if not cleared:
            return
        session.touch()
        self._session_store.save(session)
        self._record_pending_interaction_transition(
            session=session,
            action="clear",
            before=before_snapshot,
            after=None,
            reason="router._clear_pending_clarification",
        )

    def _cancel_pending_interaction(self, *, session: SessionRecord, reason: str) -> bool:
        before_snapshot = self._pending_interaction_snapshot(session=session)
        cancelled = self._pending_interaction_manager.cancel_pending_interaction(
            session=session,
            reason=reason,
        )
        if cancelled:
            session.touch()
            self._session_store.save(session)
            self._record_pending_interaction_transition(
                session=session,
                action="cancel",
                before=before_snapshot,
                after=None,
                reason=reason,
            )
        return cancelled

    def _continue_pending_interaction(
        self,
        *,
        session: SessionRecord,
        entities: dict[str, Any],
        missing_fields: list[str],
        question: str | None,
    ) -> bool:
        before_snapshot = self._pending_interaction_snapshot(session=session)
        updated = self._pending_interaction_manager.continue_pending_interaction(
            session=session,
            entities=dict(entities),
            missing_fields=[str(item) for item in missing_fields if str(item).strip()],
            question=question,
            status="pending",
            metadata_updates={"source": "router._continue_pending_interaction"},
        )
        if updated is None:
            return False
        session.touch()
        self._session_store.save(session)
        self._record_pending_interaction_transition(
            session=session,
            action="continue",
            before=before_snapshot,
            after=self._pending_snapshot_from_object(updated),
            reason="router._continue_pending_interaction",
        )
        return True

    def _pending_clarification(self, session: SessionRecord) -> dict[str, Any] | None:
        before_snapshot = self._pending_interaction_snapshot(session=session)
        expired = self._pending_interaction_manager.expire_stale_pending_interaction(session=session)
        if expired:
            session.touch()
            self._session_store.save(session)
            self._event_log.record(
                event_type="pending.interaction.expired",
                session_id=session.session_id,
                payload={"reason": "ttl_expired"},
            )
            self._record_pending_interaction_transition(
                session=session,
                action="expired",
                before=before_snapshot,
                after=None,
                reason="ttl_expired",
            )
        pending = self._pending_interaction_manager.get_pending_legacy_payload(
            session=session,
            expire_stale=False,
        )
        if pending is None:
            return None
        return pending

    def _pending_interaction_snapshot(self, *, session: SessionRecord) -> dict[str, Any] | None:
        pending = self._pending_interaction_manager.get_pending_interaction(
            session=session,
            expire_stale=False,
        )
        return self._pending_snapshot_from_object(pending)

    @staticmethod
    def _pending_snapshot_from_object(pending: Any) -> dict[str, Any] | None:
        if pending is None:
            return None
        intent_value = str(getattr(pending, "intent", "") or "").strip() or None
        return {
            "kind": str(getattr(pending, "kind", "") or "").strip() or None,
            "intent": intent_value,
            "status": str(getattr(pending, "status", "") or "").strip() or None,
            "expected_fields": [
                str(item).strip()
                for item in (getattr(pending, "expected_fields", []) or [])
                if str(item).strip()
            ],
            "question": str(getattr(pending, "question", "") or "").strip() or None,
            "expires_at": str(getattr(pending, "expires_at", "") or "").strip() or None,
        }

    def _record_pending_interaction_transition(
        self,
        *,
        session: SessionRecord,
        action: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        reason: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "action": str(action or "").strip().lower() or "unknown",
            "before": dict(before) if isinstance(before, dict) else None,
            "after": dict(after) if isinstance(after, dict) else None,
            "had_pending_before": isinstance(before, dict),
            "has_pending_after": isinstance(after, dict),
        }
        if isinstance(reason, str) and reason.strip():
            payload["reason"] = reason.strip()
        self._event_log.record(
            event_type="context.pending_interaction.transition",
            session_id=session.session_id,
            payload=payload,
        )

    def _execute_fast_command(
        self,
        decision: MicroDecision,
        source_interface: str,
        requested_by_user_id: str,
        *,
        resolved_skill: dict[str, Any] | None = None,
        agent_id: str | None = None,
        request_id: str | None = None,
        request_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        effective_agent_id = str(agent_id or "jarvis").strip().lower() or "jarvis"
        return self._authorized_skill_executor.execute(
            intent=decision.intent.value,
            entities=decision.entities,
            source_interface=source_interface,
            requested_by_user_id=requested_by_user_id,
            agent_id=effective_agent_id,
            request_context=request_context,
            request_id=request_id or self._request_id_var.get(),
            resolved_skill=resolved_skill,
        )

    def _execute_main_plan(
        self,
        plan: dict[str, Any],
        session: SessionRecord,
        session_id: str,
        source_interface: str,
        requested_by_user_id: str,
        agent_id: str,
        goal_text: str = "",
        request_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token_session = self._main_agent_token_session(session)
        loop = MainAgentLoop(
            planner=self._main_agent_planner,
            evaluator=self._main_agent_evaluator,
            context_budget=self._main_agent_context_budget,
            limits=self._main_agent_limits,
            event_hook=lambda event_type, payload: self._event_log.record(
                event_type=event_type,
                session_id=session_id,
                payload=payload,
            ),
        )
        executor = MainAgentExecutor(
            micro_jarvis=self._micro_jarvis,
            run_fast_command=lambda decision, planner_decision: self._execute_fast_command(
                decision=decision,
                source_interface=source_interface,
                requested_by_user_id=requested_by_user_id,
                agent_id=agent_id,
                request_id=(
                    f"{self._request_id_var.get()}:main-step:{planner_decision.step_number}"
                ),
                request_context=request_context,
            ),
        )
        execution = loop.run(
            goal_text=goal_text,
            plan=plan,
            agent_id=agent_id,
            execution_context={
                "session_id": session_id,
                "source_interface": source_interface,
                "requested_by_user_id": requested_by_user_id,
                "agent_id": agent_id,
                "token_session_turn_summaries": token_session.get("turn_summaries", []),
                **(request_context or {}),
            },
            executor=executor,
            content_policy_gate=self._main_agent_content_policy_gate,
        )

        self._update_main_agent_token_session(
            session=session,
            goal_text=goal_text,
            execution=execution,
        )

        for result in execution.get("results", []):
            if not isinstance(result, dict):
                continue
            classification = result.get("classification")
            intent_value = ""
            if isinstance(classification, dict):
                intent_value = str(classification.get("intent") or "").strip()
            self._event_log.record(
                event_type="main.plan.command.executed",
                session_id=session_id,
                payload={
                    "index": result.get("index"),
                    "command_text": result.get("command_text"),
                    "intent": intent_value,
                    "result_status": result.get("status"),
                },
            )

        agent_loop = execution.get("agent_loop")
        run_id = None
        if isinstance(agent_loop, dict):
            run_id = agent_loop.get("run_id")
        self._event_log.record(
            event_type="main.agent_loop.completed",
            session_id=session_id,
            payload={
                "run_id": run_id,
                "status": execution.get("status"),
                "loop_state": execution.get("loop_state"),
                "requested_count": execution.get("requested_count"),
                "success_count": execution.get("success_count"),
                "failed_count": execution.get("failed_count"),
            },
        )
        return execution

    def _set_owner(self, session: SessionRecord, new_owner: SessionOwner) -> None:
        if session.owner == new_owner:
            return
        previous = session.owner
        session.owner = new_owner
        self._session_store.save(session)
        self._event_log.record(
            event_type="session.owner.changed",
            session_id=session.session_id,
            payload={"from": previous.value, "to": new_owner.value},
        )
        self._event_log.record(
            event_type=f"handoff.{OWNER_LABEL[previous]}_to_{OWNER_LABEL[new_owner]}",
            session_id=session.session_id,
            payload={"from": previous.value, "to": new_owner.value},
        )

    def _set_state(self, session: SessionRecord, new_state: SessionState) -> None:
        if session.state == new_state:
            return
        previous = session.state
        session.state = new_state
        self._session_store.save(session)
        self._event_log.record(
            event_type="session.state.changed",
            session_id=session.session_id,
            payload={"from": previous.value, "to": new_state.value},
        )

    def _build_response(
        self,
        session: SessionRecord,
        intent: Intent,
        classification: dict[str, Any],
        route: str,
        result: dict[str, Any],
        request_text: str,
        user_id: str,
    ) -> dict[str, Any]:
        internal_result_payload = dict(result)
        self._update_session_context_from_result(
            session=session,
            intent=intent,
            result=internal_result_payload,
        )
        if self._action_ticket_service is not None:
            stripped = self._action_ticket_service.strip_internal_fields(internal_result_payload)
            result_payload = stripped if isinstance(stripped, dict) else {}
        else:
            result_payload = internal_result_payload
        main_intent_label = self._main_intent_label(
            route=route,
            intent=intent,
            classification=classification,
            result=result_payload,
        )
        if main_intent_label:
            result_payload["debug_intent_label"] = main_intent_label
        active_agent_id = self._active_agent_id(session)
        confidence_raw = classification.get("confidence")
        confidence = float(confidence_raw) if isinstance(confidence_raw, (float, int)) else None
        skill = self._resolve_skill_for_intent(
            intent=intent,
            user_id=user_id,
            agent_id=active_agent_id,
        )
        self._record_skill_run(
            skill=skill,
            session_id=session.session_id,
            user_id=user_id,
            intent=intent,
            route=route,
            status=str(result_payload.get("status") or "unknown"),
            confidence=confidence,
        )
        if isinstance(skill, dict):
            result_payload.setdefault("debug_skill_id", str(skill.get("skill_id") or ""))
        self._record_runtime_model_activity(
            session_id=session.session_id,
            intent=intent,
            classification=classification,
            route=route,
        )
        model_runtime_status = self._runtime_power.model_runtime_status()
        session_runtime = {
            "last_activity_at": session.last_activity_timestamp,
        }
        channel_runtime = session.context_reference.get("channel_session")
        if isinstance(channel_runtime, dict):
            session_runtime["channel"] = channel_runtime
        response = {
            "request_id": self._request_id_var.get(),
            "session_id": session.session_id,
            "agent_id": active_agent_id,
            "source": session.source,
            "owner": session.owner.value,
            "state": session.state.value,
            "power_state": self._runtime_power.state.value,
            "session_runtime": session_runtime,
            "model_runtime": model_runtime_status,
            "intent": intent.value,
            "classification": classification,
            "route": route,
            "result": result_payload,
            "dialog": self._build_dialog_metadata(
                session=session,
                route=route,
                result=result_payload,
            ),
        }
        response["assistant"] = build_assistant_payload(
            intent=intent.value,
            route=route,
            result=result_payload,
            dialog=response["dialog"],
            show_debug_labels=(
                str(session.context_reference.get("presentation_profile") or "default").strip().lower()
                not in {"child_simple", "minimal", "no_debug"}
            ),
        )
        if self._action_ticket_service is not None:
            request_id = self._request_id_var.get() or str(uuid4())
            capture = self._action_ticket_service.capture_response(
                request_id=request_id,
                session_id=session.session_id,
                context_reference=session.context_reference,
                user_id=user_id,
                agent_id=active_agent_id,
                source=session.source,
                intent=intent.value,
                skill_id=str((skill or {}).get("skill_id") or "").strip() or None,
                route=route,
                request_text=request_text,
                classification=classification,
                result_with_internal=internal_result_payload,
                dialog=response["dialog"],
                assistant_text=str(response["assistant"].get("text") or ""),
            )
            response["request_id"] = capture.request_id
            if capture.ticket is not None:
                response["ticket"] = {
                    "ticket_id": capture.ticket.get("ticket_id"),
                    "status": capture.ticket.get("status"),
                    "review_due_at": capture.ticket.get("review_due_at"),
                    "root_ticket_id": capture.ticket.get("root_ticket_id"),
                    "parent_ticket_id": capture.ticket.get("parent_ticket_id"),
                }
            if capture.context_reference != session.context_reference:
                session.context_reference = capture.context_reference
                session.touch()
                self._session_store.save(session)
        result_status = str(result_payload.get("status") or "").strip().lower() or None
        sensitive_email_turn = intent in EMAIL_AGENT_INTENTS
        if not sensitive_email_turn:
            self._record_recent_turn_exchange(
                session=session,
                request_text=request_text,
                assistant_text=str(response["assistant"].get("text") or ""),
                intent=intent,
                route=route,
                skill=skill,
                result_status=result_status,
            )
            self._refresh_session_summary(
                session=session,
                intent=intent,
                route=route,
                result_status=result_status,
            )
            self._record_conversation_topic_turn(
                session=session,
                skill=skill,
                intent=intent,
                route=route,
                result=result_payload,
                classification=classification,
                request_text=request_text,
                user_id=user_id,
                assistant_text=str(response["assistant"].get("text") or "").strip() or None,
            )
        self._event_log.record(
            event_type="response.generated",
            session_id=session.session_id,
            payload={
                "route": route,
                "owner": session.owner.value,
                "state": session.state.value,
                "intent": intent.value,
                "source": session.source,
                "channel_key": channel_runtime.get("channel_key")
                if isinstance(channel_runtime, dict)
                else None,
            },
        )
        if self._memory_service is not None and not sensitive_email_turn:
            response["delivery"] = {
                "memory": self._record_memory_interaction(
                    session_id=session.session_id,
                    user_id=user_id,
                    source=session.source,
                    intent=intent.value,
                    route=route,
                    request_text=request_text,
                    response_summary=self._summarize_result(result_payload),
                    metadata={
                        "owner": session.owner.value,
                        "state": session.state.value,
                        "power_state": self._runtime_power.state.value,
                        "larger_models_active": bool(
                            model_runtime_status.get("larger_models_active")
                        ),
                        "agent_id": active_agent_id,
                    },
                )
            }
        return response

    def _build_working_context_packet(
        self,
        *,
        session: SessionRecord,
        user_id: str,
        request_text: str,
        route_hint: str,
        intent_hint: str | None,
    ):
        context_reference = session.context_reference
        session_summary = context_reference.get("session_summary")
        pending_interaction = context_reference.get("pending_interaction")
        channel_runtime = context_reference.get("channel_session")
        supplemental_sections: list[str] = []
        token_session = context_reference.get("main_agent_token_session")
        if isinstance(token_session, dict):
            summaries = token_session.get("turn_summaries")
            if isinstance(summaries, list):
                supplemental_sections.extend(str(item) for item in summaries[:3] if str(item).strip())
        state_snapshot = session.context_state()
        raw_recent_turns_count = len(state_snapshot.recent_turns)
        raw_entity_count = len(state_snapshot.entity_registry.entities)

        budget_snapshot = self._main_agent_context_budget.snapshot(
            goal_text=request_text,
            context={
                "route_hint": route_hint,
                "intent_hint": intent_hint,
                "session_summary": session_summary if isinstance(session_summary, dict) else {},
                "pending_interaction": pending_interaction if isinstance(pending_interaction, dict) else {},
            },
            supplemental_sections=supplemental_sections,
        )
        raw_memory_rows = self._relevant_memory_context(
            user_id=user_id,
            session_id=session.session_id,
        )
        active_skill_context = {
            "route_hint": route_hint,
            "intent_hint": intent_hint,
        }
        active_skill_context.update(
            self._skill_memory_handoff_context(
                relevant_memory=raw_memory_rows,
                intent=intent_hint,
                request_text=request_text,
            )
        )
        runtime_channel_context = dict(channel_runtime) if isinstance(channel_runtime, dict) else {}
        available_switches = self._home_service.list_switches()
        if isinstance(available_switches, list) and available_switches:
            runtime_channel_context["available_switches"] = available_switches
        packet = self._context_builder.build_packet(
            session=session,
            relevant_memory=raw_memory_rows,
            active_skill_context=active_skill_context,
            channel_runtime=runtime_channel_context or None,
            budget_metadata=budget_snapshot.to_dict(),
        )
        self._event_log.record(
            event_type="context.packet.built",
            session_id=session.session_id,
            payload={
                "route_hint": route_hint,
                "intent_hint": intent_hint,
                "recent_turns_count": len(packet.recent_turns),
                "entity_hints_count": len(packet.entity_hints),
                "memory_count": len(packet.relevant_memory),
                "has_pending_interaction": packet.pending_interaction is not None,
                "summary_chars": len(packet.session_summary.summary_text or ""),
                "raw_recent_turns_count": raw_recent_turns_count,
                "raw_entity_registry_count": raw_entity_count,
                "raw_memory_count": len(raw_memory_rows),
                "dropped_recent_turns_count": max(0, raw_recent_turns_count - len(packet.recent_turns)),
                "dropped_entity_hints_count": max(0, raw_entity_count - len(packet.entity_hints)),
                "dropped_memory_count": max(0, len(raw_memory_rows) - len(packet.relevant_memory)),
                "budget_trimmed": bool(packet.budget_metadata.get("trimmed")),
                "budget_used_chars": int(packet.budget_metadata.get("used_chars") or 0),
                "budget_max_chars": int(packet.budget_metadata.get("max_chars") or 0),
            },
        )
        return packet

    def _skill_memory_handoff_context(
        self,
        *,
        relevant_memory: list[dict[str, Any]],
        intent: str | None,
        request_text: str,
    ) -> dict[str, Any]:
        hints: dict[str, Any] = {}
        for contract in self._skill_context_contracts:
            hook = getattr(contract, "memory_handoff_hints", None)
            if not callable(hook):
                continue
            try:
                contract_hints = hook(
                    relevant_memory=relevant_memory,
                    intent=intent,
                    request_text=request_text,
                )
            except Exception:
                continue
            if not isinstance(contract_hints, dict):
                continue
            for key, value in contract_hints.items():
                field_name = str(key or "").strip()
                if field_name and value is not None:
                    hints[field_name] = value
        return hints

    def _resolve_handoff_followup_entities(
        self,
        *,
        session: SessionRecord,
        decision: MicroDecision,
        working_context: dict[str, Any],
    ) -> MicroDecision:
        active_skill_context = working_context.get("active_skill_context")
        if not isinstance(active_skill_context, dict):
            return decision
        for contract in self._skill_context_contracts:
            if not contract.supports_intent(intent=decision.intent.value):
                continue
            hook = getattr(contract, "resolve_handoff_followup", None)
            if not callable(hook):
                continue
            try:
                decision = hook(
                    decision=decision,
                    active_skill_context=dict(active_skill_context),
                    resolver=self._reference_resolver,
                )
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                self._event_log.record(
                    event_type="context.contract.resolve_handoff_followup.failed",
                    session_id=session.session_id,
                    payload={
                        "contract_id": str(getattr(contract, "contract_id", "") or ""),
                        "intent": decision.intent.value,
                        "error": str(exc),
                    },
                )
        return decision

    def export_session_context_snapshot(
        self,
        *,
        session_id: str,
        include_legacy: bool = True,
        include_working_context: bool = True,
        include_recent_events: bool = True,
        recent_events_limit: int = 120,
    ) -> dict[str, Any] | None:
        session = self._session_store.get(session_id)
        if session is None:
            return None

        state = session.context_state()
        snapshot: dict[str, Any] = {
            "session": {
                "session_id": session.session_id,
                "user_id": session.user_id,
                "source": session.source,
                "owner": session.owner.value,
                "state": session.state.value,
                "last_activity_timestamp": session.last_activity_timestamp,
            },
            "context_state": state.to_dict(),
            "context_summary": {
                "recent_turns_count": len(state.recent_turns),
                "pending_interaction_active": state.pending_interaction is not None,
                "entity_registry_count": len(state.entity_registry.entities),
                "session_summary_chars": len(str(state.session_summary.summary_text or "")),
                "context_annotations_keys": sorted(str(key) for key in state.context_annotations.keys()),
            },
        }
        if include_legacy:
            snapshot["legacy_context_view"] = session.legacy_context_view()
        if include_working_context:
            packet = self._context_builder.build_packet(
                session=session,
                relevant_memory=self._relevant_memory_context(
                    user_id=session.user_id,
                    session_id=session.session_id,
                ),
                active_skill_context={
                    "route_hint": "debug_snapshot_export",
                    "intent_hint": None,
                },
                channel_runtime=state.channel_runtime,
                budget_metadata={"source": "debug_snapshot_export"},
            )
            packet_dict = packet.to_dict()
            snapshot["working_context_preview"] = {
                "counts": {
                    "recent_turns": len(packet.recent_turns),
                    "entity_hints": len(packet.entity_hints),
                    "relevant_memory": len(packet.relevant_memory),
                },
                "pending_interaction": packet_dict.get("pending_interaction"),
                "recent_turns": packet_dict.get("recent_turns"),
                "session_summary": packet_dict.get("session_summary"),
                "entity_hints": packet_dict.get("entity_hints"),
                "relevant_memory": packet_dict.get("relevant_memory"),
                "active_skill_context": packet_dict.get("active_skill_context"),
                "channel_runtime": packet_dict.get("channel_runtime"),
                "budget_metadata": packet_dict.get("budget_metadata"),
            }
        if include_recent_events:
            bounded_limit = max(20, min(int(recent_events_limit), 500))
            snapshot["context_trace_events"] = self._recent_context_trace_events(
                session_id=session.session_id,
                limit=bounded_limit,
            )

        self._event_log.record(
            event_type="context.snapshot.exported",
            session_id=session.session_id,
            payload={
                "include_legacy": bool(include_legacy),
                "include_working_context": bool(include_working_context),
                "include_recent_events": bool(include_recent_events),
                "recent_events_limit": int(recent_events_limit),
            },
        )
        return snapshot

    def _recent_context_trace_events(self, *, session_id: str, limit: int) -> list[dict[str, Any]]:
        rows = self._event_log.recent(limit=max(limit * 3, 60))
        filtered: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("session_id") or "") != session_id:
                continue
            event_type = str(row.get("event_type") or "").strip().lower()
            if not self._is_context_trace_event_type(event_type):
                continue
            filtered.append(dict(row))
        if len(filtered) > limit:
            return filtered[-limit:]
        return filtered

    @staticmethod
    def _is_context_trace_event_type(event_type: str) -> bool:
        normalized = str(event_type or "").strip().lower()
        if normalized.startswith("context."):
            return True
        if normalized.startswith("pending."):
            return True
        return normalized in {
            "main.repair.clarification.attempted",
            "main.repair.clarification.executed",
        }

    def _relevant_memory_context(
        self,
        *,
        user_id: str,
        session_id: str,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        if self._memory_service is None:
            return []
        try:
            rows = self._memory_service.recent(limit=max(12, int(limit) * 4))
        except Exception:  # pragma: no cover - defensive fallback
            return []
        matched: list[dict[str, Any]] = []
        target_user_id = str(user_id or "").strip().lower()
        target_session_id = str(session_id or "").strip()
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            row_user_id = str(row.get("user_id") or "").strip().lower()
            row_session_id = str(row.get("session_id") or "").strip()
            if row_session_id != target_session_id and row_user_id != target_user_id:
                continue
            matched.append(row)
            if len(matched) >= max(1, int(limit)):
                break
        matched.reverse()
        return matched

    def _record_recent_turn_exchange(
        self,
        *,
        session: SessionRecord,
        request_text: str,
        assistant_text: str,
        intent: Intent,
        route: str,
        skill: dict[str, Any] | None,
        result_status: str | None,
    ) -> None:
        skill_id = str((skill or {}).get("skill_id") or "").strip() or None
        update = self._session_context_manager.record_exchange(
            session=session,
            user_text=request_text,
            assistant_text=assistant_text,
            intent=intent.value,
            route=route,
            skill_id=skill_id,
            result_status=result_status,
        )
        if not update.updated:
            return
        session.touch()
        self._session_store.save(session)
        self._event_log.record(
            event_type="context.recent_turns.updated",
            session_id=session.session_id,
            payload={
                "appended_count": update.appended_count,
                "pruned_count": update.pruned_count,
                "total_turns": update.total_turns,
                "total_chars": update.total_chars,
                "intent": intent.value,
                "route": route,
            },
        )

    def _refresh_session_summary(
        self,
        *,
        session: SessionRecord,
        intent: Intent,
        route: str,
        result_status: str | None,
    ) -> None:
        update = self._session_summary_manager.maybe_refresh(
            session=session,
            intent=intent.value,
            route=route,
            result_status=result_status,
        )
        if not update.updated:
            return
        session.touch()
        self._session_store.save(session)
        self._event_log.record(
            event_type="context.session_summary.updated",
            session_id=session.session_id,
            payload={
                "trigger": update.trigger,
                "turn_counter": update.turn_counter,
                "recent_turns_count": update.recent_turns_count,
                "recent_chars": update.recent_chars,
                "summary_chars": update.summary_chars,
                "intent": intent.value,
                "route": route,
                "status": result_status,
            },
        )

    def _record_memory_interaction(
        self,
        *,
        session_id: str,
        user_id: str,
        source: str,
        intent: str,
        route: str,
        request_text: str,
        response_summary: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        if self._memory_service is None:
            return {"status": "not_configured"}

        safe_metadata = dict(metadata)
        try:
            if self._durable_write_service is not None:
                return self._durable_write_service.enqueue_memory_interaction(
                    request_id=str(self._request_id_var.get() or uuid4()),
                    session_id=session_id,
                    user_id=user_id,
                    source=source,
                    intent=intent,
                    route=route,
                    request_text=request_text,
                    response_summary=response_summary,
                    metadata=safe_metadata,
                )
            else:
                self._memory_service.record_interaction(
                    session_id=session_id,
                    user_id=user_id,
                    source=source,
                    intent=intent,
                    route=route,
                    request_text=request_text,
                    response_summary=response_summary,
                    metadata=safe_metadata,
                )
                return {"status": "committed"}
        except Exception as exc:  # pragma: no cover - defensive logging path
            self._event_log.record(
                event_type="memory.record.failed",
                session_id=session_id,
                payload={
                    "error": type(exc).__name__,
                    "source": source,
                    "intent": intent,
                    "route": route,
                },
            )
            return {"status": "failed", "error": "memory_persistence_failed"}

    def _record_runtime_model_activity(
        self,
        *,
        session_id: str,
        intent: Intent,
        classification: dict[str, Any],
        route: str,
    ) -> None:
        label_owner = self._task_label_owner(intent=intent, classification=classification, route=route)
        if label_owner is None:
            return
        changed = self._runtime_power.record_task_label(label_owner)
        if not changed:
            return
        status = self._runtime_power.model_runtime_status()
        self._event_log.record(
            event_type="runtime.models.changed",
            session_id=session_id,
            payload={
                "larger_models_active": status.get("larger_models_active"),
                "task_count": status.get("task_count"),
                "micro_labeled_count": status.get("micro_labeled_count"),
                "main_labeled_count": status.get("main_labeled_count"),
                "window_seconds": status.get("window_seconds"),
            },
        )

    @staticmethod
    def _task_label_owner(
        *,
        intent: Intent,
        classification: dict[str, Any],
        route: str,
    ) -> SessionOwner | None:
        if intent in {Intent.SYSTEM_WAKE, Intent.SYSTEM_SLEEP}:
            return None
        if route in {"runtime_power", "sleep_guard"}:
            return None

        # Route ownership takes precedence over classifier recommendation:
        # if Main is the active pipeline for this turn, keep larger models warm.
        if route in {"main_jarvis", "main_jarvis_repair", "main_skill", "main_jarvis_commitment"}:
            return SessionOwner.MAIN
        if route == "micro_tool":
            return SessionOwner.MICRO

        recommended_owner = JarvisRouter._coerce_owner(str(classification.get("recommended_owner") or ""))
        if recommended_owner in {SessionOwner.MICRO, SessionOwner.MAIN}:
            return recommended_owner

        if intent in FAST_COMMAND_INTENTS:
            return SessionOwner.MICRO
        return None

    @staticmethod
    def _coerce_owner(raw: str) -> SessionOwner | None:
        normalized = raw.strip().lower()
        for owner in SessionOwner:
            if owner.value == normalized:
                return owner
        return None

    def _main_intent_label(
        self,
        *,
        route: str,
        intent: Intent,
        classification: dict[str, Any],
        result: dict[str, Any],
    ) -> str | None:
        if route not in {"main_jarvis", "main_jarvis_repair", "main_skill", "main_jarvis_commitment"}:
            return None

        domain_label = self._main_domain_label(intent=intent, classification=classification, result=result)
        if self._is_followup_turn(classification=classification, result=result):
            if domain_label == "gen question":
                return "follow up from previous"
            return f"follow up from previous | {domain_label}"
        return domain_label

    def _main_domain_label(
        self,
        *,
        intent: Intent,
        classification: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        effective_intent = intent
        inferred_intent = (
            str(classification.get("repair_inferred_intent") or "").strip()
            or str(result.get("inferred_intent") or "").strip()
        )
        if effective_intent in {Intent.UNKNOWN, Intent.CONVERSATIONAL}:
            plan = result.get("plan")
            plan_type = str(plan.get("plan_type") or "").strip().casefold() if isinstance(plan, dict) else ""
            if plan_type.startswith("list."):
                return "list action"
            if plan_type.startswith("calendar."):
                return "calendar action"
            if plan_type.startswith("home."):
                return "home action"
            candidate = (
                str(classification.get("repair_candidate_intent") or "").strip()
                or str(result.get("cancelled_intent") or "").strip()
            )
            coerced = self._coerce_intent(candidate)
            if coerced is not None:
                effective_intent = coerced
            elif inferred_intent.startswith("home."):
                if "thermostat" in inferred_intent:
                    return "thermostat action"
                return "home action"

        if effective_intent == Intent.HOME_SET_SWITCH:
            return "lights action"
        if effective_intent in {
            Intent.CALENDAR_ADD_EVENT,
            Intent.CALENDAR_VIEW,
            Intent.CALENDAR_UPDATE_EVENT,
            Intent.CALENDAR_DELETE_EVENT,
        }:
            return "calendar action"
        if effective_intent in {
            Intent.LIST_CREATE_LIST,
            Intent.LIST_ADD_ITEM,
            Intent.LIST_GET_ITEMS,
            Intent.LIST_DELETE_LIST,
            Intent.LIST_REMOVE_ITEM,
            Intent.LIST_MARK_ITEM_DONE,
        }:
            return "list action"
        if effective_intent in EMAIL_AGENT_INTENTS:
            return "email action"
        return "gen question"

    @staticmethod
    def _is_followup_turn(*, classification: dict[str, Any], result: dict[str, Any]) -> bool:
        ambiguity_flags_raw = classification.get("ambiguity_flags")
        ambiguity_flags = {
            str(item).strip().lower()
            for item in ambiguity_flags_raw
            if isinstance(ambiguity_flags_raw, list) and str(item).strip()
        }
        if {"clarification_pending", "clarification_completed", "cancelled_pending_clarification"} & ambiguity_flags:
            return True

        reasoning = str(classification.get("reasoning") or "").strip().lower()
        if reasoning.startswith("pending_clarification"):
            return True

        if classification.get("cancelled_intent") is not None:
            return True

        repair_source = str(result.get("repair_source") or "").strip().lower()
        if repair_source == "clarification_followup":
            return True

        status = str(result.get("status") or "").strip().lower()
        if status == "cancelled":
            return True

        return False

    @staticmethod
    def _summarize_result(result: dict[str, Any]) -> str:
        message = result.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        status = result.get("status")
        if isinstance(status, str) and status.strip():
            return status.strip()
        return "response_generated"

    @staticmethod
    def _is_conversation_skill_turn(
        *,
        skill: dict[str, Any] | None,
        intent: Intent,
        route: str,
        result: dict[str, Any],
    ) -> bool:
        skill_id = str((skill or {}).get("skill_id") or "").strip().lower()
        if skill_id == "skill.conversation.general":
            return True
        if route not in {"main_jarvis", "main_jarvis_repair"}:
            return False
        if intent in {Intent.CONVERSATIONAL, Intent.UNKNOWN}:
            return True
        result_status = str(result.get("status") or "").strip().lower()
        return result_status == "conversation"

    def _record_conversation_topic_turn(
        self,
        *,
        session: SessionRecord,
        skill: dict[str, Any] | None,
        intent: Intent,
        route: str,
        result: dict[str, Any],
        classification: dict[str, Any],
        request_text: str,
        user_id: str,
        assistant_text: str | None,
    ) -> None:
        if self._conversation_history_service is None:
            return
        if not self._is_conversation_skill_turn(
            skill=skill,
            intent=intent,
            route=route,
            result=result,
        ):
            return
        entry = self._conversation_history_service.record_turn(
            session_id=session.session_id,
            user_id=user_id,
            agent_id=self._active_agent_id(session),
            route=route,
            intent=intent.value,
            status=str(result.get("status") or "").strip().lower() or None,
            user_text=request_text,
            assistant_text=assistant_text,
            metadata={
                "classification_confidence": classification.get("confidence"),
                "classification_reasoning": classification.get("reasoning"),
                "repair_source": result.get("repair_source"),
            },
        )
        if not isinstance(entry, dict):
            return
        topic_key = str(entry.get("topic_key") or "").strip()
        topic_label = str(entry.get("topic_label") or "").strip()
        if not topic_label:
            topic_label = topic_key.replace("_", " ").strip()
        if not topic_label:
            return
        if topic_key in {"general_conversation", "identity", "capabilities"}:
            return
        topic_terms = entry.get("topic_terms")
        aliases: list[str] = []
        if isinstance(topic_terms, list):
            aliases.extend(str(item).strip() for item in topic_terms if str(item).strip())
        if topic_key:
            aliases.append(topic_key.replace("_", " ").strip())
        update = self._entity_registry_manager.record_entities(
            session=session,
            entities=[
                {
                    "domain": "conversation",
                    "entity_type": "topic",
                    "display_name": topic_label,
                    "aliases": aliases,
                    "salience": 0.76,
                    "resolution_hints": {
                        "topic_key": topic_key or None,
                        "source": "conversation_history_service",
                    },
                }
            ],
        )
        if not bool(update.get("updated")):
            return
        session.touch()
        self._session_store.save(session)
        self._event_log.record(
            event_type="context.entity_registry.updated",
            session_id=session.session_id,
            payload={
                "intent": intent.value,
                "status": str(result.get("status") or "").strip().lower(),
                "upserted_count": int(update.get("upserted_count") or 0),
                "total_entities": int(update.get("total_entities") or 0),
                "emitted_entities": [
                    {
                        "domain": "conversation",
                        "entity_type": "topic",
                        "display_name": topic_label,
                    }
                ],
            },
        )

    def _build_dialog_metadata(
        self,
        session: SessionRecord,
        route: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        result_status = str(result.get("status") or "").strip().lower()
        question = str(result.get("question") or "").strip() or None
        missing_fields = result.get("missing_fields")
        if not isinstance(missing_fields, list):
            missing_fields = []

        pending = self._pending_clarification(session)
        pending_intent = None
        if pending is not None:
            pending_intent = str(pending.get("intent") or "").strip() or None
            if not missing_fields:
                pending_missing = pending.get("missing_fields")
                if isinstance(pending_missing, list):
                    missing_fields = [str(item) for item in pending_missing if str(item).strip()]

        is_pending = (
            session.state == SessionState.AWAITING_CONFIRMATION
            or result_status in {"needs_clarification", "needs_input"}
            or question is not None
        )
        if is_pending:
            mode = "conversation_pending"
        elif route == "main_jarvis" or result_status in {"conversation", "planned"}:
            mode = "conversation"
        else:
            mode = "command_action"

        return {
            "mode": mode,
            "turn_complete": not is_pending,
            "pending_intent": pending_intent,
            "awaiting_fields": missing_fields,
            "question": question,
            "status": result_status or None,
        }
