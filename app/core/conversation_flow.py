from __future__ import annotations

import re
from typing import Any

from app.core.micro_jarvis import MicroDecision
from app.core.session_store import SessionRecord
from app.core.types import Intent, SessionOwner, SessionState
from app.schemas.api import AskRequest


FOLLOWUP_PRONOUN_PATTERN = re.compile(
    r"^(?:do|does|did|are|is|can|could|would|will|should)\s+"
    r"(?=.{1,100}$).*\b(?:they|it|he|she|them|those|that|this)\b",
    flags=re.IGNORECASE,
)
FOLLOWUP_ELLIPTICAL_PATTERN = re.compile(
    r"^(?:do both|are they|can it|does that|does this|they both)\b",
    flags=re.IGNORECASE,
)
CONVERSATION_PENDING_QUESTION_PATTERN = re.compile(r"^(?:who|which|what)\b", flags=re.IGNORECASE)
CONVERSATION_PENDING_CONFIRM_PATTERN = re.compile(
    r"^(?:is|are|do|does|did|can|could|would|will|should|has|have)\b",
    flags=re.IGNORECASE,
)


class ConversationFlow:
    """Own conversational follow-up detection and tool follow-up continuation."""

    def __init__(self, router_ports: Any) -> None:
        self._router = router_ports

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
        router = self._router
        if decision.intent not in {Intent.CONVERSATIONAL, Intent.UNKNOWN}:
            return classification, response
        if router._pending_clarification(session) is not None:
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

        missing_fields = router._infer_conversation_pending_fields(question=question)
        if not missing_fields:
            return classification, response

        topic_hint = router._extract_contextual_topic_hint(working_context_payload)
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
        router._store_pending_conversation(
            session=session,
            entities=pending_entities,
            missing_fields=missing_fields,
            question=question,
            metadata=metadata,
        )
        router._arm_main_sticky_followup(session=session, reason="conversation_clarification_pending")
        router._set_owner(session, SessionOwner.MAIN)
        router._set_state(session, SessionState.AWAITING_CONFIRMATION)

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
        router = self._router
        cleaned = re.sub(r"\s+", " ", str(text or "").strip())
        if not cleaned:
            return None
        followup_signal = router._looks_like_contextual_followup_text(
            cleaned,
            working_context=working_context,
        )
        if not followup_signal:
            return None
        topic_hint = router._extract_contextual_topic_hint(working_context)
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
        router = self._router
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
                    return router._preferred_conversation_topic_hint(entity=entity, fallback=display_name)
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

    def _looks_like_contextual_followup_text(self, text: str, *, working_context: dict[str, Any]) -> str | None:
        router = self._router
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
            if router._recent_turns_have_clarification_question(working_context=working_context):
                return "short_followup_after_question"
            return "short_noun_phrase"
        if (
            lowered.endswith("?")
            and token_count <= 8
            and router._recent_turns_have_clarification_question(working_context=working_context)
        ):
            return "short_question_after_question"
        return None

    def _maybe_open_tool_followup(
        self,
        session: SessionRecord,
        decision: MicroDecision,
        tool_result: dict[str, Any],
        request_text: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        router = self._router
        status = str(tool_result.get("status") or "").strip().lower()
        if status == "ok":
            return None

        entities = router._normalize_entities_for_intent(intent=decision.intent, entities=dict(decision.entities))
        missing_fields = tool_result.get("missing_fields")
        if not isinstance(missing_fields, list):
            missing_fields = []
        missing_fields = [str(item) for item in missing_fields if str(item).strip()]
        question = str(tool_result.get("question") or "").strip() or None
        registry = router._entity_registry_manager.get_registry(session=session)
        intent_value = decision.intent.value
        for contract in router._skill_context_contracts:
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
                router._event_log.record(
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
            missing_fields = router._required_fields_for_intent(intent=decision.intent, entities=entities)
        if not missing_fields:
            return None

        if question is None:
            question = router._clarification_question(intent=decision.intent, field_name=missing_fields[0])

        router._store_pending_clarification(
            session=session,
            intent=decision.intent,
            entities=entities,
            missing_fields=missing_fields,
            question=question,
        )
        router._arm_main_sticky_followup(session=session, reason="tool_followup_required")
        router._set_owner(session, SessionOwner.MAIN)
        router._set_state(session, SessionState.AWAITING_CONFIRMATION)

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

        return router._build_response(
            session=session,
            intent=decision.intent,
            classification=classification,
            route="main_jarvis_repair",
            result=result,
            request_text=request_text,
            user_id=user_id,
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
        router = self._router
        pending_question = str(pending.get("question") or "").strip()
        pending_metadata = pending.get("metadata")
        if not isinstance(pending_metadata, dict):
            pending_metadata = {}
        topic_subject = (
            str(merged_entities.get("topic_subject") or "").strip()
            or str(merged_entities.get("topic_entity") or "").strip()
        )
        confirmation = str(merged_entities.get("confirmation") or "").strip().lower()
        followup_prompt = router._conversation_followup_prompt(
            user_text=payload.text,
            pending_question=pending_question,
            topic_subject=topic_subject,
            confirmation=confirmation,
        )
        working_context = router._build_working_context_packet(
            session=session,
            user_id=payload.user_id,
            request_text=payload.text,
            route_hint="main_pending_conversation_followup",
            intent_hint=intent.value,
        ).to_dict()
        pending_agent_id = str(payload.context.get("agent_id") or "jarvis").strip().lower() or "jarvis"
        response = router._main_jarvis.respond(
            text=followup_prompt,
            context={
                "micro_intent": Intent.CONVERSATIONAL.value,
                "micro_confidence": 0.72,
                "micro_entities": merged_entities,
                "micro_ambiguity_flags": ["conversation_clarification_completed"],
                "runtime_skill_intents": [Intent.CONVERSATIONAL.value],
                "runtime_capability_catalog": router._runtime_capability_catalog(
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
        router._clear_pending_clarification(session)
        router._clear_main_sticky_followup(session)
        router._set_owner(session, SessionOwner.MAIN)
        result_status = str(response.get("status") or "").strip().lower()
        if result_status in {"conversation", "planned"}:
            router._set_state(session, SessionState.CONVERSATIONAL)
        else:
            router._set_state(session, SessionState.IDLE)
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
        return router._build_response(
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
