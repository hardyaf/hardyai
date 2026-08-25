from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.context.entity_registry import EntityRegistryManager
from app.context.session_context_manager import SessionContextManager
from app.context.summarizer import SessionSummaryManager
from app.core.assistant_response import build_assistant_payload
from app.core.session_store import SessionRecord, SessionStore
from app.core.pending_interaction import PendingInteractionCoordinator
from app.core.persistence_policy import (
    PersistencePolicy,
    most_restrictive_persistence_policy,
    persistence_policy_for_intent,
)
from app.core.state_machine import RuntimePowerController
from app.core.types import (
    EMAIL_AGENT_INTENTS,
    FAST_COMMAND_INTENTS,
    Intent,
    SessionOwner,
    SessionState,
)
from app.services.conversation_history_service import ConversationHistoryService
from app.services.durable_write_service import DurableWriteService
from app.services.event_log import EventLogService
from app.services.memory_service import MemoryService
from app.skills.authorized_executor import AuthorizedSkillExecutor


@dataclass(frozen=True)
class TurnFinalizationOptions:
    """Select optional history work without changing response/ticket semantics."""

    record_context_history: bool = True
    record_recent_turns: bool = True
    record_conversation_history: bool = True
    record_memory: bool = True
    capture_ticket: bool = True
    persistence_policy: PersistencePolicy | None = None


class TurnFinalizer:
    """Own the single post-execution response, receipt, context, and write boundary."""

    def __init__(
        self,
        *,
        session_store: SessionStore,
        runtime_power: RuntimePowerController,
        event_log: EventLogService,
        memory_service: MemoryService | None,
        durable_write_service: DurableWriteService | None,
        conversation_history_service: ConversationHistoryService | None,
        action_ticket_service: Any | None,
        skill_registry: Any | None,
        authorized_skill_executor: AuthorizedSkillExecutor,
        skill_context_contracts: list[Any],
        entity_registry_manager: EntityRegistryManager,
        pending_interaction_coordinator: PendingInteractionCoordinator,
        session_context_manager: SessionContextManager,
        session_summary_manager: SessionSummaryManager,
    ) -> None:
        self._session_store = session_store
        self._runtime_power = runtime_power
        self._event_log = event_log
        self._memory_service = memory_service
        self._durable_write_service = durable_write_service
        self._conversation_history_service = conversation_history_service
        self._action_ticket_service = action_ticket_service
        self._skill_registry = skill_registry
        self._authorized_skill_executor = authorized_skill_executor
        self._skill_context_contracts = list(skill_context_contracts)
        self._entity_registry_manager = entity_registry_manager
        self._pending_interaction_coordinator = pending_interaction_coordinator
        self._session_context_manager = session_context_manager
        self._session_summary_manager = session_summary_manager

    def set_skill_context_contracts(self, contracts: list[Any]) -> None:
        """Keep compatibility with runtime/test contract replacement during extraction."""

        self._skill_context_contracts = list(contracts)

    def build_response(
        self,
        *,
        request_id: str | None,
        session: SessionRecord,
        intent: Intent,
        classification: dict[str, Any],
        route: str,
        result: dict[str, Any],
        request_text: str,
        user_id: str,
        options: TurnFinalizationOptions | None = None,
    ) -> dict[str, Any]:
        finalization = options or TurnFinalizationOptions()
        effective_request_id = str(request_id or uuid4())
        internal_result_payload = dict(result)
        declared_policy = internal_result_payload.pop("_persistence_policy", None)
        policy = most_restrictive_persistence_policy(
            persistence_policy_for_intent(intent.value),
            declared_policy,
            finalization.persistence_policy,
        )
        if finalization.record_context_history and policy.record_entity_context:
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
        skill = self._authorized_skill_executor.resolve(
            intent=intent.value,
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
        session_runtime: dict[str, Any] = {
            "last_activity_at": session.last_activity_timestamp,
        }
        channel_runtime = session.context_reference.get("channel_session")
        if isinstance(channel_runtime, dict):
            session_runtime["channel"] = channel_runtime

        response: dict[str, Any] = {
            "request_id": effective_request_id,
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
        delivery: dict[str, Any] = {
            "session": {"status": "committed"},
            "event_log": {"status": "committed"},
            "skill_telemetry": {
                "status": "committed" if self._skill_registry is not None else "not_configured"
            },
        }

        if (
            self._action_ticket_service is not None
            and finalization.capture_ticket
            and policy.capture_ticket
        ):
            capture = self._action_ticket_service.capture_response(
                request_id=effective_request_id,
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
            delivery["ticket"] = {"status": "committed"}
        else:
            delivery["ticket"] = {"status": "not_applicable"}

        result_status = str(result_payload.get("status") or "").strip().lower() or None
        if (
            finalization.record_context_history
            and finalization.record_recent_turns
            and policy.record_recent_turns
        ):
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
        if finalization.record_conversation_history and policy.record_conversation_history:
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
            delivery["conversation_history"] = {"status": "committed"}
        else:
            delivery["conversation_history"] = {"status": "not_applicable"}
        if (
            finalization.record_memory
            and self._memory_service is not None
            and policy.record_memory
        ):
            delivery["memory"] = self._record_memory_interaction(
                request_id=effective_request_id,
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
        else:
            delivery["memory"] = {"status": "not_applicable"}
        response["delivery"] = delivery
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
                "delivery": delivery,
            },
        )
        return response

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
        emitted_entities: list[dict[str, Any]] = []
        for contract in self._skill_context_contracts:
            if not contract.supports_intent(intent=intent.value):
                continue
            try:
                emitted_entities.extend(contract.emit_context_updates(intent=intent.value, result=result))
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                self._event_log.record(
                    event_type="context.contract.emit_context_updates.failed",
                    session_id=session.session_id,
                    payload={
                        "contract_id": str(getattr(contract, "contract_id", "") or ""),
                        "intent": intent.value,
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
                "intent": intent.value,
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
        request_id: str,
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
        try:
            if self._durable_write_service is not None:
                return self._durable_write_service.enqueue_memory_interaction(
                    request_id=request_id,
                    session_id=session_id,
                    user_id=user_id,
                    source=source,
                    intent=intent,
                    route=route,
                    request_text=request_text,
                    response_summary=response_summary,
                    metadata=dict(metadata),
                )
            self._memory_service.record_interaction(
                session_id=session_id,
                user_id=user_id,
                source=source,
                intent=intent,
                route=route,
                request_text=request_text,
                response_summary=response_summary,
                metadata=dict(metadata),
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

    @classmethod
    def _task_label_owner(
        cls,
        *,
        intent: Intent,
        classification: dict[str, Any],
        route: str,
    ) -> SessionOwner | None:
        if intent in {Intent.SYSTEM_WAKE, Intent.SYSTEM_SLEEP} or route in {"runtime_power", "sleep_guard"}:
            return None
        if route in {"main_jarvis", "main_jarvis_repair", "main_skill", "main_jarvis_commitment"}:
            return SessionOwner.MAIN
        if route == "micro_tool":
            return SessionOwner.MICRO
        recommended_owner = cls._coerce_owner(str(classification.get("recommended_owner") or ""))
        if recommended_owner in {SessionOwner.MICRO, SessionOwner.MAIN}:
            return recommended_owner
        return SessionOwner.MICRO if intent in FAST_COMMAND_INTENTS else None

    @staticmethod
    def _coerce_owner(raw: str) -> SessionOwner | None:
        normalized = raw.strip().lower()
        return next((owner for owner in SessionOwner if owner.value == normalized), None)

    @classmethod
    def _main_intent_label(
        cls,
        *,
        route: str,
        intent: Intent,
        classification: dict[str, Any],
        result: dict[str, Any],
    ) -> str | None:
        if route not in {"main_jarvis", "main_jarvis_repair", "main_skill", "main_jarvis_commitment"}:
            return None
        domain_label = cls._main_domain_label(intent=intent, classification=classification, result=result)
        if cls._is_followup_turn(classification=classification, result=result):
            if domain_label == "gen question":
                return "follow up from previous"
            return f"follow up from previous | {domain_label}"
        return domain_label

    @classmethod
    def _main_domain_label(
        cls,
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
            coerced = cls._coerce_intent(candidate)
            if coerced is not None:
                effective_intent = coerced
            elif inferred_intent.startswith("home."):
                return "thermostat action" if "thermostat" in inferred_intent else "home action"
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
    def _coerce_intent(raw_intent: str) -> Intent | None:
        normalized = str(raw_intent or "").strip().lower()
        return next((intent for intent in Intent if intent.value == normalized), None)

    @staticmethod
    def _is_followup_turn(*, classification: dict[str, Any], result: dict[str, Any]) -> bool:
        ambiguity_flags_raw = classification.get("ambiguity_flags")
        ambiguity_flags = {
            str(item).strip().lower()
            for item in (ambiguity_flags_raw if isinstance(ambiguity_flags_raw, list) else [])
            if str(item).strip()
        }
        if {"clarification_pending", "clarification_completed", "cancelled_pending_clarification"} & ambiguity_flags:
            return True
        if str(classification.get("reasoning") or "").strip().lower().startswith("pending_clarification"):
            return True
        if classification.get("cancelled_intent") is not None:
            return True
        if str(result.get("repair_source") or "").strip().lower() == "clarification_followup":
            return True
        return str(result.get("status") or "").strip().lower() == "cancelled"

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
        return str(result.get("status") or "").strip().lower() == "conversation"

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
        if not self._is_conversation_skill_turn(skill=skill, intent=intent, route=route, result=result):
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
        topic_label = str(entry.get("topic_label") or "").strip() or topic_key.replace("_", " ").strip()
        if not topic_label or topic_key in {"general_conversation", "identity", "capabilities"}:
            return
        topic_terms = entry.get("topic_terms")
        aliases = [
            str(item).strip()
            for item in topic_terms
            if isinstance(topic_terms, list) and str(item).strip()
        ]
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
        *,
        session: SessionRecord,
        route: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        result_status = str(result.get("status") or "").strip().lower()
        question = str(result.get("question") or "").strip() or None
        missing_fields = result.get("missing_fields")
        if not isinstance(missing_fields, list):
            missing_fields = []
        pending = self._pending_interaction_coordinator.get(session=session)
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

    @staticmethod
    def _active_agent_id(session: SessionRecord) -> str:
        value = str(session.context_reference.get("active_agent_id") or "jarvis").strip().lower()
        return value or "jarvis"
