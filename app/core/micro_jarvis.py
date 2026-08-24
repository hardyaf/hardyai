from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.text_normalization import normalize_skill_anchor_spelling
from app.core.types import EMAIL_AGENT_INTENTS, FAST_COMMAND_INTENTS, Intent, SessionOwner
from app.skills.domains.lists.planning import parse_list_create_and_add
from app.skills.patterns import extract_all_lights_action, extract_switch_action


class MicroInferenceBackend(Protocol):
    def classify(self, text: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Return optional model-driven classification payload."""


class NullMicroInferenceBackend:
    def classify(self, text: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return None


@dataclass
class MicroDecision:
    intent: Intent
    confidence: float
    entities: dict[str, Any] = field(default_factory=dict)
    ambiguity_flags: list[str] = field(default_factory=list)
    recommended_owner: SessionOwner = SessionOwner.MAIN
    reasoning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "entities": self.entities,
            "ambiguity_flags": self.ambiguity_flags,
            "recommended_owner": self.recommended_owner.value,
            "reasoning": self.reasoning,
        }


class MicroJarvis:
    def __init__(
        self,
        backend: MicroInferenceBackend | None = None,
        fast_confidence_threshold: float = 0.72,
        heuristic_fallback_enabled: bool = True,
    ) -> None:
        self._backend = backend or NullMicroInferenceBackend()
        self._fast_confidence_threshold = fast_confidence_threshold
        self._heuristic_fallback_enabled = heuristic_fallback_enabled

    @staticmethod
    def looks_like_wake_command(text: str) -> bool:
        normalized = text.strip().lower()
        return bool(
            re.search(r"\b(wake up|wake jarvis|jarvis wake|jarvis i'm here|jarvis im here)\b", normalized)
        )

    @staticmethod
    def looks_like_sleep_command(text: str) -> bool:
        normalized = text.strip().lower()
        return bool(re.search(r"\b(go to sleep|sleep mode|jarvis sleep|sleep now)\b", normalized))

    def interpret(self, text: str, context: dict[str, Any] | None = None) -> MicroDecision:
        context = context or {}
        backend_result = self._backend.classify(text=text, context=context)
        decision = self._decision_from_backend(backend_result) if backend_result else None
        model_backend_configured = not isinstance(self._backend, NullMicroInferenceBackend)
        fail_open_to_heuristic = model_backend_configured and decision is None and not self._heuristic_fallback_enabled
        use_heuristic = self._heuristic_fallback_enabled or fail_open_to_heuristic
        heuristic_decision = self._heuristic_decision(text=text, context=context) if use_heuristic else None
        if decision is not None and decision.intent == Intent.CONVERSATIONAL and heuristic_decision is None:
            email_candidate = self._heuristic_decision(text=text, context=context)
            if email_candidate.intent in EMAIL_AGENT_INTENTS:
                heuristic_decision = email_candidate

        if decision is None and heuristic_decision is not None:
            if fail_open_to_heuristic:
                heuristic_decision.reasoning = f"heuristic_failopen_after_model_no_result:{heuristic_decision.reasoning}"
            decision = heuristic_decision
        elif (
            decision is not None
            and heuristic_decision is not None
            and decision.intent == Intent.UNKNOWN
            and heuristic_decision.intent != Intent.UNKNOWN
        ):
            heuristic_decision.reasoning = f"heuristic_override_after_{decision.reasoning}"
            decision = heuristic_decision
        elif (
            decision is not None
            and heuristic_decision is not None
            and decision.intent == Intent.CONVERSATIONAL
            and heuristic_decision.intent in EMAIL_AGENT_INTENTS
        ):
            heuristic_decision.reasoning = (
                f"email_heuristic_override_after_{decision.reasoning}"
            )
            decision = heuristic_decision
        elif (
            decision is not None
            and heuristic_decision is not None
            and heuristic_decision.intent in {
                Intent.EMAIL_MARK_SPAM,
                Intent.EMAIL_MARK_NEEDS_REPLY,
                Intent.EMAIL_MARK_COMPLETE,
            }
        ):
            heuristic_decision.reasoning = (
                f"explicit_email_spam_override_after_{decision.reasoning}"
            )
            decision = heuristic_decision
        elif (
            decision is not None
            and heuristic_decision is not None
            and decision.intent in {Intent.EMAIL_SUMMARIZE, Intent.EMAIL_DISCUSS}
            and heuristic_decision.intent == Intent.EMAIL_LIST_RECENT
            and "reference" not in heuristic_decision.entities
        ):
            heuristic_decision.reasoning = (
                f"email_collection_override_after_{decision.reasoning}"
            )
            decision = heuristic_decision
        elif (
            decision is not None
            and heuristic_decision is not None
            and decision.confidence < 0.55
            and heuristic_decision.confidence > decision.confidence
        ):
            heuristic_decision.reasoning = f"heuristic_boost_after_{decision.reasoning}"
            decision = heuristic_decision

        if decision is None:
            decision = MicroDecision(
                intent=Intent.UNKNOWN,
                confidence=0.0,
                reasoning="model_only_no_decision",
                ambiguity_flags=["unknown_intent", "model_only"],
            )
        elif heuristic_decision is not None:
            decision = self._merge_heuristic_entities(decision=decision, heuristic=heuristic_decision)

        decision = self._apply_guardrails(text=text, decision=decision)
        decision.recommended_owner = self._recommended_owner(decision)
        return decision

    def _decision_from_backend(self, data: dict[str, Any]) -> MicroDecision | None:
        raw_intent = str(data.get("intent") or "").strip().lower()
        intent_map = {item.value: item for item in Intent}
        intent = intent_map.get(raw_intent)
        if intent is None:
            return None

        confidence = data.get("confidence")
        if not isinstance(confidence, (float, int)):
            confidence = 0.5
        confidence = max(0.0, min(float(confidence), 1.0))
        entities = data.get("entities")
        if not isinstance(entities, dict):
            entities = {}
        entities = self._normalize_backend_entities(intent=intent, entities=entities)
        ambiguity_flags = data.get("ambiguity_flags")
        if not isinstance(ambiguity_flags, list):
            ambiguity_flags = []
        reasoning = str(data.get("reasoning") or "model_backend")
        return MicroDecision(
            intent=intent,
            confidence=confidence,
            entities=entities,
            ambiguity_flags=[str(item) for item in ambiguity_flags],
            reasoning=reasoning,
        )

    @staticmethod
    def _normalize_backend_entities(intent: Intent, entities: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(entities)
        if intent == Intent.HOME_SET_SWITCH:
            if "switch_name" not in normalized and isinstance(normalized.get("switch"), str):
                normalized["switch_name"] = normalized.get("switch")
        if intent == Intent.LIST_ADD_ITEM:
            if "item_text" not in normalized and isinstance(normalized.get("item"), str):
                normalized["item_text"] = normalized.get("item")
            if "list_name" not in normalized and isinstance(normalized.get("list"), str):
                normalized["list_name"] = normalized.get("list")
        if intent in {Intent.LIST_REMOVE_ITEM, Intent.LIST_MARK_ITEM_DONE}:
            if "item_text" not in normalized and isinstance(normalized.get("item"), str):
                normalized["item_text"] = normalized.get("item")
            if "list_name" not in normalized and isinstance(normalized.get("list"), str):
                normalized["list_name"] = normalized.get("list")
            if "completion_mode" not in normalized and isinstance(normalized.get("mode"), str):
                normalized["completion_mode"] = normalized.get("mode")
        if intent == Intent.LIST_GET_ITEMS:
            if "list_name" not in normalized and isinstance(normalized.get("list"), str):
                normalized["list_name"] = normalized.get("list")
        if intent == Intent.LIST_DELETE_LIST:
            if "list_name" not in normalized and isinstance(normalized.get("list"), str):
                normalized["list_name"] = normalized.get("list")
        if intent == Intent.CALENDAR_ADD_EVENT:
            if "event_title" not in normalized:
                for key in ["event_name", "title", "name", "subject", "event"]:
                    if isinstance(normalized.get(key), str) and str(normalized.get(key)).strip():
                        normalized["event_title"] = str(normalized.get(key)).strip()
                        break
            if "when_hint" not in normalized:
                for key in ["start_time", "when", "time", "start", "start_at", "date", "datetime"]:
                    if isinstance(normalized.get(key), str) and str(normalized.get(key)).strip():
                        normalized["when_hint"] = str(normalized.get(key)).strip()
                        break
            if "invitee_names" not in normalized:
                invitee_names = normalized.get("invitee_names") or normalized.get("invitees") or normalized.get(
                    "attendees"
                )
                if isinstance(invitee_names, str):
                    parts = re.split(r"\s*(?:,| and | & )\s*", invitee_names.strip())
                    names = [part.strip(" .") for part in parts if part.strip(" .")]
                    if names:
                        normalized["invitee_names"] = names
                elif isinstance(invitee_names, list):
                    names = [str(item).strip(" .") for item in invitee_names if str(item).strip(" .")]
                    if names:
                        normalized["invitee_names"] = names
            normalized.pop("person_name", None)
        if intent in {Intent.CALENDAR_UPDATE_EVENT, Intent.CALENDAR_DELETE_EVENT}:
            if "event_reference" not in normalized:
                for key in ["event_name", "event_title", "event", "title", "name", "reference"]:
                    if isinstance(normalized.get(key), str) and str(normalized.get(key)).strip():
                        normalized["event_reference"] = str(normalized.get(key)).strip()
                        break
            if intent == Intent.CALENDAR_UPDATE_EVENT:
                if "new_event_title" not in normalized:
                    for key in ["updated_title", "replacement_title", "rename_to"]:
                        if isinstance(normalized.get(key), str) and str(normalized.get(key)).strip():
                            normalized["new_event_title"] = str(normalized.get(key)).strip()
                            break
                if "new_when_hint" not in normalized:
                    for key in ["new_time", "new_when", "start_time", "when", "start", "date", "datetime"]:
                        if isinstance(normalized.get(key), str) and str(normalized.get(key)).strip():
                            normalized["new_when_hint"] = str(normalized.get(key)).strip()
                            break
                raw_all_day = normalized.get("all_day")
                if isinstance(raw_all_day, str):
                    lowered_all_day = raw_all_day.strip().lower()
                    if lowered_all_day in {"true", "yes", "1", "all-day", "all day"}:
                        normalized["all_day"] = True
                    elif lowered_all_day in {"false", "no", "0", "timed"}:
                        normalized["all_day"] = False
        return normalized

    @staticmethod
    def _missing_required_fields(intent: Intent, entities: dict[str, Any]) -> list[str]:
        required_by_intent: dict[Intent, list[str]] = {
            Intent.LIST_CREATE_LIST: ["list_name"],
            Intent.LIST_ADD_ITEM: ["list_name", "item_text"],
            Intent.LIST_GET_ITEMS: ["list_name"],
            Intent.LIST_DELETE_LIST: ["list_name"],
            Intent.LIST_REMOVE_ITEM: ["list_name", "item_text"],
            Intent.LIST_MARK_ITEM_DONE: ["list_name", "item_text"],
            Intent.HOME_SET_SWITCH: ["switch_name", "action"],
            Intent.CALENDAR_ADD_EVENT: ["event_title", "when_hint"],
            Intent.CALENDAR_VIEW: ["window"],
        }
        if intent in {Intent.CALENDAR_UPDATE_EVENT, Intent.CALENDAR_DELETE_EVENT}:
            event_reference = entities.get("event_id") or entities.get("event_reference")
            missing = [] if isinstance(event_reference, str) and event_reference.strip() else ["event_reference"]
            if intent == Intent.CALENDAR_UPDATE_EVENT and not any(
                key in entities and entities.get(key) is not None
                for key in ["new_event_title", "new_when_hint", "all_day"]
            ):
                missing.append("requested_change")
            return missing
        required = required_by_intent.get(intent, [])
        missing: list[str] = []
        for field_name in required:
            value = entities.get(field_name)
            if value is None:
                missing.append(field_name)
                continue
            if isinstance(value, str) and not value.strip():
                missing.append(field_name)
        return missing

    def _merge_heuristic_entities(self, decision: MicroDecision, heuristic: MicroDecision) -> MicroDecision:
        if decision.intent != heuristic.intent:
            return decision
        if decision.intent not in FAST_COMMAND_INTENTS:
            return decision

        decision_missing = self._missing_required_fields(decision.intent, decision.entities)
        if not decision_missing:
            return decision

        merged_entities = dict(decision.entities)
        merged = False
        for field_name in decision_missing:
            value = heuristic.entities.get(field_name)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            merged_entities[field_name] = value
            merged = True

        if not merged:
            return decision
        decision.entities = merged_entities
        decision.reasoning = f"{decision.reasoning}_merged_heuristic_entities"
        decision.confidence = max(decision.confidence, min(0.92, heuristic.confidence))
        return decision

    def _heuristic_decision(
        self,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> MicroDecision:
        cleaned = self._normalize_text(text)
        lowered = cleaned.lower()

        if self.looks_like_wake_command(cleaned):
            return MicroDecision(
                intent=Intent.SYSTEM_WAKE,
                confidence=0.99,
                reasoning="explicit_wake_phrase",
            )

        if self.looks_like_sleep_command(cleaned):
            return MicroDecision(
                intent=Intent.SYSTEM_SLEEP,
                confidence=0.99,
                reasoning="explicit_sleep_phrase",
            )

        email_decision = self._email_heuristic(cleaned=cleaned, context=context or {})
        if email_decision is not None:
            return email_decision

        all_lights_action = extract_all_lights_action(cleaned)
        if all_lights_action is not None:
            return MicroDecision(
                intent=Intent.HOME_SET_SWITCH,
                confidence=0.97,
                entities={
                    "switch_name": "all lights",
                    "action": all_lights_action,
                    "scope": "all",
                },
                ambiguity_flags=["bulk_scope_requires_planning"],
                reasoning="all_lights_pattern",
            )

        switch_match = extract_switch_action(cleaned)
        if switch_match is not None:
            switch_name, action = switch_match
            return MicroDecision(
                intent=Intent.HOME_SET_SWITCH,
                confidence=0.93,
                entities={
                    "switch_name": switch_name,
                    "action": action,
                },
                reasoning="switch_command_pattern",
            )

        calendar_mutation = self._calendar_mutation_heuristic(cleaned)
        if calendar_mutation is not None:
            return calendar_mutation

        calendar_match = re.match(
            r"^(?:add|put)\s+(?P<title>.+?)\s+to\s+(?P<target>.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if calendar_match:
            title = calendar_match.group("title").strip()
            target = calendar_match.group("target").strip()
            if "calendar" in target.lower():
                _, when_hint = self._calendar_target_details(target)
                return MicroDecision(
                    intent=Intent.CALENDAR_ADD_EVENT,
                    confidence=0.9,
                    entities={
                        "event_title": title,
                        "when_hint": when_hint,
                    },
                    reasoning="calendar_add_pattern",
                )
            list_name = re.sub(r"\s+list$", "", target, flags=re.IGNORECASE).strip()
            return MicroDecision(
                intent=Intent.LIST_ADD_ITEM,
                confidence=0.88,
                entities={
                    "item_text": title,
                    "list_name": list_name,
                },
                reasoning="list_add_pattern",
            )

        calendar_event_match = re.match(
            r"^add\s+(?:an?\s+)?(?:event|meeting|appointment)\s+(?:on|to)\s+(?P<target>.+?)\s+"
            r"(?:for|called|named)\s+(?P<title>.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if calendar_event_match:
            title = calendar_event_match.group("title").strip()
            target = calendar_event_match.group("target").strip()
            if "calendar" in target.lower():
                _, when_hint = self._calendar_target_details(target)
                return MicroDecision(
                    intent=Intent.CALENDAR_ADD_EVENT,
                    confidence=0.91,
                    entities={
                        "event_title": title,
                        "when_hint": when_hint,
                    },
                    reasoning="calendar_add_event_phrase_pattern",
                )

        calendar_on_match = re.match(
            r"^(?:add|put)\s+(?P<title>.+?)\s+on\s+(?P<target>.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if calendar_on_match:
            title = calendar_on_match.group("title").strip()
            target = calendar_on_match.group("target").strip()
            if "calendar" in target.lower():
                _, when_hint = self._calendar_target_details(target)
                return MicroDecision(
                    intent=Intent.CALENDAR_ADD_EVENT,
                    confidence=0.89,
                    entities={
                        "event_title": title,
                        "when_hint": when_hint,
                    },
                    reasoning="calendar_add_on_pattern",
                )
            list_name = re.sub(r"\s+list$", "", target, flags=re.IGNORECASE).strip()
            return MicroDecision(
                intent=Intent.LIST_ADD_ITEM,
                confidence=0.83,
                entities={
                    "item_text": title,
                    "list_name": list_name,
                },
                reasoning="list_add_on_pattern",
            )

        calendar_view_match = re.search(
            r"\b(show|view|get|display|whats|what is on|what's on)\b.*\bcalendar\b",
            lowered,
        )
        if calendar_view_match:
            window = "weekly" if re.search(r"\b(week|weekly)\b", lowered) else "daily"
            person_name = None
            person_match = re.search(r"\bfor\s+([a-z][a-z\s'-]+)$", cleaned, flags=re.IGNORECASE)
            if person_match:
                person_name = person_match.group(1).strip()
            else:
                poss_match = re.search(r"\b([a-z][a-z'-]+)'?s?\s+calendar\b", cleaned, flags=re.IGNORECASE)
                if poss_match:
                    owner = poss_match.group(1).strip()
                    if owner.lower() not in {"my", "the"}:
                        person_name = owner
            return MicroDecision(
                intent=Intent.CALENDAR_VIEW,
                confidence=0.9,
                entities={"window": window, "person_name": person_name},
                reasoning="calendar_view_pattern",
            )

        list_create_and_add_match = re.search(
            r"\b(?:create|make|start)\b\s+(?:a|an|my|the)?\s*(?P<list>.+?)\s+list\b.*\b(?:add|put)\b\s+"
            r"(?P<item>.+?)\s+to\s+(?:it|that(?:\s+list)?|the\s+list)\b",
            cleaned,
            flags=re.IGNORECASE,
        )
        if list_create_and_add_match:
            return MicroDecision(
                intent=Intent.CONVERSATIONAL,
                confidence=0.83,
                ambiguity_flags=["compound_list_create_add"],
                reasoning="compound_list_create_add_pattern",
            )

        list_create_match = re.match(
            r"^(?:create|make|start)\s+(?:a|an|my|the)?\s*(?P<list>.+?)\s+list$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if not list_create_match:
            list_create_match = re.match(
                r"^(?:create|make|start)\s+list\s+(?P<list>.+)$",
                cleaned,
                flags=re.IGNORECASE,
            )
        if not list_create_match:
            list_create_match = re.match(
                r"^(?:create|make|start)\s+(?:a|an)?\s*new\s+list(?:\s+called|\s+named)?\s+(?P<list>.+)$",
                cleaned,
                flags=re.IGNORECASE,
            )
        if list_create_match:
            list_name = list_create_match.group("list").strip()
            list_name = re.sub(r"^(?:called|named)\s+", "", list_name, flags=re.IGNORECASE).strip()
            list_name = re.sub(r"^list\s+(?:called|named)\s+", "", list_name, flags=re.IGNORECASE).strip()
            list_name = re.sub(r"^list\s+", "", list_name, flags=re.IGNORECASE).strip()
            list_name = re.sub(r"\s+list$", "", list_name, flags=re.IGNORECASE).strip()
            if list_name:
                return MicroDecision(
                    intent=Intent.LIST_CREATE_LIST,
                    confidence=0.9,
                    entities={"list_name": list_name},
                    reasoning="list_create_pattern",
                )

        list_show_match = re.match(
            r"^(?:show|get|display)(?:\s+me)?\s+(?P<target>.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if list_show_match and "calendar" not in lowered:
            target = list_show_match.group("target").strip()
            target = re.sub(r"^(?:my|the)\s+", "", target, flags=re.IGNORECASE).strip()
            target = re.sub(r"\s+list$", "", target, flags=re.IGNORECASE).strip()
            if target:
                return MicroDecision(
                    intent=Intent.LIST_GET_ITEMS,
                    confidence=0.87,
                    entities={"list_name": target},
                    reasoning="list_get_pattern",
                )

        list_question_match = re.match(
            r"^(?:what(?:'s|s| is)\s+(?:on|up))\s+(?P<target>.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if list_question_match and "calendar" not in lowered:
            target = list_question_match.group("target").strip()
            target = re.sub(r"^(?:my|the)\s+", "", target, flags=re.IGNORECASE).strip()
            target = re.sub(r"\s+list$", "", target, flags=re.IGNORECASE).strip()
            if target:
                return MicroDecision(
                    intent=Intent.LIST_GET_ITEMS,
                    confidence=0.86,
                    entities={"list_name": target},
                    reasoning="list_get_question_pattern",
                )

        list_delete_match = re.match(
            r"^(?:delete|remove|clear)\s+(?:the\s+)?(?P<target>.+?)\s+list$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if list_delete_match:
            target = list_delete_match.group("target").strip()
            if re.search(r"\bfrom\b", target, flags=re.IGNORECASE):
                target = ""
            target = re.sub(r"^(?:entire|whole)\s+", "", target, flags=re.IGNORECASE).strip()
            target = re.sub(r"^(?:my|the|our)\s+", "", target, flags=re.IGNORECASE).strip()
            target = re.sub(r"\s+list$", "", target, flags=re.IGNORECASE).strip()
            if target:
                return MicroDecision(
                    intent=Intent.LIST_DELETE_LIST,
                    confidence=0.9,
                    entities={"list_name": target},
                    reasoning="list_delete_pattern",
                )

        list_remove_item_match = re.match(
            r"^(?:remove|delete)\s+(?P<item>.+?)\s+from\s+(?:the\s+)?(?P<target>.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if list_remove_item_match:
            item_text = list_remove_item_match.group("item").strip()
            target = list_remove_item_match.group("target").strip()
            target = re.sub(r"^(?:my|the|our)\s+", "", target, flags=re.IGNORECASE).strip()
            target = re.sub(r"\s+list$", "", target, flags=re.IGNORECASE).strip()
            if item_text and target:
                return MicroDecision(
                    intent=Intent.LIST_REMOVE_ITEM,
                    confidence=0.88,
                    entities={"item_text": item_text, "list_name": target},
                    reasoning="list_remove_item_pattern",
                )

        list_mark_done_match = re.match(
            r"^(?:mark|check)\s+(?P<item>.+?)\s+(?:as\s+)?(?P<state>done|complete|completed|checked off|check off)\s+"
            r"(?:on|in|from)\s+(?:the\s+)?(?P<target>.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if list_mark_done_match:
            item_text = list_mark_done_match.group("item").strip()
            state = list_mark_done_match.group("state").strip().lower()
            target = list_mark_done_match.group("target").strip()
            target = re.sub(r"^(?:my|the|our)\s+", "", target, flags=re.IGNORECASE).strip()
            target = re.sub(r"\s+list$", "", target, flags=re.IGNORECASE).strip()
            completion_mode = "done" if state in {"done", "checked off", "check off"} else None
            if item_text and target:
                entities: dict[str, Any] = {"item_text": item_text, "list_name": target}
                if completion_mode:
                    entities["completion_mode"] = completion_mode
                return MicroDecision(
                    intent=Intent.LIST_MARK_ITEM_DONE,
                    confidence=0.86,
                    entities=entities,
                    reasoning="list_mark_done_pattern",
                )

        if re.search(r"\b(plan|help|explain|why|how|brainstorm|design)\b", lowered):
            return MicroDecision(
                intent=Intent.CONVERSATIONAL,
                confidence=0.82,
                reasoning="conversational_keyword",
            )

        return MicroDecision(
            intent=Intent.UNKNOWN,
            confidence=0.4,
            reasoning="fallback_unknown",
            ambiguity_flags=["unknown_intent"],
        )

    @staticmethod
    def _normalize_text(text: str) -> str:
        normalized = re.sub(r"\s+", " ", text).strip()
        normalized = re.sub(r"[.!?]+$", "", normalized)
        normalized = re.sub(r"^(?:let(?:'s|s)\s+)", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"^(?:(?:hi|hello|hey|yo)\s+)?jarvis[:,]?\s*", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"^(?:please\s+)+", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"^(?:can|could|would)\s+you\s+", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+please$", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+for\s+me$", "", normalized, flags=re.IGNORECASE)
        normalized = normalize_skill_anchor_spelling(normalized)
        return normalized.strip()

    @staticmethod
    def _apply_guardrails(text: str, decision: MicroDecision) -> MicroDecision:
        cleaned = MicroJarvis._normalize_text(text)
        compound_list_request = parse_list_create_and_add(text)
        if compound_list_request is not None:
            return MicroDecision(
                intent=Intent.CONVERSATIONAL,
                confidence=max(decision.confidence, 0.9),
                entities={
                    "list_name": compound_list_request.list_name,
                    "items": list(compound_list_request.items),
                },
                ambiguity_flags=["compound_list_create_add", "multi_action_request"],
                recommended_owner=SessionOwner.MAIN,
                reasoning="guardrail_compound_list_create_add",
            )
        calendar_mutation = MicroJarvis._calendar_mutation_heuristic(cleaned)
        if calendar_mutation is not None:
            if decision.intent != calendar_mutation.intent:
                calendar_mutation.reasoning = f"guardrail_calendar_mutation_after_{decision.reasoning}"
                return calendar_mutation
            merged_entities = dict(decision.entities)
            for key, value in calendar_mutation.entities.items():
                if key not in merged_entities or merged_entities.get(key) is None or merged_entities.get(key) == "":
                    merged_entities[key] = value
            decision.entities = merged_entities
            event_reference = str(merged_entities.get("event_reference") or "").strip()
            decision.ambiguity_flags = [
                flag
                for flag in decision.ambiguity_flags
                if str(flag).strip().casefold() != "deictic_event_reference"
            ]
            if MicroJarvis._is_deictic_event_reference(event_reference):
                decision.ambiguity_flags.append("deictic_event_reference")
        explicit_create_match = re.match(
            r"^(?:create|make|start)\s+(?:a|an|my|the)?\s*(?P<list>.+?)\s+list$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if not explicit_create_match:
            explicit_create_match = re.match(
                r"^(?:create|make|start)\s+list\s+(?P<list>.+)$",
                cleaned,
                flags=re.IGNORECASE,
            )
        if explicit_create_match:
            list_name = explicit_create_match.group("list").strip()
            list_name = re.sub(r"^(?:called|named)\s+", "", list_name, flags=re.IGNORECASE).strip()
            list_name = re.sub(r"\s+list$", "", list_name, flags=re.IGNORECASE).strip()
            if list_name and decision.intent != Intent.LIST_CREATE_LIST:
                decision.intent = Intent.LIST_CREATE_LIST
                decision.entities = {"list_name": list_name}
                decision.ambiguity_flags = [
                    flag
                    for flag in decision.ambiguity_flags
                    if str(flag).strip().lower() not in {"deictic", "deictic_list_reference"}
                ]
                decision.reasoning = "guardrail_explicit_list_create_phrase"
                decision.confidence = max(decision.confidence, 0.9)
                return decision
        elif decision.intent == Intent.LIST_CREATE_LIST:
            mentions_list = bool(re.search(r"\blist\b", cleaned, flags=re.IGNORECASE))
            create_hint = bool(re.search(r"\b(create|make|start|new)\b", cleaned, flags=re.IGNORECASE))
            if not (mentions_list and create_hint):
                decision.intent = Intent.UNKNOWN
                decision.entities = {}
                if "guardrail_suspect_list_create" not in decision.ambiguity_flags:
                    decision.ambiguity_flags.append("guardrail_suspect_list_create")
                decision.reasoning = "guardrail_rejected_non_explicit_list_create"
                decision.confidence = min(decision.confidence, 0.45)
                return decision

        lowered = text.lower()
        if decision.intent == Intent.LIST_ADD_ITEM and "calendar" in lowered:
            decision.intent = Intent.CALENDAR_ADD_EVENT
            item_text = str(decision.entities.get("item_text") or "").strip()
            decision.entities = {
                "event_title": item_text,
                "when_hint": None,
            }
            decision.ambiguity_flags.append("calendar_hint_overrode_list_intent")
            decision.reasoning = "guardrail_calendar_precedence"
            decision.confidence = max(decision.confidence, 0.85)
            return decision

        if decision.intent == Intent.LIST_ADD_ITEM:
            list_name = str(decision.entities.get("list_name") or "").strip()
            if MicroJarvis._is_deictic_list_reference(list_name):
                if "deictic_list_reference" not in decision.ambiguity_flags:
                    decision.ambiguity_flags.append("deictic_list_reference")
        if decision.intent in {Intent.LIST_DELETE_LIST, Intent.LIST_REMOVE_ITEM, Intent.LIST_MARK_ITEM_DONE}:
            list_name = str(decision.entities.get("list_name") or "").strip()
            if MicroJarvis._is_deictic_list_reference(list_name):
                if "deictic_list_reference" not in decision.ambiguity_flags:
                    decision.ambiguity_flags.append("deictic_list_reference")

        delete_list_match = re.match(
            r"^(?:delete|remove|clear)\s+(?:the\s+)?(?P<target>.+?)\s+list$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if delete_list_match and decision.intent != Intent.LIST_DELETE_LIST:
            list_name = delete_list_match.group("target").strip()
            if re.search(r"\bfrom\b", list_name, flags=re.IGNORECASE):
                list_name = ""
            list_name = re.sub(r"^(?:entire|whole)\s+", "", list_name, flags=re.IGNORECASE).strip()
            list_name = re.sub(r"^(?:my|the|our)\s+", "", list_name, flags=re.IGNORECASE).strip()
            list_name = re.sub(r"\s+list$", "", list_name, flags=re.IGNORECASE).strip()
            if list_name:
                decision.intent = Intent.LIST_DELETE_LIST
                decision.entities = {"list_name": list_name}
                decision.reasoning = "guardrail_explicit_list_delete_phrase"
                decision.confidence = max(decision.confidence, 0.9)
                return decision

        remove_item_match = re.match(
            r"^(?:remove|delete)\s+(?P<item>.+?)\s+from\s+(?:the\s+)?(?P<target>.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if remove_item_match and decision.intent == Intent.LIST_ADD_ITEM:
            item_text = remove_item_match.group("item").strip()
            target = remove_item_match.group("target").strip()
            target = re.sub(r"^(?:my|the|our)\s+", "", target, flags=re.IGNORECASE).strip()
            target = re.sub(r"\s+list$", "", target, flags=re.IGNORECASE).strip()
            if item_text and target:
                decision.intent = Intent.LIST_REMOVE_ITEM
                decision.entities = {"item_text": item_text, "list_name": target}
                decision.reasoning = "guardrail_remove_item_precedence"
                decision.confidence = max(decision.confidence, 0.88)
                return decision

        if decision.intent == Intent.HOME_SET_SWITCH:
            entities = dict(decision.entities)
            action_raw = str(entities.get("action") or "").strip().lower()
            switch_raw = str(entities.get("switch_name") or "").strip().lower()
            combined = " ".join(part for part in [action_raw, switch_raw, lowered] if part)

            all_lights_phrases = [
                "turn_off_all_lights",
                "all_lights_off",
                "off_all_lights",
                "turn_on_all_lights",
                "all_lights_on",
                "on_all_lights",
            ]
            if any(phrase in combined for phrase in all_lights_phrases) or re.search(
                r"\b(?:all|every)\s+(?:the\s+)?lights?\b",
                combined,
            ):
                if "off" in combined:
                    entities["action"] = "off"
                elif "on" in combined:
                    entities["action"] = "on"
                if str(entities.get("action") or "").strip().lower() in {"on", "off"}:
                    entities["switch_name"] = "all lights"
                    entities["scope"] = "all"
                    if "bulk_scope_requires_planning" not in decision.ambiguity_flags:
                        decision.ambiguity_flags.append("bulk_scope_requires_planning")
            elif action_raw not in {"", "on", "off"}:
                if "off" in action_raw:
                    entities["action"] = "off"
                elif "on" in action_raw:
                    entities["action"] = "on"

            if not str(entities.get("action") or "").strip():
                action_match = re.search(r"\b(on|off)\b", lowered, flags=re.IGNORECASE)
                if action_match:
                    entities["action"] = action_match.group(1).lower()

            if not str(entities.get("switch_name") or "").strip():
                switch_match = re.match(
                    r"^(?:turn|switch)\s+(?:on|off)\s+(?:the\s+)?(?P<switch>.+)$",
                    lowered,
                )
                if not switch_match:
                    switch_match = re.match(
                        r"^(?:turn|switch)\s+(?:the\s+)?(?P<switch>.+)\s+(?:on|off)$",
                        lowered,
                    )
                if switch_match:
                    entities["switch_name"] = switch_match.group("switch").strip()

            decision.entities = entities
        return decision

    @staticmethod
    def _email_heuristic(
        *,
        cleaned: str,
        context: dict[str, Any],
    ) -> MicroDecision | None:
        lowered = cleaned.casefold()
        reference_match = re.search(r"\bE(?P<number>\d{1,2})\b", cleaned, flags=re.IGNORECASE)
        reference = f"E{reference_match.group('number')}" if reference_match else None
        references = tuple(
            dict.fromkeys(
                f"E{value}"
                for value in re.findall(r"\bE(\d{1,2})\b", cleaned, flags=re.IGNORECASE)
            )
        )
        has_context = MicroJarvis._has_email_context(context)
        category_signal = bool(
            re.search(
                r"\b(?:bills?|work|sports|projects|community|needs review|spam)\b",
                lowered,
            )
        )
        source_signal = bool(
            re.search(r"\b(?:work|community|county(?: account)?)\b", lowered)
        )
        email_signal = bool(re.search(r"\b(?:e-?mails?|inboxes?|gmail)\b", lowered))
        focused_contextual_signal = has_context and bool(
            re.search(r"\b(?:it|that|this|second one|first one|third one|tell me more|thread)\b", lowered)
        )
        collection_contextual_signal = has_context and bool(
            re.search(
                r"\b(?:anything|everything|all|today|recent|latest|new|unseen|ones?|summarize|"
                r"my addresses?|my accounts?|our addresses?|our accounts?)\b",
                lowered,
            )
        )
        contextual_signal = focused_contextual_signal or collection_contextual_signal
        implicit_search = (category_signal or source_signal) and bool(
            re.search(r"\b(?:show|latest|recent|anything|arrived|came in|from|about|needs? attention)\b", lowered)
        )
        spam_signal = bool(re.search(r"\b(?:spam|junk)\b", lowered))
        disposition_signal = has_context and bool(
            re.search(
                r"\b(?:needs?\s+(?:a\s+)?reply|reply needed|mark\s+as\s+read|"
                r"read\s+and\s+(?:complete|done)|complete|dismiss|snooze)\b",
                lowered,
            )
        )
        if not (
            reference
            or email_signal
            or contextual_signal
            or implicit_search
            or disposition_signal
            or (spam_signal and has_context)
        ):
            return None

        entities: dict[str, Any] = {"query": cleaned}
        if len(references) > 1:
            entities["references"] = list(references[:5])
        elif reference:
            entities["reference"] = reference
        elif focused_contextual_signal:
            entities["reference"] = "that"
        if has_context and re.search(r"\b(?:all of (?:those|them)|those all|them all)\b", lowered):
            entities.pop("reference", None)
            entities["reference_scope"] = "all_current"

        positive_spam_request = spam_signal and bool(
            re.search(
                r"(?:\b(?:mark|move|send|put|flag|call)\b.*\b(?:spam|junk)\b|"
                r"\b(?:is|are|looks? like|seems? like)\s+(?:spam|junk)\b)",
                lowered,
            )
        )
        negative_spam_request = bool(
            re.search(r"\b(?:not|isn['’]?t|aren['’]?t)\s+(?:spam|junk)\b", lowered)
        )
        if positive_spam_request and not negative_spam_request:
            return MicroDecision(
                intent=Intent.EMAIL_MARK_SPAM,
                confidence=0.99,
                entities=entities,
                ambiguity_flags=["manual_provider_write"],
                reasoning="explicit_email_spam_pattern",
            )

        needs_reply_request = bool(
            re.search(
                r"\b(?:mark|flag|set|put)\b.*\b(?:needs?\s+(?:a\s+)?reply|reply needed|needs? response)\b",
                lowered,
            )
        )
        if needs_reply_request:
            return MicroDecision(
                intent=Intent.EMAIL_MARK_NEEDS_REPLY,
                confidence=0.98,
                entities=entities,
                reasoning="explicit_email_needs_reply_pattern",
            )

        complete_request = bool(
            re.search(
                r"(?:\b(?:mark|make|set)\b.*\bread\b.*\b(?:complete|done|handled)\b|"
                r"\b(?:mark|make|set)\b.*\bread\b.*\bmove\b.*\bfolders?\b|"
                r"\b(?:complete|finish)\b.*\b(?:e\d{1,2}|email|this|that|those|them)\b|"
                r"\b(?:e\d{1,2}|this|that)\b.*\b(?:is|as)\s+(?:complete|done|handled)\b)",
                lowered,
            )
        )
        negative_complete_request = bool(
            re.search(r"\b(?:do not|don['’]?t|not)\b.*\b(?:complete|done|handled|read)\b", lowered)
        )
        if complete_request and not negative_complete_request:
            return MicroDecision(
                intent=Intent.EMAIL_MARK_COMPLETE,
                confidence=0.99,
                entities=entities,
                ambiguity_flags=["manual_provider_write"],
                reasoning="explicit_email_complete_pattern",
            )

        if collection_contextual_signal and not reference:
            return MicroDecision(
                intent=Intent.EMAIL_LIST_RECENT,
                confidence=0.94,
                entities=entities,
                reasoning="email_collection_followup_pattern",
            )

        if reference and re.search(r"\b(?:put|add|turn|make)\b.*\b(?:list|to-?do)\b", lowered):
            return MicroDecision(
                intent=Intent.EMAIL_PROMOTE_TO_LIST,
                confidence=0.96,
                entities=entities,
                reasoning="email_reference_promote_to_list_pattern",
            )
        if reference and re.search(r"\b(?:put|add|make|turn)\b.*\bcalendar\b", lowered):
            return MicroDecision(
                intent=Intent.EMAIL_PROMOTE_TO_CALENDAR,
                confidence=0.96,
                entities=entities,
                reasoning="email_reference_promote_to_calendar_pattern",
            )
        if reference and re.search(r"\b(?:wave|ticket)\b", lowered):
            return MicroDecision(
                intent=Intent.EMAIL_PROMOTE_TO_WAVE,
                confidence=0.96,
                entities=entities,
                reasoning="email_reference_promote_to_wave_pattern",
            )
        if reference and re.search(r"\btask\b", lowered):
            return MicroDecision(
                intent=Intent.EMAIL_PROMOTE_TO_TASK,
                confidence=0.96,
                entities=entities,
                reasoning="email_reference_promote_to_task_pattern",
            )

        category_match = re.search(
            r"\b(?P<category>bills?|work|sports|projects|community|needs review|spam)\b",
            lowered,
        )
        if category_match:
            entities["category"] = category_match.group("category")
        if re.search(r"\b(?:belongs? in|belongs? to|categor(?:y|ize)|classif(?:y|ied)|move)\b", lowered) and (
            reference or has_context
        ):
            return MicroDecision(
                intent=Intent.EMAIL_CORRECT_CATEGORY,
                confidence=0.94,
                entities=entities,
                reasoning="email_category_correction_pattern",
            )
        if re.search(r"\b(?:mark|set)\b.*\breviewed\b", lowered):
            return MicroDecision(
                intent=Intent.EMAIL_MARK_REVIEWED,
                confidence=0.94,
                entities=entities,
                reasoning="email_mark_reviewed_pattern",
            )
        if re.search(r"\bdismiss\b", lowered):
            return MicroDecision(
                intent=Intent.EMAIL_DISMISS,
                confidence=0.94,
                entities=entities,
                reasoning="email_dismiss_pattern",
            )
        snooze_match = re.search(r"\bsnooze\b(?:\s+(?:it|that|this|E\d+))?(?:\s+(?:until|for)\s+(.+))?", cleaned, re.I)
        if snooze_match:
            if snooze_match.group(1):
                entities["until"] = snooze_match.group(1).strip()
            return MicroDecision(
                intent=Intent.EMAIL_SNOOZE,
                confidence=0.94,
                entities=entities,
                reasoning="email_snooze_pattern",
            )
        if re.search(r"\b(?:status|working|healthy|synced|sync status)\b", lowered) and email_signal:
            return MicroDecision(
                intent=Intent.EMAIL_STATUS,
                confidence=0.94,
                entities=entities,
                reasoning="email_status_pattern",
            )
        if re.search(r"\bthread\b", lowered) and (reference or contextual_signal or category_signal):
            return MicroDecision(
                intent=Intent.EMAIL_GET_THREAD,
                confidence=0.93,
                entities=entities,
                reasoning="email_thread_pattern",
            )
        if reference or contextual_signal:
            intent = Intent.EMAIL_SUMMARIZE if re.search(r"\bsummar", lowered) else Intent.EMAIL_DISCUSS
            return MicroDecision(
                intent=intent,
                confidence=0.93,
                entities=entities,
                reasoning="email_reference_discussion_pattern",
            )
        if re.search(r"\b(?:from|about|search|find|show|latest|recent|anything)\b", lowered) and (
            category_signal or source_signal or re.search(r"\bfrom\s+", lowered)
        ):
            return MicroDecision(
                intent=Intent.EMAIL_SEARCH,
                confidence=0.91,
                entities=entities,
                reasoning="email_search_pattern",
            )
        return MicroDecision(
            intent=Intent.EMAIL_LIST_RECENT,
            confidence=0.9,
            entities=entities,
            reasoning="email_recent_pattern",
        )

    @staticmethod
    def _has_email_context(context: dict[str, Any]) -> bool:
        hints = context.get("entity_hints")
        if not isinstance(hints, list):
            working = context.get("working_context")
            hints = working.get("entity_hints") if isinstance(working, dict) else []
        if any(
            isinstance(item, dict)
            and str(item.get("domain") or "").strip().casefold() == "email"
            and str(item.get("entity_type") or "").strip().casefold() == "message"
            for item in (hints or [])
        ):
            return True
        active = context.get("active_skill_context")
        if not isinstance(active, dict):
            working = context.get("working_context")
            active = working.get("active_skill_context") if isinstance(working, dict) else None
        return (
            isinstance(active, dict)
            and str(active.get("skill_id") or "").strip().casefold() == "skill.email.agent"
            and str(active.get("context_kind") or "").strip().casefold() == "email_reference_set"
        )

    @staticmethod
    def _calendar_target_details(target: str) -> tuple[str | None, str | None]:
        person_name = None
        person_match = re.match(r"(?P<owner>.+?)'?s?\s+calendar(?:\b|$)", target, flags=re.IGNORECASE)
        if person_match:
            owner = person_match.group("owner").strip()
            if owner.lower() not in {"my", "the", "our"}:
                person_name = owner

        when_hint = None
        calendar_match = re.search(r"\bcalendar\b", target, flags=re.IGNORECASE)
        if calendar_match:
            suffix = target[calendar_match.end() :].strip()
            suffix = re.sub(r"^(?:for|on|at|in)\s+", "", suffix, flags=re.IGNORECASE).strip()
            if suffix:
                when_hint = suffix
        return person_name, when_hint

    @staticmethod
    def _calendar_mutation_heuristic(cleaned: str) -> MicroDecision | None:
        all_day_match = re.match(
            r"^(?:make|change|convert)\s+(?:the\s+)?(?P<reference>.+?)\s+"
            r"(?:into\s+)?(?:an?\s+)?all[- ]day(?:\s+event)?(?:\s+actually)?$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if not all_day_match:
            all_day_match = re.match(
                r"^(?:make|change|convert)\s+(?:the\s+)?(?P<reference>.+?)\s+(?:event\s+)?"
                r"(?:to\s+)?all[- ]day(?:\s+actually)?$",
                cleaned,
                flags=re.IGNORECASE,
            )
        if all_day_match:
            reference = all_day_match.group("reference").strip()
            flags = ["deictic_event_reference"] if MicroJarvis._is_deictic_event_reference(reference) else []
            return MicroDecision(
                intent=Intent.CALENDAR_UPDATE_EVENT,
                confidence=0.94,
                entities={"event_reference": reference, "all_day": True},
                ambiguity_flags=flags,
                reasoning="calendar_update_all_day_pattern",
            )

        move_match = re.match(
            r"^(?:move|reschedule)\s+(?:the\s+)?(?P<reference>.+?)\s+(?:to|for)\s+(?P<when>.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if move_match:
            reference = move_match.group("reference").strip()
            flags = ["deictic_event_reference"] if MicroJarvis._is_deictic_event_reference(reference) else []
            return MicroDecision(
                intent=Intent.CALENDAR_UPDATE_EVENT,
                confidence=0.9,
                entities={"event_reference": reference, "new_when_hint": move_match.group("when").strip()},
                ambiguity_flags=flags,
                reasoning="calendar_update_move_pattern",
            )

        delete_match = re.match(
            r"^(?:delete|remove)\s+(?:the\s+)?(?P<reference>.+?)\s+(?:calendar\s+event|event)"
            r"(?:\s+from\s+(?:my|the|our)\s+calendar)?$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if not delete_match:
            delete_match = re.match(
                r"^cancel\s+(?:the\s+)?(?P<reference>.+?)(?:\s+(?:calendar\s+)?event)?$",
                cleaned,
                flags=re.IGNORECASE,
            )
        if delete_match:
            reference = delete_match.group("reference").strip()
            flags = ["deictic_event_reference"] if MicroJarvis._is_deictic_event_reference(reference) else []
            return MicroDecision(
                intent=Intent.CALENDAR_DELETE_EVENT,
                confidence=0.91,
                entities={"event_reference": reference},
                ambiguity_flags=flags,
                reasoning="calendar_delete_pattern",
            )
        return None

    def _recommended_owner(self, decision: MicroDecision) -> SessionOwner:
        if decision.intent in {Intent.SYSTEM_WAKE, Intent.SYSTEM_SLEEP}:
            return SessionOwner.SYSTEM
        if decision.intent in {
            Intent.CALENDAR_ADD_EVENT,
            Intent.CALENDAR_UPDATE_EVENT,
            Intent.CALENDAR_DELETE_EVENT,
            Intent.LIST_CREATE_LIST,
            Intent.LIST_DELETE_LIST,
            Intent.LIST_REMOVE_ITEM,
            Intent.LIST_MARK_ITEM_DONE,
        }:
            # Calendar writes and list-mutating actions are main-only so Main can handle context/clarification.
            return SessionOwner.MAIN
        if decision.intent in FAST_COMMAND_INTENTS:
            if decision.confidence < self._fast_confidence_threshold:
                return SessionOwner.MAIN
            if self._missing_required_fields(decision.intent, decision.entities):
                return SessionOwner.MAIN
            if self._has_blocking_ambiguity(decision):
                return SessionOwner.MAIN
            return SessionOwner.MICRO
        return SessionOwner.MAIN

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
    def _is_deictic_list_reference(value: str) -> bool:
        normalized = re.sub(r"[^a-z0-9\s_-]+", "", value.strip().lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        normalized = re.sub(r"^(?:my|the|our)\s+", "", normalized)
        normalized = re.sub(r"\s+list$", "", normalized).strip()
        return normalized in {"it", "that", "this", "same", "same list", "that list", "this list"}

    @staticmethod
    def _is_deictic_event_reference(value: str) -> bool:
        normalized = re.sub(r"[^a-z0-9\s_-]+", "", value.strip().lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        normalized = re.sub(r"^(?:my|the|our)\s+", "", normalized)
        normalized = re.sub(r"\s+(?:calendar\s+)?event$", "", normalized).strip()
        return normalized in {"it", "that", "this", "same", "same event", "that event", "this event"}
