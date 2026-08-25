from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any, TYPE_CHECKING

from app.context.context_builder import ContextBuilder
from app.context.entity_registry import EntityRegistryManager
from app.context.pending import PendingInteractionManager
from app.context.reference_resolver import ReferenceResolver
from app.context.session_context_manager import SessionContextManager
from app.context.summarizer import SessionSummaryManager
from app.core.agent_loop_types import AgentLoopLimits
from app.core.action_execution import ActionExecutionService
from app.core.agent_routing import AgentRoutingPolicy
from app.core.content_policy import MainAgentContentPolicyGate
from app.core.clarification_coordinator import ClarificationCoordinator
from app.core.conversation_flow import ConversationFlow
from app.core.conversation_routing import ConversationLanePolicy
from app.core.context_budget import ContextBudget
from app.core.context_flow import ContextFlow
from app.core.domain_context import DomainContextService
from app.core.evaluator import MainAgentEvaluator
from app.core.main_jarvis import MainJarvis
from app.core.main_plan_flow import MainPlanFlow
from app.core.main_repair_flow import MainRepairFlow
from app.core.main_turn_commitment import MainTurnCommitmentCoordinator
from app.core.micro_jarvis import MicroDecision, MicroJarvis
from app.core.pending_interaction import PendingInteractionCoordinator
from app.core.planner import MainAgentPlanner
from app.core.request_pipeline import JarvisRequestPipeline, PipelineDecision
from app.core.request_flow import RequestFlowCoordinator
from app.core.session_store import SessionRecord, SessionStore
from app.core.session_transitions import SessionTransitionService
from app.core.state_machine import RuntimePowerController
from app.core.turn_finalizer import TurnFinalizer
from app.core.types import (
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
        documents_service: Any | None = None,
        skill_service_bindings: dict[str, Any] | None = None,
        skill_context_contracts: list[Any] | None = None,
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
            service_bindings={
                **(skill_service_bindings or {}),
                **({"documents_service": documents_service} if documents_service is not None else {}),
            },
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
        self._session_transitions = SessionTransitionService(
            session_store=session_store,
            event_log=event_log,
            sticky_followup_turns=self._main_sticky_followup_turns,
        )
        self._main_pending_clarification_heuristic_fallback_enabled = bool(
            main_pending_clarification_heuristic_fallback_enabled
        )
        self._pending_interaction_ttl_seconds = max(1.0, float(pending_interaction_ttl_seconds))
        self._pending_interaction_manager = PendingInteractionManager(
            default_ttl_seconds=self._pending_interaction_ttl_seconds
        )
        self._pending_interaction_coordinator = PendingInteractionCoordinator(
            manager=self._pending_interaction_manager,
            session_store=session_store,
            event_log=event_log,
        )
        self._entity_registry_manager = EntityRegistryManager()
        self._reference_resolver = ReferenceResolver()
        self._skill_context_contracts = list(skill_context_contracts) if skill_context_contracts else (
            default_skill_context_contracts(
                email_agent_service=email_agent_service,
                documents_enabled=documents_service is not None,
            )
        )
        self._domain_context = DomainContextService(
            contracts=self._skill_context_contracts,
            reference_resolver=self._reference_resolver,
            event_log=event_log,
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
        self._turn_finalizer = TurnFinalizer(
            session_store=session_store,
            runtime_power=runtime_power,
            event_log=event_log,
            memory_service=memory_service,
            durable_write_service=durable_write_service,
            conversation_history_service=conversation_history_service,
            action_ticket_service=action_ticket_service,
            skill_registry=skill_registry,
            authorized_skill_executor=self._authorized_skill_executor,
            skill_context_contracts=self._skill_context_contracts,
            entity_registry_manager=self._entity_registry_manager,
            pending_interaction_coordinator=self._pending_interaction_coordinator,
            session_context_manager=self._session_context_manager,
            session_summary_manager=self._session_summary_manager,
        )
        self._action_execution_service = ActionExecutionService(
            authorized_executor=self._authorized_skill_executor,
            turn_finalizer=self._turn_finalizer,
            action_ticket_service=action_ticket_service,
        )
        self._main_turn_commitment = MainTurnCommitmentCoordinator(
            domain_context=self._domain_context,
            pending_interactions=self._pending_interaction_coordinator,
            session_transitions=self._session_transitions,
            action_execution=self._action_execution_service,
            turn_finalizer=self._turn_finalizer,
            session_store=session_store,
            event_log=event_log,
            action_ticket_service=action_ticket_service,
            low_confidence_floor=self._main_low_confidence_floor,
            child_action_denial_message=self._CHILD_ACTION_DENIAL_MESSAGE,
        )
        self._clarification_coordinator = ClarificationCoordinator(
            domain_context=self._domain_context,
            pending_interactions=self._pending_interaction_coordinator,
            session_transitions=self._session_transitions,
            action_execution=self._action_execution_service,
            turn_finalizer=self._turn_finalizer,
            session_store=session_store,
            event_log=event_log,
            action_ticket_service=action_ticket_service,
        )
        self._request_flow = RequestFlowCoordinator(self)
        self._main_repair_flow = MainRepairFlow(self)
        self._conversation_flow = ConversationFlow(self)
        self._context_flow = ContextFlow(self)
        self._main_plan_flow = MainPlanFlow(self)

    @property
    def authorized_skill_executor(self) -> AuthorizedSkillExecutor:
        return self._authorized_skill_executor

    @property
    def turn_finalizer(self) -> TurnFinalizer:
        return self._turn_finalizer

    @property
    def action_execution_service(self) -> ActionExecutionService:
        return self._action_execution_service

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
        return self._request_flow.route(payload)

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
        self._domain_context.set_contracts(self._skill_context_contracts)
        self._turn_finalizer.set_skill_context_contracts(self._skill_context_contracts)
        return self._main_turn_commitment.handle(
            response=response,
            payload=payload,
            session=session,
            effective_context=effective_context,
            runtime_capability_catalog=runtime_capability_catalog,
            request_text=request_text,
            request_id=self._request_id_var.get(),
            open_tool_followup=self._maybe_open_tool_followup,
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
        return self._conversation_flow._maybe_open_conversation_followup(
            session=session,
            decision=decision,
            classification=classification,
            response=response,
            request_text=request_text,
            working_context_payload=working_context_payload,
        )

    def _store_pending_conversation(
        self,
        *,
        session: SessionRecord,
        entities: dict[str, Any],
        missing_fields: list[str],
        question: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._pending_interaction_coordinator.store(
            session=session,
            intent=Intent.CONVERSATIONAL.value,
            entities=dict(entities),
            missing_fields=[str(item).strip() for item in missing_fields if str(item).strip()],
            question=question,
            kind="conversation_clarification",
            metadata=dict(metadata or {}),
            reason="router._store_pending_conversation",
        )

    @staticmethod
    def _infer_conversation_pending_fields(*, question: str) -> list[str]:
        return ConversationFlow._infer_conversation_pending_fields(
            question=question,
        )

    def _infer_contextual_followup(self, *, text: str, working_context: dict[str, Any]) -> dict[str, Any] | None:
        return self._conversation_flow._infer_contextual_followup(
            text=text,
            working_context=working_context,
        )

    def _extract_contextual_topic_hint(self, working_context: dict[str, Any]) -> str | None:
        return self._conversation_flow._extract_contextual_topic_hint(
            working_context=working_context,
        )

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
        return self._conversation_flow._looks_like_contextual_followup_text(
            text=text,
            working_context=working_context,
        )

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
        return self._action_execution_service.resolve(
            intent=intent.value,
            user_id=user_id,
            agent_id=agent_id,
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
        return MainRepairFlow._should_attempt_main_repair(decision=decision)

    def _attempt_main_repair(
        self,
        payload: AskRequest,
        session: SessionRecord,
        micro_decision: MicroDecision,
        required_missing_fields: list[str] | None = None,
        working_context_payload: dict[str, Any] | None = None,
        contextual_followup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._main_repair_flow._attempt_main_repair(
            payload=payload,
            session=session,
            micro_decision=micro_decision,
            required_missing_fields=required_missing_fields,
            working_context_payload=working_context_payload,
            contextual_followup=contextual_followup,
        )

    def _fallback_repair_to_missing_fields_clarification(
        self,
        *,
        payload: AskRequest,
        session: SessionRecord,
        micro_decision: MicroDecision,
        preferred_missing_fields: list[str] | None,
        fallback_reason: str,
    ) -> dict[str, Any] | None:
        return self._main_repair_flow._fallback_repair_to_missing_fields_clarification(
            payload=payload,
            session=session,
            micro_decision=micro_decision,
            preferred_missing_fields=preferred_missing_fields,
            fallback_reason=fallback_reason,
        )

    @staticmethod
    def _should_surface_not_actionable(
        *,
        repair: dict[str, Any],
        micro_decision: MicroDecision,
    ) -> bool:
        return MainRepairFlow._should_surface_not_actionable(repair=repair, micro_decision=micro_decision)

    def _maybe_open_tool_followup(
        self,
        session: SessionRecord,
        decision: MicroDecision,
        tool_result: dict[str, Any],
        request_text: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        return self._conversation_flow._maybe_open_tool_followup(
            session=session,
            decision=decision,
            tool_result=tool_result,
            request_text=request_text,
            user_id=user_id,
        )

    def _resolve_followup_entities(self, session: SessionRecord, decision: MicroDecision) -> MicroDecision:
        return self._context_flow._resolve_followup_entities(
            session=session,
            decision=decision,
        )

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

    def _normalize_entities_for_intent(
        self,
        intent: Intent,
        entities: dict[str, Any],
    ) -> dict[str, Any]:
        self._domain_context.set_contracts(self._skill_context_contracts)
        return self._domain_context.normalize_entities(intent=intent, entities=entities)

    def _apply_text_constraints(
        self,
        *,
        intent: Intent,
        text: str,
        entities: dict[str, Any],
    ) -> dict[str, Any]:
        self._domain_context.set_contracts(self._skill_context_contracts)
        return self._domain_context.apply_text_constraints(intent=intent, text=text, entities=entities)

    def _clarification_supplemental_fields(self, *, intent: Intent) -> list[str]:
        self._domain_context.set_contracts(self._skill_context_contracts)
        return self._domain_context.clarification_supplemental_fields(intent=intent)

    def _repair_decision_from_main(
        self,
        repair: dict[str, Any],
        micro_decision: MicroDecision,
    ) -> MicroDecision | None:
        return self._main_repair_flow._repair_decision_from_main(repair=repair, micro_decision=micro_decision)

    def _maybe_require_confidence_clarification(
        self,
        *,
        payload: AskRequest,
        session: SessionRecord,
        micro_decision: MicroDecision,
        repaired_decision: MicroDecision,
        repair: dict[str, Any],
    ) -> dict[str, Any] | None:
        return self._main_repair_flow._maybe_require_confidence_clarification(
            payload=payload,
            session=session,
            micro_decision=micro_decision,
            repaired_decision=repaired_decision,
            repair=repair,
        )

    def _confidence_gate_reason(
        self,
        *,
        micro_decision: MicroDecision,
        repaired_decision: MicroDecision,
    ) -> str | None:
        return self._main_repair_flow._confidence_gate_reason(
            micro_decision=micro_decision,
            repaired_decision=repaired_decision,
        )

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
        return MainRepairFlow._default_clarification_field_for_intent(intent=intent)

    @staticmethod
    def _coerce_intent(raw_intent: str) -> Intent | None:
        cleaned = raw_intent.strip().lower()
        for intent in Intent:
            if intent.value == cleaned:
                return intent
        return None

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
        self._domain_context.set_contracts(self._skill_context_contracts)
        self._turn_finalizer.set_skill_context_contracts(self._skill_context_contracts)
        return self._clarification_coordinator.handle(
            payload=payload,
            session=session,
            request_id=self._request_id_var.get(),
            should_interrupt=self._should_interrupt_pending_clarification,
            extract_model_updates=self._extract_clarification_updates_with_main_repair,
            complete_conversation=self._complete_pending_conversation_followup,
            open_tool_followup=self._maybe_open_tool_followup,
        )

    def _complete_pending_conversation_followup(
        self,
        *,
        session: SessionRecord,
        payload: AskRequest,
        intent: Intent,
        merged_entities: dict[str, Any],
        pending: dict[str, Any],
    ) -> dict[str, Any]:
        return self._conversation_flow._complete_pending_conversation_followup(
            session=session,
            payload=payload,
            intent=intent,
            merged_entities=merged_entities,
            pending=pending,
        )

    @staticmethod
    def _conversation_followup_prompt(
        *,
        user_text: str,
        pending_question: str,
        topic_subject: str,
        confirmation: str,
    ) -> str:
        return ConversationFlow._conversation_followup_prompt(
            user_text=user_text,
            pending_question=pending_question,
            topic_subject=topic_subject,
            confirmation=confirmation,
        )

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
        self._domain_context.set_contracts(self._skill_context_contracts)
        return self._domain_context.required_fields(intent=intent, entities=entities)

    @staticmethod
    def _merge_missing_fields(base: list[str], candidate: list[str]) -> list[str]:
        return DomainContextService.merge_missing_fields(base, candidate)

    @staticmethod
    def _normalize_missing_field_list(value: list[str]) -> list[str]:
        return DomainContextService.normalize_missing_fields(value)

    def _extract_safe_contextual_clarification_updates(
        self,
        *,
        session: SessionRecord,
        intent: Intent,
        text: str,
        missing_fields: list[str],
        current_entities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._domain_context.set_contracts(self._skill_context_contracts)
        return self._domain_context.extract_pending_updates(
            session=session,
            intent=intent,
            text=text,
            missing_fields=missing_fields,
            current_entities=current_entities,
        )

    def _extract_clarification_updates_with_main_repair(
        self,
        *,
        session: SessionRecord,
        payload: AskRequest,
        intent: Intent,
        missing_fields: list[str],
        current_entities: dict[str, Any],
    ) -> dict[str, Any]:
        return self._main_repair_flow._extract_clarification_updates_with_main_repair(
            session=session,
            payload=payload,
            intent=intent,
            missing_fields=missing_fields,
            current_entities=current_entities,
        )

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
        self._domain_context.set_contracts(self._skill_context_contracts)
        return self._domain_context.clarification_question(intent=intent, field_name=field_name)

    def _legacy_main_handoff_context(
        self,
        *,
        session: SessionRecord,
        intent: str | None = None,
        route: str | None = None,
    ) -> dict[str, Any]:
        return self._context_flow._legacy_main_handoff_context(
            session=session,
            intent=intent,
            route=route,
        )

    def _runtime_main_handoff_context(self) -> dict[str, Any]:
        return self._context_flow._runtime_main_handoff_context()

    @staticmethod
    def _active_agent_id(session: SessionRecord) -> str:
        return SessionTransitionService.active_agent_id(session)

    @staticmethod
    def _main_sticky_followup_turns_remaining(session: SessionRecord) -> int:
        return SessionTransitionService.main_followup_turns_remaining(session=session)

    def _arm_main_sticky_followup(
        self,
        *,
        session: SessionRecord,
        reason: str,
        turns: int | None = None,
    ) -> None:
        self._session_transitions.arm_main_followup(session=session, reason=reason, turns=turns)

    def _consume_main_sticky_followup_turn(self, session: SessionRecord) -> int:
        return self._session_transitions.consume_main_followup(session=session)

    def _clear_main_sticky_followup(self, session: SessionRecord) -> None:
        self._session_transitions.clear_main_followup(session=session)

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
        return MainPlanFlow._main_agent_token_session(
            session=session,
        )

    def _update_main_agent_token_session(
        self,
        *,
        session: SessionRecord,
        goal_text: str,
        execution: dict[str, Any],
    ) -> None:
        return self._main_plan_flow._update_main_agent_token_session(
            session=session,
            goal_text=goal_text,
            execution=execution,
        )

    @staticmethod
    def _truncate_for_token_session(value: str, limit: int) -> str:
        return MainPlanFlow._truncate_for_token_session(
            value=value,
            limit=limit,
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
        pending_metadata = {"source": "router._store_pending_clarification"}
        if isinstance(metadata, dict):
            pending_metadata.update(metadata)
        self._pending_interaction_coordinator.store(
            session=session,
            intent=intent.value,
            entities=dict(entities),
            missing_fields=[str(item) for item in missing_fields if str(item).strip()],
            question=question,
            kind=kind,
            skill_id=skill_id,
            metadata=pending_metadata,
            reason="router._store_pending_clarification",
        )

    def _clear_pending_clarification(self, session: SessionRecord) -> None:
        self._pending_interaction_coordinator.clear(
            session=session,
            reason="router._clear_pending_clarification",
        )

    def _cancel_pending_interaction(self, *, session: SessionRecord, reason: str) -> bool:
        return self._pending_interaction_coordinator.cancel(
            session=session,
            reason=reason,
        )

    def _continue_pending_interaction(
        self,
        *,
        session: SessionRecord,
        entities: dict[str, Any],
        missing_fields: list[str],
        question: str | None,
    ) -> bool:
        return self._pending_interaction_coordinator.continue_interaction(
            session=session,
            entities=dict(entities),
            missing_fields=[str(item) for item in missing_fields if str(item).strip()],
            question=question,
            status="pending",
            metadata_updates={"source": "router._continue_pending_interaction"},
            reason="router._continue_pending_interaction",
        )

    def _pending_clarification(self, session: SessionRecord) -> dict[str, Any] | None:
        return self._pending_interaction_coordinator.get(session=session)

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
        return self._action_execution_service.execute(
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
        return self._main_plan_flow._execute_main_plan(
            plan=plan,
            session=session,
            session_id=session_id,
            source_interface=source_interface,
            requested_by_user_id=requested_by_user_id,
            agent_id=agent_id,
            goal_text=goal_text,
            request_context=request_context,
        )

    def _set_owner(self, session: SessionRecord, new_owner: SessionOwner) -> None:
        self._session_transitions.set_owner(session=session, owner=new_owner)

    def _set_state(self, session: SessionRecord, new_state: SessionState) -> None:
        self._session_transitions.set_state(session=session, state=new_state)

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
        self._turn_finalizer.set_skill_context_contracts(self._skill_context_contracts)
        return self._turn_finalizer.build_response(
            request_id=self._request_id_var.get(),
            session=session,
            intent=intent,
            classification=classification,
            route=route,
            result=result,
            request_text=request_text,
            user_id=user_id,
        )

    def _build_working_context_packet(
        self,
        *,
        session: SessionRecord,
        user_id: str,
        request_text: str,
        route_hint: str,
        intent_hint: str | None,
    ):
        return self._context_flow._build_working_context_packet(
            session=session,
            user_id=user_id,
            request_text=request_text,
            route_hint=route_hint,
            intent_hint=intent_hint,
        )

    def _skill_memory_handoff_context(
        self,
        *,
        relevant_memory: list[dict[str, Any]],
        intent: str | None,
        request_text: str,
    ) -> dict[str, Any]:
        return self._context_flow._skill_memory_handoff_context(
            relevant_memory=relevant_memory,
            intent=intent,
            request_text=request_text,
        )

    def _resolve_handoff_followup_entities(
        self,
        *,
        session: SessionRecord,
        decision: MicroDecision,
        working_context: dict[str, Any],
    ) -> MicroDecision:
        return self._context_flow._resolve_handoff_followup_entities(
            session=session,
            decision=decision,
            working_context=working_context,
        )

    def export_session_context_snapshot(
        self,
        *,
        session_id: str,
        include_legacy: bool = True,
        include_working_context: bool = True,
        include_recent_events: bool = True,
        recent_events_limit: int = 120,
    ) -> dict[str, Any] | None:
        return self._context_flow.export_session_context_snapshot(
            session_id=session_id,
            include_legacy=include_legacy,
            include_working_context=include_working_context,
            include_recent_events=include_recent_events,
            recent_events_limit=recent_events_limit,
        )

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
        return self._context_flow._relevant_memory_context(
            user_id=user_id,
            session_id=session_id,
            limit=limit,
        )
