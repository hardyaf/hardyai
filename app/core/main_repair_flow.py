from __future__ import annotations

from typing import Any

from app.core.micro_jarvis import MicroDecision
from app.core.session_store import SessionRecord
from app.core.types import FAST_COMMAND_INTENTS, MAIN_ACTION_INTENTS, Intent, SessionOwner, SessionState
from app.schemas.api import AskRequest


class MainRepairFlow:
    """Own Main action repair, confidence gates, and clarification extraction."""

    def __init__(self, router_ports: Any) -> None:
        self._router = router_ports

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
        router = self._router
        missing_fields = [str(item) for item in (required_missing_fields or []) if str(item).strip()]
        if not missing_fields and not router._should_attempt_main_repair(micro_decision):
            return None

        repair_working_context = (
            dict(working_context_payload)
            if isinstance(working_context_payload, dict)
            else router._build_working_context_packet(
                session=session,
                user_id=payload.user_id,
                request_text=payload.text,
                route_hint="main_repair",
                intent_hint=micro_decision.intent.value,
            ).to_dict()
        )
        repair_agent_id = str(payload.context.get("agent_id") or "jarvis").strip().lower() or "jarvis"
        runtime_capability_catalog = router._runtime_capability_catalog(
            payload=payload,
            agent_id=repair_agent_id,
        )
        repair = router._main_jarvis.repair_action(
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
        router._event_log.record(
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
            repaired = router._repair_decision_from_main(repair, micro_decision)
            if repaired is None:
                return None
            repaired = router._resolve_followup_entities(session=session, decision=repaired)
            if router._child_action_denied(payload.context, repaired.intent):
                router._cancel_pending_interaction(
                    session=session,
                    reason="identity_policy_denied_repaired_action",
                )
                router._set_owner(session, SessionOwner.SYSTEM)
                router._set_state(session, SessionState.IDLE)
                return router._build_response(
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
                        "message": router._CHILD_ACTION_DENIAL_MESSAGE,
                        "policy_profile": payload.context.get("policy_profile"),
                    },
                    request_text=payload.text,
                    user_id=payload.user_id,
                )
            if repaired.intent == Intent.CALENDAR_ADD_EVENT:
                entities = router._apply_text_constraints(
                    intent=repaired.intent,
                    text=payload.text,
                    entities=dict(repaired.entities),
                )
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
                    micro_entities = router._normalize_entities_for_intent(
                        intent=Intent.CALENDAR_ADD_EVENT,
                        entities=dict(micro_decision.entities),
                    )
                    micro_entities = router._apply_text_constraints(
                        intent=Intent.CALENDAR_ADD_EVENT,
                        text=payload.text,
                        entities=micro_entities,
                    )
                    micro_missing = router._required_fields_for_intent(
                        intent=Intent.CALENDAR_ADD_EVENT,
                        entities=micro_entities,
                    )
                    if not micro_missing:
                        repaired = MicroDecision(
                            intent=Intent.CALENDAR_ADD_EVENT,
                            confidence=max(repaired.confidence, micro_decision.confidence),
                            entities=micro_entities,
                            ambiguity_flags=list(repaired.ambiguity_flags),
                            recommended_owner=SessionOwner.MAIN,
                            reasoning=f"{repaired.reasoning}_using_high_conf_micro_calendar_entities",
                        )

            repaired_missing = router._required_fields_for_intent(
                intent=repaired.intent,
                entities=repaired.entities,
            )
            if repaired_missing:
                micro_missing = router._required_fields_for_intent(
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
                question = router._clarification_question(intent=repaired.intent, field_name=repaired_missing[0])
                router._store_pending_clarification(
                    session=session,
                    intent=repaired.intent,
                    entities=repaired.entities,
                    missing_fields=repaired_missing,
                    question=question,
                )
                router._arm_main_sticky_followup(session=session, reason="main_repair_missing_fields")
                router._set_owner(session, SessionOwner.MAIN)
                router._set_state(session, SessionState.AWAITING_CONFIRMATION)
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
                return router._build_response(
                    session=session,
                    intent=repaired.intent,
                    classification=classification,
                    route="main_jarvis_repair",
                    result=result,
                    request_text=payload.text,
                    user_id=payload.user_id,
                )

            confidence_clarification_response = router._maybe_require_confidence_clarification(
                payload=payload,
                session=session,
                micro_decision=micro_decision,
                repaired_decision=repaired,
                repair=repair,
            )
            if confidence_clarification_response is not None:
                return confidence_clarification_response

            router._set_owner(session, SessionOwner.MAIN)
            router._set_state(session, SessionState.ERROR_RECOVERY)
            tool_result = router._execute_fast_command(
                decision=repaired,
                source_interface=payload.source,
                requested_by_user_id=payload.user_id,
                agent_id=str(payload.context.get("agent_id") or "jarvis"),
                request_context=payload.context,
            )
            router._event_log.record(
                event_type="main.repair.executed",
                session_id=session.session_id,
                payload={
                    "intent": repaired.intent.value,
                    "result_status": tool_result.get("status"),
                },
            )
            router._clear_pending_clarification(session)
            router._set_state(session, SessionState.IDLE)
            classification = repaired.to_dict()
            classification["recovered_from"] = micro_decision.to_dict()
            classification["repair_status"] = "resolved_action"
            result = dict(tool_result)
            result["repaired_by"] = "main_jarvis"
            result["repair_reasoning"] = str(repair.get("reasoning") or "")
            result["repair_confidence"] = repair.get("confidence")
            result["repair_source"] = repair.get("source")
            return router._build_response(
                session=session,
                intent=repaired.intent,
                classification=classification,
                route="main_jarvis_repair",
                result=result,
                request_text=payload.text,
                user_id=payload.user_id,
            )

        if repair_status == "needs_clarification":
            maybe_intent = router._coerce_intent(str(repair.get("intent") or ""))
            intent = maybe_intent or Intent.UNKNOWN
            pending_entities = repair.get("entities")
            if not isinstance(pending_entities, dict):
                pending_entities = {}
            if maybe_intent is not None:
                pending_entities = router._normalize_entities_for_intent(intent=maybe_intent, entities=pending_entities)
            pending_missing = repair.get("missing_fields")
            if not isinstance(pending_missing, list):
                pending_missing = []
            if maybe_intent is not None:
                router._store_pending_clarification(
                    session=session,
                    intent=maybe_intent,
                    entities=pending_entities,
                    missing_fields=[str(item) for item in pending_missing if str(item).strip()],
                    question=str(repair.get("question") or "").strip() or None,
                )
            router._arm_main_sticky_followup(session=session, reason="main_repair_needs_clarification")
            router._set_owner(session, SessionOwner.MAIN)
            router._set_state(session, SessionState.AWAITING_CONFIRMATION)
            classification = micro_decision.to_dict()
            classification["repair_status"] = "needs_clarification"
            classification["repair_candidate_intent"] = repair.get("intent")
            classification["repair_reasoning"] = repair.get("reasoning")
            classification["repair_source"] = repair.get("source")
            result = dict(repair)
            result["repaired_by"] = "main_jarvis"
            return router._build_response(
                session=session,
                intent=intent,
                classification=classification,
                route="main_jarvis_repair",
                result=result,
                request_text=payload.text,
                user_id=payload.user_id,
            )

        if repair_status == "not_actionable":
            if router._should_surface_not_actionable(repair=repair, micro_decision=micro_decision):
                message = str(repair.get("message") or "").strip()
                if not message:
                    message = "I understand the intent, but that capability is not wired yet."
                router._set_owner(session, SessionOwner.MAIN)
                router._set_state(session, SessionState.IDLE)
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
                return router._build_response(
                    session=session,
                    intent=Intent.CONVERSATIONAL,
                    classification=classification,
                    route="main_jarvis_repair",
                    result=result,
                    request_text=payload.text,
                    user_id=payload.user_id,
                )
            fallback_response = router._fallback_repair_to_missing_fields_clarification(
                payload=payload,
                session=session,
                micro_decision=micro_decision,
                preferred_missing_fields=missing_fields,
                fallback_reason="main_repair_not_actionable_missing_fields_fallback",
            )
            if fallback_response is not None:
                return fallback_response
            return None

        fallback_response = router._fallback_repair_to_missing_fields_clarification(
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
        router = self._router
        if micro_decision.intent not in FAST_COMMAND_INTENTS:
            return None

        fallback_entities = router._normalize_entities_for_intent(
            intent=micro_decision.intent,
            entities=dict(micro_decision.entities),
        )
        fallback_missing = [str(item) for item in (preferred_missing_fields or []) if str(item).strip()]
        if not fallback_missing:
            fallback_missing = router._required_fields_for_intent(
                intent=micro_decision.intent,
                entities=fallback_entities,
            )
        if not fallback_missing:
            return None

        question = router._clarification_question(
            intent=micro_decision.intent,
            field_name=fallback_missing[0],
        )
        router._store_pending_clarification(
            session=session,
            intent=micro_decision.intent,
            entities=fallback_entities,
            missing_fields=fallback_missing,
            question=question,
        )
        router._arm_main_sticky_followup(session=session, reason=fallback_reason)
        router._set_owner(session, SessionOwner.MAIN)
        router._set_state(session, SessionState.AWAITING_CONFIRMATION)

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
        return router._build_response(
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

    def _repair_decision_from_main(
        self,
        repair: dict[str, Any],
        micro_decision: MicroDecision,
    ) -> MicroDecision | None:
        router = self._router
        intent = router._coerce_intent(str(repair.get("intent") or ""))
        if intent is None or intent not in MAIN_ACTION_INTENTS:
            return None
        confidence_raw = repair.get("confidence")
        confidence = 0.6
        if isinstance(confidence_raw, (int, float)):
            confidence = max(0.0, min(float(confidence_raw), 1.0))
        entities = repair.get("entities")
        if not isinstance(entities, dict):
            entities = {}
        entities = router._normalize_entities_for_intent(intent=intent, entities=entities)
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
        router = self._router
        gate_reason = router._confidence_gate_reason(
            micro_decision=micro_decision,
            repaired_decision=repaired_decision,
        )
        if gate_reason is None:
            return None

        clarification_entities = dict(repaired_decision.entities)
        clarification_field = router._default_clarification_field_for_intent(repaired_decision.intent)
        missing_fields: list[str] = []
        if clarification_field:
            clarification_entities.pop(clarification_field, None)
            missing_fields = [clarification_field]

        if missing_fields:
            question = router._clarification_question(intent=repaired_decision.intent, field_name=missing_fields[0])
            router._store_pending_clarification(
                session=session,
                intent=repaired_decision.intent,
                entities=clarification_entities,
                missing_fields=missing_fields,
                question=question,
            )
            router._set_state(session, SessionState.AWAITING_CONFIRMATION)
        else:
            question = "Can you restate that with the exact action and target so I do not run the wrong command?"
            router._set_state(session, SessionState.CONVERSATIONAL)

        router._arm_main_sticky_followup(session=session, reason=f"confidence_gate:{gate_reason}")
        router._set_owner(session, SessionOwner.MAIN)
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
        return router._build_response(
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
        router = self._router
        confidence = max(0.0, min(float(repaired_decision.confidence), 1.0))
        reasoning = str(repaired_decision.reasoning or "").strip().lower()
        if "asr_recovery" in reasoning and confidence >= 0.65:
            return None
        ambiguity_flags = router._meaningful_ambiguity_flags(
            micro_flags=micro_decision.ambiguity_flags,
            repaired_flags=repaired_decision.ambiguity_flags,
        )
        if confidence < router._main_low_confidence_floor:
            return "low_confidence"
        if router._is_high_risk_bulk_write(repaired_decision) and confidence < router._main_high_risk_confidence_threshold:
            return "high_risk_low_confidence"
        if confidence < router._main_conversational_confidence_threshold and ambiguity_flags:
            return "ambiguous_mid_confidence"
        return None

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

    def _extract_clarification_updates_with_main_repair(
        self,
        *,
        session: SessionRecord,
        payload: AskRequest,
        intent: Intent,
        missing_fields: list[str],
        current_entities: dict[str, Any],
    ) -> dict[str, Any]:
        router = self._router
        text = payload.text
        working_context = router._build_working_context_packet(
            session=session,
            user_id=session.user_id,
            request_text=text,
            route_hint="main_repair_clarification",
            intent_hint=intent.value,
        ).to_dict()
        repair = router._main_jarvis.repair_action(
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
                "runtime_capability_catalog": router._runtime_capability_catalog(
                    payload=payload,
                    agent_id=router._active_agent_id(session),
                ),
                "working_context": working_context,
                "session_summary": working_context.get("session_summary"),
                "recent_turns": working_context.get("recent_turns"),
                "entity_hints": working_context.get("entity_hints"),
                "pending_interaction": working_context.get("pending_interaction"),
                "budget_metadata": working_context.get("budget_metadata"),
                "agent_id": router._active_agent_id(session),
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
        repair_intent = router._coerce_intent(str(repair.get("intent") or ""))
        router._event_log.record(
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
            not router._main_pending_clarification_heuristic_fallback_enabled
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
        normalized = router._normalize_entities_for_intent(intent=intent, entities=entities)
        constrained = router._apply_text_constraints(
            intent=intent,
            text=text,
            entities=normalized,
        )

        allowed_fields = set(missing_fields)
        allowed_fields.update(router._clarification_supplemental_fields(intent=intent))
        allowed_fields.update(
            key
            for key, value in constrained.items()
            if key not in normalized or normalized.get(key) != value
        )
        updates: dict[str, Any] = {}
        for field_name in allowed_fields:
            value = constrained.get(field_name)
            if not router._entity_value_present(value):
                continue
            updates[field_name] = value
        return updates
