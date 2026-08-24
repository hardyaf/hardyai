from __future__ import annotations

import re
from typing import Any, Protocol, TYPE_CHECKING

from app.core.main_repair_contract import normalize_repair_payload
from app.core.main_turn_contract import normalize_main_turn_decision
from app.core.text_normalization import normalize_skill_anchor_spelling
from app.skills.domains.lists.planning import MAX_COMPOUND_LIST_ITEMS, parse_list_create_and_add
from app.skills.patterns import extract_all_lights_action

if TYPE_CHECKING:
    from app.research.service import WebResearchService


class MainRepairBackend(Protocol):
    def repair_action(self, text: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Optional model-backed repair inference."""


class NullMainRepairBackend:
    def repair_action(self, text: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return None


class MainConversationBackend(Protocol):
    def decide_turn(self, text: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Choose conversation, bound clarification, or executable action."""

    def respond(self, text: str, context: dict[str, Any] | None = None) -> str | None:
        """Optional model-backed conversational response."""


class NullMainConversationBackend:
    def decide_turn(self, text: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return None

    def respond(self, text: str, context: dict[str, Any] | None = None) -> str | None:
        return None


class MainJarvis:
    def __init__(
        self,
        repair_backend: MainRepairBackend | None = None,
        conversation_backend: MainConversationBackend | None = None,
        research_service: "WebResearchService | None" = None,
    ) -> None:
        self._repair_backend = repair_backend or NullMainRepairBackend()
        self._conversation_backend = conversation_backend or NullMainConversationBackend()
        self._research_service = research_service

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
    def _extract_all_lights_action(text: str) -> str | None:
        return extract_all_lights_action(text)

    @staticmethod
    def _working_context(context: dict[str, Any]) -> dict[str, Any]:
        raw = context.get("working_context")
        if isinstance(raw, dict):
            return raw
        return {}

    @staticmethod
    def _entity_hints(context: dict[str, Any]) -> list[dict[str, Any]]:
        direct = context.get("entity_hints")
        if isinstance(direct, list):
            return [item for item in direct if isinstance(item, dict)]
        working_context = MainJarvis._working_context(context)
        nested = working_context.get("entity_hints")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_last_list_name(context: dict[str, Any]) -> str | None:
        direct = str(context.get("last_list_name") or "").strip()
        if direct:
            return direct
        for entity in MainJarvis._entity_hints(context):
            domain = str(entity.get("domain") or "").strip().lower()
            entity_type = str(entity.get("entity_type") or "").strip().lower()
            if domain != "lists" or entity_type != "list":
                continue
            display_name = str(entity.get("display_name") or "").strip()
            if display_name:
                return display_name
        return None

    @staticmethod
    def _extract_switch_names(context: dict[str, Any]) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        def _add_name(candidate: Any) -> None:
            text = str(candidate or "").strip()
            if not text:
                return
            lowered = text.lower()
            if lowered in seen:
                return
            seen.add(lowered)
            names.append(text)

        def _consume_entries(entries: Any) -> None:
            if not isinstance(entries, list):
                return
            for entry in entries:
                if isinstance(entry, dict):
                    _add_name(entry.get("name"))
                else:
                    _add_name(entry)

        _consume_entries(context.get("available_switches"))
        working_context = MainJarvis._working_context(context)
        channel_runtime = working_context.get("channel_runtime")
        if isinstance(channel_runtime, dict):
            _consume_entries(channel_runtime.get("available_switches"))

        if names:
            return names
        for entity in MainJarvis._entity_hints(context):
            domain = str(entity.get("domain") or "").strip().lower()
            entity_type = str(entity.get("entity_type") or "").strip().lower()
            if domain == "home" and entity_type == "switch":
                _add_name(entity.get("display_name"))
        return names

    @staticmethod
    def _extract_list_create_and_add_parts(text: str) -> tuple[str, list[str]] | None:
        parsed = parse_list_create_and_add(text)
        if parsed is None:
            return None
        return parsed.list_name, list(parsed.items)

    @staticmethod
    def _extract_list_create_only(text: str) -> str | None:
        match = re.match(
            r"^(?:create|make|start)\s+(?:a|an|my|the)?\s*(?P<list>.+?)\s+list$",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            match = re.match(
                r"^(?:create|make|start)\s+list\s+(?P<list>.+)$",
                text,
                flags=re.IGNORECASE,
            )
        if not match:
            return None
        list_name = match.group("list").strip()
        list_name = re.sub(r"^(?:called|named)\s+", "", list_name, flags=re.IGNORECASE).strip()
        list_name = re.sub(r"\s+list$", "", list_name, flags=re.IGNORECASE).strip()
        return list_name or None

    @staticmethod
    def _extract_list_add_only(text: str, *, last_list_name: str | None) -> tuple[str, str] | None:
        match = re.match(
            r"^(?:add|put)\s+(?P<item>.+?)\s+(?:to|on)\s+(?P<target>.+)$",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        item_text = match.group("item").strip()
        target = match.group("target").strip()
        target = re.sub(r"^(?:my|the|our)\s+", "", target, flags=re.IGNORECASE).strip()
        target = re.sub(r"\s+list$", "", target, flags=re.IGNORECASE).strip()
        if target.lower() in {"it", "that", "this", "that list", "this list", "same list", "same"}:
            target = (last_list_name or "").strip()
        if not item_text or not target:
            return None
        return item_text, target

    @staticmethod
    def _extract_list_delete_only(text: str, *, last_list_name: str | None) -> str | None:
        match = re.match(
            r"^(?:delete|remove|clear)\s+(?:the\s+)?(?P<target>.+?)\s+list$",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        target = match.group("target").strip()
        if re.search(r"\bfrom\b", target, flags=re.IGNORECASE):
            return None
        target = re.sub(r"^(?:entire|whole)\s+", "", target, flags=re.IGNORECASE).strip()
        target = re.sub(r"^(?:my|the|our)\s+", "", target, flags=re.IGNORECASE).strip()
        target = re.sub(r"\s+list$", "", target, flags=re.IGNORECASE).strip()
        if target.lower() in {"it", "that", "this", "that list", "this list", "same list", "same"}:
            target = (last_list_name or "").strip()
        return target or None

    @staticmethod
    def _extract_list_remove_item_only(text: str, *, last_list_name: str | None) -> tuple[str, str] | None:
        match = re.match(
            r"^(?:remove|delete)\s+(?P<item>.+?)\s+from\s+(?:the\s+)?(?P<target>.+)$",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        item_text = match.group("item").strip()
        target = match.group("target").strip()
        target = re.sub(r"^(?:my|the|our)\s+", "", target, flags=re.IGNORECASE).strip()
        target = re.sub(r"\s+list$", "", target, flags=re.IGNORECASE).strip()
        if target.lower() in {"it", "that", "this", "that list", "this list", "same list", "same"}:
            target = (last_list_name or "").strip()
        if not item_text or not target:
            return None
        return item_text, target

    @staticmethod
    def _extract_list_mark_done_only(text: str, *, last_list_name: str | None) -> tuple[str, str, str | None] | None:
        match = re.match(
            r"^(?:mark|check)\s+(?P<item>.+?)\s+(?:as\s+)?(?P<state>done|complete|completed|checked off|check off)\s+"
            r"(?:on|in|from)\s+(?:the\s+)?(?P<target>.+)$",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        item_text = match.group("item").strip()
        state = match.group("state").strip().lower()
        target = match.group("target").strip()
        target = re.sub(r"^(?:my|the|our)\s+", "", target, flags=re.IGNORECASE).strip()
        target = re.sub(r"\s+list$", "", target, flags=re.IGNORECASE).strip()
        if target.lower() in {"it", "that", "this", "that list", "this list", "same list", "same"}:
            target = (last_list_name or "").strip()
        if not item_text or not target:
            return None
        completion_mode = "done" if state in {"done", "checked off", "check off"} else None
        return item_text, target, completion_mode

    def repair_action(self, text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}

        backend_raw = self._repair_backend.repair_action(text=text, context=context)
        backend_payload = normalize_repair_payload(backend_raw)
        if backend_payload is not None:
            backend_payload["source"] = backend_payload.get("source") or "backend"
            return backend_payload

        return {
            "status": "not_actionable",
            "intent": None,
            "confidence": 0.0,
            "reasoning": "main_repair_model_unavailable_or_invalid",
            "entities": {},
            "missing_fields": [],
            "message": (
                "I could not confidently map that request from the model output. "
                "Please restate it with one direct action and key details."
            ),
            "question": None,
            "source": "unavailable",
        }

    def respond(self, text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}
        intent = str(context.get("micro_intent") or "unknown")
        all_lights_action = self._extract_all_lights_action(text)
        switch_names = self._extract_switch_names(context)
        last_list_name = str(self._extract_last_list_name(context) or "").strip()
        cleaned = self._normalize_text(text)
        conversational_intent = intent in {"unknown", "conversation.general", "conversational"}
        list_create_and_add = self._extract_list_create_and_add_parts(cleaned) if conversational_intent else None
        list_create_only = self._extract_list_create_only(cleaned) if conversational_intent else None
        list_add_only = (
            self._extract_list_add_only(cleaned, last_list_name=last_list_name)
            if conversational_intent
            else None
        )
        list_delete_only = (
            self._extract_list_delete_only(cleaned, last_list_name=last_list_name)
            if conversational_intent
            else None
        )
        list_remove_only = (
            self._extract_list_remove_item_only(cleaned, last_list_name=last_list_name)
            if conversational_intent
            else None
        )
        list_mark_done_only = (
            self._extract_list_mark_done_only(cleaned, last_list_name=last_list_name)
            if conversational_intent
            else None
        )

        if all_lights_action and switch_names:
            commands = [
                {
                    "command_text": f"turn {switch_name} {all_lights_action}",
                    "target": switch_name,
                }
                for switch_name in sorted(switch_names)
            ]
            return {
                "status": "planned",
                "message": f"I can turn {all_lights_action} all lights now.",
                "plan": {
                    "plan_type": "home.bulk_set",
                    "scope": "all_lights",
                    "action": all_lights_action,
                    "confidence": 0.87,
                    "commands": commands,
                },
            }

        if list_create_and_add is not None:
            list_name, items = list_create_and_add
            if len(items) > MAX_COMPOUND_LIST_ITEMS:
                return {
                    "status": "needs_clarification",
                    "message": (
                        f"I found {len(items)} items for `{list_name}`. "
                        f"Please split that into groups of {MAX_COMPOUND_LIST_ITEMS} so I do not partially execute the list."
                    ),
                    "question": f"Which {MAX_COMPOUND_LIST_ITEMS} items should I add first?",
                    "missing_fields": ["bounded_item_selection"],
                    "entities": {"list_name": list_name, "items": items},
                }
            commands = [
                {
                    "command_text": f"create {list_name} list",
                    "target": list_name,
                    "intent": "lists.create_list",
                    "entities": {"list_name": list_name},
                    "list_name": list_name,
                    "confidence": 0.94,
                },
                *[
                    {
                        "command_text": f"add {item_text} to {list_name}",
                        "target": list_name,
                        "intent": "lists.add_item",
                        "entities": {"list_name": list_name, "item_text": item_text},
                        "list_name": list_name,
                        "item_text": item_text,
                        "confidence": 0.94,
                    }
                    for item_text in items
                ],
            ]
            return {
                "status": "planned",
                "message": f"I can create `{list_name}` and add {len(items)} item(s) to it.",
                "success_message": f"Created `{list_name}` and added {len(items)} item(s).",
                "plan": {
                    "plan_type": "list.create_and_add",
                    "scope": "single_list",
                    "action": "create_and_add",
                    "confidence": 0.94,
                    "commands": commands,
                },
            }

        if list_create_only is not None:
            list_name = list_create_only
            return {
                "status": "planned",
                "message": f"I can create `{list_name}` now.",
                "plan": {
                    "plan_type": "list.create",
                    "scope": "single_list",
                    "action": "create",
                    "confidence": 0.84,
                    "commands": [
                        {
                            "command_text": f"create {list_name} list",
                            "target": list_name,
                        }
                    ],
                },
            }

        if list_add_only is not None:
            item_text, list_name = list_add_only
            return {
                "status": "planned",
                "message": f"I can add `{item_text}` to `{list_name}`.",
                "plan": {
                    "plan_type": "list.add",
                    "scope": "single_list",
                    "action": "add_item",
                    "confidence": 0.82,
                    "commands": [
                        {
                            "command_text": f"add {item_text} to {list_name}",
                            "target": list_name,
                        }
                    ],
                },
            }

        if list_delete_only is not None:
            list_name = list_delete_only
            return {
                "status": "planned",
                "message": f"I can delete `{list_name}`.",
                "plan": {
                    "plan_type": "list.delete",
                    "scope": "single_list",
                    "action": "delete_list",
                    "confidence": 0.8,
                    "commands": [
                        {
                            "command_text": f"delete {list_name} list",
                            "target": list_name,
                        }
                    ],
                },
            }

        if list_remove_only is not None:
            item_text, list_name = list_remove_only
            return {
                "status": "planned",
                "message": f"I can remove `{item_text}` from `{list_name}`.",
                "plan": {
                    "plan_type": "list.remove_item",
                    "scope": "single_list",
                    "action": "remove_item",
                    "confidence": 0.8,
                    "commands": [
                        {
                            "command_text": f"remove {item_text} from {list_name}",
                            "target": list_name,
                        }
                    ],
                },
            }

        if list_mark_done_only is not None:
            item_text, list_name, completion_mode = list_mark_done_only
            command_text = f"mark {item_text} complete on {list_name}"
            if completion_mode == "done":
                command_text = f"mark {item_text} as done on {list_name}"
            return {
                "status": "planned",
                "message": f"I can mark `{item_text}` complete on `{list_name}`.",
                "plan": {
                    "plan_type": "list.mark_item_done",
                    "scope": "single_list",
                    "action": "mark_item_done",
                    "confidence": 0.79,
                    "commands": [
                        {
                            "command_text": command_text,
                            "target": list_name,
                        }
                    ],
                },
            }

        if intent in {"unknown", "conversation.general"}:
            research_outcome = (
                self._research_service.research_if_needed(text=text, context=context)
                if self._research_service is not None
                else None
            )
            if research_outcome is not None and research_outcome.status == "needs_clarification":
                return {
                    "status": "needs_clarification",
                    "message": "I can research that, but I need a more specific subject first.",
                    "question": "What exactly should I look up?",
                    "missing_fields": ["research_subject"],
                    "research": research_outcome.public_payload(),
                }
            model_context = dict(context)
            if research_outcome is not None and research_outcome.status == "ok":
                model_context["web_research"] = research_outcome.prompt_payload()
            decide_turn = getattr(self._conversation_backend, "decide_turn", None)
            if callable(decide_turn):
                raw_turn_decision = decide_turn(text=text, context=model_context)
                turn_decision = normalize_main_turn_decision(raw_turn_decision)
                if turn_decision is not None:
                    if turn_decision["mode"] != "conversation":
                        return {
                            "status": "main_turn_decision",
                            "turn_decision": turn_decision,
                            **(
                                {"research": research_outcome.public_payload()}
                                if research_outcome is not None
                                else {}
                            ),
                        }
                    message = str(turn_decision.get("message") or "").strip()
                    conversation_source = "model"
                    if research_outcome is not None and research_outcome.status == "ok":
                        message = self._research_service.ground_answer(
                            answer=message,
                            outcome=research_outcome,
                        )
                        conversation_source = "model_with_web_research"
                    elif research_outcome is not None and research_outcome.required:
                        message = (
                            f"{message}\n\n"
                            "I could not verify this with web research right now, so treat it as a best-effort answer."
                        )
                    return {
                        "status": "conversation",
                        "message": message,
                        "conversation_source": conversation_source,
                        "turn_decision": turn_decision,
                        **(
                            {"research": research_outcome.public_payload()}
                            if research_outcome is not None
                            else {}
                        ),
                    }
                return {
                    "status": "conversation",
                    "message": (
                        "I could not safely decide whether to answer or run an action. "
                        "Please try that again in a moment."
                    ),
                    "conversation_source": "decision_unavailable",
                    **(
                        {"research": research_outcome.public_payload()}
                        if research_outcome is not None
                        else {}
                    ),
                }

            # Compatibility path for non-production/test backends that only
            # implement the original natural-language conversation interface.
            model_reply = self._conversation_backend.respond(text=text, context=model_context)
            if isinstance(model_reply, str) and model_reply.strip():
                message = model_reply.strip()
                conversation_source = "model"
                if research_outcome is not None and research_outcome.status == "ok":
                    message = self._research_service.ground_answer(
                        answer=message,
                        outcome=research_outcome,
                    )
                    conversation_source = "model_with_web_research"
                elif research_outcome is not None and research_outcome.required:
                    message = (
                        f"{message}\n\n"
                        "I could not verify this with web research right now, so treat it as a best-effort answer."
                    )
                return {
                    "status": "conversation",
                    "message": message,
                    "conversation_source": conversation_source,
                    **(
                        {"research": research_outcome.public_payload()}
                        if research_outcome is not None
                        else {}
                    ),
                }
            return {
                "status": "conversation",
                "message": (
                    "I do not have a reliable answer right now because the main conversation model did not respond. "
                    "Please try again in a moment."
                ),
                "conversation_source": "unavailable",
                **(
                    {"research": research_outcome.public_payload()}
                    if research_outcome is not None
                    else {}
                ),
            }

        return {
            "status": "not_actionable",
            "message": (
                "I could not safely execute that action from this phrasing yet. "
                "Please restate it as a direct command or answer the missing detail."
            ),
            "followup_hint": (
                "Examples: `add milk to groceries`, `turn kitchen light on`, "
                "`what's on my calendar today`."
            ),
            "reasoning": "main_action_fallback_not_actionable",
            "inferred_intent": intent,
        }
