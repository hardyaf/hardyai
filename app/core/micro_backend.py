from __future__ import annotations

import json
import re
from typing import Any, TYPE_CHECKING

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.core.ollama_observability import OllamaCallObserver, OllamaMetricsCallback
from app.core.types import Intent

if TYPE_CHECKING:
    from app.skills.registry_service import SkillRegistryService

ALLOWED_INTENTS = {intent.value for intent in Intent}


class BackendDecisionPayload(BaseModel):
    intent: str
    confidence: float = Field(ge=0.0, le=1.0)
    entities: dict[str, Any] = Field(default_factory=dict)
    ambiguity_flags: list[str] = Field(default_factory=list)
    reasoning: str = "model_backend"

    @field_validator("intent")
    @classmethod
    def validate_intent(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_INTENTS:
            raise ValueError("intent is not an allowed value")
        return normalized

    def to_decision_payload(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": float(self.confidence),
            "entities": self.entities,
            "ambiguity_flags": [str(item) for item in self.ambiguity_flags],
            "reasoning": self.reasoning,
        }


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    trimmed = text.strip()
    if not trimmed:
        return None
    try:
        loaded = json.loads(trimmed)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        pass

    start = trimmed.find("{")
    end = trimmed.rfind("}")
    if start < 0 or end <= start:
        return None

    maybe_json = trimmed[start : end + 1]
    maybe_json = re.sub(r"```(?:json)?", "", maybe_json, flags=re.IGNORECASE).strip()
    try:
        loaded = json.loads(maybe_json)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        return None


def parse_backend_payload(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        validated = BackendDecisionPayload.model_validate(raw)
    except ValidationError:
        return None
    return validated.to_decision_payload()


class OllamaMicroInferenceBackend:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = 3.0,
        skill_registry: "SkillRegistryService | None" = None,
        num_ctx: int = 4096,
        num_predict: int = 256,
        metrics_callback: OllamaMetricsCallback | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._skill_registry = skill_registry
        self._observer = OllamaCallObserver(
            lane="micro",
            model=model,
            num_ctx=num_ctx,
            num_predict=num_predict,
            metrics_callback=metrics_callback,
        )

    def classify(self, text: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        prompt = self._build_prompt(text=text, context=context or {})
        try:
            response = httpx.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                    "options": self._observer.options(temperature=0.0),
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            self._observer.record(prompt=prompt, outcome="error", error_type=type(exc).__name__)
            return None

        self._observer.record(prompt=prompt, response_payload=data, outcome="success")
        raw_text = str(data.get("response") or "")
        loaded = _extract_first_json_object(raw_text)
        return parse_backend_payload(loaded)

    def status(self) -> dict[str, Any]:
        return self._observer.status()

    @staticmethod
    def _collect_intent_hints(context: dict[str, Any]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        raw_hints = context.get("runtime_skill_intents")
        if isinstance(raw_hints, list):
            for item in raw_hints:
                hint = str(item or "").strip().lower()
                if hint and hint not in seen:
                    seen.add(hint)
                    ordered.append(hint)
        for key in ("pending_intent", "micro_intent", "intent_hint"):
            hint = str(context.get(key) or "").strip().lower()
            if hint and hint not in seen:
                seen.add(hint)
                ordered.append(hint)
        return ordered

    @staticmethod
    def _working_context(context: dict[str, Any]) -> dict[str, Any]:
        value = context.get("working_context")
        if isinstance(value, dict):
            return value
        return {}

    @staticmethod
    def _compact_recent_turns(context: dict[str, Any], *, max_turns: int = 3, max_chars: int = 90) -> str:
        turns = OllamaMicroInferenceBackend._working_context(context).get("recent_turns")
        if not isinstance(turns, list):
            return "(none)"
        compact: list[str] = []
        for turn in turns[-max_turns:]:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role") or "").strip().lower() or "turn"
            text = re.sub(r"\s+", " ", str(turn.get("text") or "").strip())
            if not text:
                continue
            if len(text) > max_chars:
                text = f"{text[: max_chars - 3]}..."
            compact.append(f"{role}: {text}")
        if not compact:
            return "(none)"
        return " | ".join(compact)

    @staticmethod
    def _session_summary_text(context: dict[str, Any], *, max_chars: int = 180) -> str:
        summary = OllamaMicroInferenceBackend._working_context(context).get("session_summary")
        if not isinstance(summary, dict):
            return "(none)"
        text = re.sub(r"\s+", " ", str(summary.get("summary_text") or "").strip())
        if not text:
            return "(none)"
        if len(text) > max_chars:
            return f"{text[: max_chars - 3]}..."
        return text

    @staticmethod
    def _pending_hint(context: dict[str, Any], *, max_chars: int = 140) -> str:
        pending = OllamaMicroInferenceBackend._working_context(context).get("pending_interaction")
        if not isinstance(pending, dict):
            return "(none)"
        intent = str(pending.get("intent") or "").strip()
        missing = pending.get("expected_fields")
        if not isinstance(missing, list):
            missing = []
        missing_fields = [str(item).strip() for item in missing if str(item).strip()]
        question = re.sub(r"\s+", " ", str(pending.get("question") or "").strip())
        if len(question) > max_chars:
            question = f"{question[: max_chars - 3]}..."
        return f"intent={intent or 'unknown'} missing={missing_fields or []} question={question or None}"

    def _build_prompt(self, text: str, context: dict[str, Any]) -> str:
        session_state = str(context.get("session_state") or "IDLE")
        known_owner = str(context.get("session_owner") or "system")
        agent_id = str(context.get("agent_id") or "jarvis").strip().lower() or "jarvis"
        contextual_followup = context.get("contextual_followup")
        if not isinstance(contextual_followup, dict):
            contextual_followup = {}
        contextual_rewrite = str(contextual_followup.get("rewritten_user_text") or "").strip()
        contextual_topic = str(contextual_followup.get("active_topic") or "").strip()
        recent_turns = self._compact_recent_turns(context)
        session_summary = self._session_summary_text(context)
        pending_hint = self._pending_hint(context)
        core_profile = ""
        relevant_skills_profile = ""
        if self._skill_registry is not None:
            docs = self._skill_registry.load_model_boot_memory(model_name="microj", agent_id=agent_id)
            profile_parts: list[str] = []
            for doc in docs:
                doc_path = str(doc.get("doc_path") or "").strip().lower()
                content = str(doc.get("content") or "").strip()
                if not content:
                    continue
                if doc_path.endswith("jarvis_identity.md"):
                    continue
                if doc_path.endswith("jarvis_capabilities.md"):
                    continue
                profile_parts.append(content)
            core_profile = "\n\n".join(profile_parts).strip()

            intent_hints = self._collect_intent_hints(context)
            if intent_hints:
                user_id = str(context.get("requested_by_user_id") or context.get("user_id") or "").strip() or "local_user"
                skill_loader = getattr(
                    self._skill_registry,
                    "load_skill_runtime_docs_for_intents",
                    self._skill_registry.load_skill_docs_for_intents,
                )
                skill_docs = skill_loader(
                    intents=intent_hints,
                    user_id=user_id,
                    agent_id=agent_id,
                )
                relevant_parts = [str(doc.get("content") or "").strip() for doc in skill_docs]
                relevant_skills_profile = "\n\n".join(part for part in relevant_parts if part).strip()
        intents = ", ".join(sorted(ALLOWED_INTENTS))
        return (
            "You are micro_jarvis, a fast intent classifier for a home assistant.\n"
            "Classify the user text and extract entities. Respond with JSON only.\n"
            "Allowed intents: "
            f"{intents}\n"
            "Runtime core profile:\n"
            f"{core_profile or '(not provided)'}\n"
            "Relevant skill profiles (loaded on demand):\n"
            f"{relevant_skills_profile or '(not provided)'}\n"
            "Rules:\n"
            "- If text is explicit wake command, use system.wake.\n"
            "- If text is explicit sleep command, use system.sleep.\n"
            "- If text asks to add/schedule/invite on a calendar, use calendar.add_event (Main executes this).\n"
            "- If text asks to show/view/get calendar items, use calendar.view.\n"
            "- If text asks to change/move/rename an existing calendar event, use calendar.update_event (Main executes this).\n"
            "- 'Make that an all day event' is calendar.update_event with event_reference=that, all_day=true, "
            "and ambiguity_flags including deictic_event_reference.\n"
            "- If text asks to cancel/delete an existing calendar event, use calendar.delete_event (Main executes this).\n"
            "- For calendar invitees, only extract names when invite intent is explicit (invite/send to/add attendee).\n"
            "- If text asks to create a new list, use lists.create_list (Main executes this).\n"
            "- If text asks to add something to a list, use lists.add_item.\n"
            "- If text asks to show/view/get a list, use lists.get_items.\n"
            "- If text asks to delete a list, use lists.delete_list (Main executes this).\n"
            "- If text asks to remove an item from a list, use lists.remove_item (Main executes this).\n"
            "- If text asks to mark an item complete, use lists.mark_item_done (Main executes this).\n"
            "- For deictic list targets (it/that list/this list), still use lists.add_item but include ambiguity_flags with deictic_list_reference.\n"
            "- If text is just a cancel/abort phrase (never mind, cancel, stop), prefer conversational or unknown instead of forcing a tool intent.\n"
            "- Use canonical entity keys only:\n"
            "  calendar.add_event -> event_title, when_hint, invitee_names(optional list)\n"
            "  calendar.view -> window, person_name(optional)\n"
            "  calendar.update_event -> event_reference, new_event_title(optional), new_when_hint(optional), "
            "all_day(optional bool), event_id(optional), calendar_id(optional)\n"
            "  calendar.delete_event -> event_reference, event_id(optional), calendar_id(optional)\n"
            "  lists.add_item -> list_name, item_text\n"
            "  lists.get_items -> list_name\n"
            "  lists.create_list -> list_name\n"
            "  lists.delete_list -> list_name\n"
            "  lists.remove_item -> list_name, item_text\n"
            "  lists.mark_item_done -> list_name, item_text, completion_mode(optional)\n"
            "  home.set_switch -> switch_name, action(on|off)\n"
            "- Keep confidence between 0 and 1.\n"
            "- ambiguity_flags should include short strings for uncertainty.\n"
            "JSON schema:\n"
            '{"intent":"<allowed>","confidence":0.0,"entities":{},"ambiguity_flags":[],"reasoning":"..."}\n'
            f"Session state: {session_state}\n"
            f"Session owner: {known_owner}\n"
            f"Session summary: {session_summary}\n"
            f"Recent turns: {recent_turns}\n"
            f"Pending interaction: {pending_hint}\n"
            f"Contextual topic hint: {contextual_topic or None}\n"
            f"Contextual rewrite hint: {contextual_rewrite or None}\n"
            f"User text: {text}\n"
        )
