from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

from app.context.reference_resolver import ReferenceResolver
from app.context.types import EntityRegistry


CALENDAR_INTENTS = {
    "calendar.view",
    "calendar.add_event",
    "calendar.update_event",
    "calendar.delete_event",
}


def emit_context_entities(*, intent: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    if intent not in CALENDAR_INTENTS:
        return []
    status = str(result.get("status") or "").strip().lower()
    if status != "ok":
        return []

    entities: list[dict[str, Any]] = []
    person_name = str(result.get("person_name") or "").strip()
    if person_name:
        entities.append(
            {
                "domain": "calendar",
                "entity_type": "person",
                "display_name": person_name,
                "aliases": _aliases_for_person_name(person_name),
                "salience": 0.87,
                "resolution_hints": {
                    "intent": intent,
                    "status": status,
                },
            }
        )
    if intent in {"calendar.add_event", "calendar.update_event"}:
        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        event_title = str(event.get("event_title") or event.get("title") or "").strip()
        if event_title:
            event_id = str(event.get("google_event_id") or "").strip() or None
            entities.append(
                {
                    "domain": "calendar",
                    "entity_type": "event",
                    "entity_id": event_id,
                    "display_name": event_title,
                    "aliases": [event_title],
                    "salience": 0.98,
                    "resolution_hints": {
                        "intent": intent,
                        "status": status,
                        "event_id": event_id,
                        "calendar_id": str(event.get("host_calendar_id") or "").strip() or None,
                        "start_at": str(event.get("start_at") or "").strip() or None,
                    },
                }
            )
    return entities


def _aliases_for_person_name(person_name: str) -> list[str]:
    base = re.sub(r"\s+", " ", person_name.strip().lower()).strip()
    aliases = {base}
    aliases.add(re.sub(r"^(?:my|the)\s+", "", base).strip())
    return sorted(item for item in aliases if item)


def _pick_first_text(container: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = container.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _coerce_name_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = re.sub(r"^\s*invite(?:\s+to)?\s+", "", value.strip(), flags=re.IGNORECASE)
        parts = re.split(r"\s*(?:,| and | & )\s*", cleaned)
        return _dedupe_names([part.strip(" .") for part in parts if part.strip(" .")])
    if isinstance(value, list):
        return _dedupe_names([str(item).strip(" .") for item in value if str(item).strip(" .")])
    return []


def _normalize_person_reference(value: Any) -> str | None:
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
    normalized = re.sub(r"[^a-z0-9\s_-]+", " ", candidate.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return None
    if normalized in {"my", "our", "me", "us", "the", "house", "home", "household"}:
        return "default"
    normalized = re.sub(r"^(?:my|the|our)\s+", "", normalized).strip()
    normalized = re.sub(r"\s+calendar$", "", normalized).strip()
    if normalized in {"", "default", "main", "primary", "mine", "ours"}:
        return "default"
    return normalized


class CalendarContextContract:
    contract_id = "calendar"

    def supports_intent(self, *, intent: str) -> bool:
        return str(intent or "").strip().lower() in CALENDAR_INTENTS

    def normalize_entities(self, *, intent: str, entities: dict[str, Any]) -> dict[str, Any]:
        intent_value = str(intent or "").strip().lower()
        normalized = dict(entities)
        if intent_value == "calendar.add_event":
            event_title = _pick_first_text(
                normalized,
                ["event_title", "event_name", "title", "name", "subject", "event"],
            )
            when_hint = _pick_first_text(
                normalized,
                ["when_hint", "when", "start_time", "start", "start_at", "time", "date", "datetime"],
            )
            invitee_names = _coerce_name_list(
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

        if intent_value == "calendar.view":
            window = _pick_first_text(normalized, ["window", "range", "period"]) or "daily"
            window_clean = window.strip().lower()
            normalized["window"] = "weekly" if "week" in window_clean else "daily"
            person_name = _normalize_person_reference(
                normalized.get("person_name")
                or normalized.get("person")
                or normalized.get("owner")
                or normalized.get("calendar_owner")
            )
            if person_name:
                normalized["person_name"] = person_name
            else:
                normalized.pop("person_name", None)
            return normalized

        if intent_value in {"calendar.update_event", "calendar.delete_event"}:
            event_reference = _pick_first_text(
                normalized,
                ["event_reference", "event_name", "event_title", "event", "title", "name", "reference"],
            )
            event_id = _pick_first_text(normalized, ["event_id", "google_event_id"])
            calendar_id = _pick_first_text(normalized, ["calendar_id", "host_calendar_id"])
            if event_reference:
                normalized["event_reference"] = event_reference
            if event_id:
                normalized["event_id"] = event_id
            if calendar_id:
                normalized["calendar_id"] = calendar_id
            if intent_value == "calendar.update_event":
                new_title = _pick_first_text(
                    normalized,
                    ["new_event_title", "new_title", "updated_title", "replacement_title", "rename_to"],
                )
                new_when = _pick_first_text(
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
        return normalized

    def apply_text_constraints(
        self,
        *,
        intent: str,
        text: str,
        entities: dict[str, Any],
    ) -> dict[str, Any]:
        constrained = dict(entities)
        if str(intent or "").strip().lower() != "calendar.add_event":
            return constrained
        invitee_names = _extract_calendar_invitee_names(text)
        if invitee_names:
            constrained["invitee_names"] = invitee_names
            constrained["invite_explicit"] = True
        else:
            constrained.pop("invitee_names", None)
            constrained.pop("invite_explicit", None)
        return constrained

    def clarification_supplemental_fields(self, *, intent: str) -> list[str]:
        if str(intent or "").strip().lower() == "calendar.add_event":
            return ["invitee_names", "invite_explicit"]
        return []

    def emit_context_updates(self, *, intent: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        return emit_context_entities(intent=intent, result=result)

    def resolve_followup(
        self,
        *,
        decision: Any,
        registry: EntityRegistry,
        resolver: ReferenceResolver,
        required_fields_for_intent: Callable[[Any, dict[str, Any]], list[str]],
        has_blocking_ambiguity: Callable[[Any], bool],
    ) -> Any:
        del required_fields_for_intent
        del has_blocking_ambiguity
        intent_value = str(getattr(getattr(decision, "intent", None), "value", "")).strip().lower()
        if not intent_value:
            intent_value = str(getattr(decision, "intent", "")).strip().lower()
        if intent_value in {"calendar.update_event", "calendar.delete_event"}:
            entities = getattr(decision, "entities", {})
            if not isinstance(entities, dict):
                return decision
            event_reference = str(entities.get("event_reference") or "").strip()
            if not event_reference:
                return decision
            resolved = resolver.resolve_reference(
                value=event_reference,
                registry=registry,
                domain="calendar",
                entity_type="event",
                deictic_only=True,
            )
            if resolved is None:
                return decision
            entities["event_reference"] = str(resolved.entity.display_name or "").strip()
            hints = resolved.entity.resolution_hints if isinstance(resolved.entity.resolution_hints, dict) else {}
            event_id = str(hints.get("event_id") or resolved.entity.entity_id or "").strip()
            calendar_id = str(hints.get("calendar_id") or "").strip()
            if event_id:
                entities["event_id"] = event_id
            if calendar_id:
                entities["calendar_id"] = calendar_id
            decision.entities = entities
            decision.ambiguity_flags = [
                flag
                for flag in getattr(decision, "ambiguity_flags", [])
                if str(flag).strip().casefold() != "deictic_event_reference"
            ]
            if "event_reference_resolved_from_context" not in decision.ambiguity_flags:
                decision.ambiguity_flags.append("event_reference_resolved_from_context")
            decision.confidence = max(float(getattr(decision, "confidence", 0.0)), 0.91)
            return decision

        if intent_value != "calendar.view":
            return decision
        entities = getattr(decision, "entities", {})
        if not isinstance(entities, dict):
            return decision
        person_name = str(entities.get("person_name") or "").strip()
        if not person_name:
            return decision
        resolved = resolver.resolve_reference(
            value=person_name,
            registry=registry,
            domain="calendar",
            entity_type="person",
            deictic_only=True,
        )
        if resolved is None:
            return decision
        entities["person_name"] = str(resolved.entity.display_name or "").strip()
        decision.entities = entities
        return decision

    def resolve_handoff_followup(
        self,
        *,
        decision: Any,
        active_skill_context: dict[str, Any],
        resolver: ReferenceResolver,
    ) -> Any:
        intent_value = str(getattr(getattr(decision, "intent", None), "value", "")).strip().casefold()
        if intent_value not in {"calendar.update_event", "calendar.delete_event"}:
            return decision
        entities = getattr(decision, "entities", {})
        if not isinstance(entities, dict):
            return decision
        reference = str(entities.get("event_reference") or "").strip()
        if not reference or not resolver.is_deictic_reference(value=reference, entity_type="event"):
            return decision
        memory_reference = str(active_skill_context.get("last_event_reference") or "").strip()
        if not memory_reference or resolver.is_deictic_reference(value=memory_reference, entity_type="event"):
            return decision
        entities["event_reference"] = memory_reference
        decision.entities = entities
        decision.ambiguity_flags = [
            flag
            for flag in getattr(decision, "ambiguity_flags", [])
            if str(flag).strip().casefold() != "deictic_event_reference"
        ]
        if "event_reference_resolved_from_memory_handoff" not in decision.ambiguity_flags:
            decision.ambiguity_flags.append("event_reference_resolved_from_memory_handoff")
        decision.confidence = max(float(getattr(decision, "confidence", 0.0)), 0.9)
        return decision

    def refine_missing_fields(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        missing_fields: list[str],
        resolver: ReferenceResolver,
    ) -> list[str]:
        deduped: list[str] = []
        for item in missing_fields:
            cleaned = str(item).strip()
            if cleaned and cleaned not in deduped:
                deduped.append(cleaned)
        intent_value = str(intent or "").strip().casefold()
        event_reference = str(entities.get("event_reference") or "").strip()
        if (
            intent_value in {"calendar.update_event", "calendar.delete_event"}
            and not str(entities.get("event_id") or "").strip()
            and event_reference
            and resolver.is_deictic_reference(value=event_reference, entity_type="event")
            and "event_reference" not in deduped
        ):
            deduped.append("event_reference")
        return deduped

    def required_fields(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        resolver: ReferenceResolver,
    ) -> list[str] | None:
        intent_value = str(intent or "").strip().lower()
        if intent_value == "calendar.update_event":
            missing: list[str] = []
            if not str(entities.get("event_id") or "").strip() and not str(
                entities.get("event_reference") or ""
            ).strip():
                missing.append("event_reference")
            has_change = any(
                [
                    str(entities.get("new_event_title") or "").strip(),
                    str(entities.get("new_when_hint") or "").strip(),
                    isinstance(entities.get("all_day"), bool),
                ]
            )
            if not has_change:
                missing.append("changes")
            return self.refine_missing_fields(
                intent=intent_value,
                entities=entities,
                missing_fields=missing,
                resolver=resolver,
            )
        if intent_value == "calendar.delete_event":
            missing = []
            if not str(entities.get("event_id") or "").strip() and not str(
                entities.get("event_reference") or ""
            ).strip():
                missing.append("event_reference")
            return self.refine_missing_fields(
                intent=intent_value,
                entities=entities,
                missing_fields=missing,
                resolver=resolver,
            )
        if intent_value != "calendar.add_event":
            return None
        missing: list[str] = []
        event_title = str(entities.get("event_title") or "").strip()
        when_hint = str(entities.get("when_hint") or "").strip()
        if not event_title or _is_placeholder_calendar_title(event_title):
            missing.append("event_title")
        if not when_hint or _is_vague_calendar_when_hint(when_hint):
            missing.append("when_hint")
        return self.refine_missing_fields(
            intent=intent_value,
            entities=entities,
            missing_fields=missing,
            resolver=resolver,
        )

    def clarification_question(
        self,
        *,
        intent: str,
        field_name: str,
    ) -> str | None:
        intent_value = str(intent or "").strip().lower()
        field = str(field_name or "").strip()
        if intent_value in {"calendar.update_event", "calendar.delete_event"} and field == "event_reference":
            return "Which calendar event do you mean?"
        if intent_value == "calendar.update_event" and field == "changes":
            return "What would you like to change about that event?"
        if intent_value == "calendar.update_event" and field == "new_when_hint":
            return "What date and time should the event use?"
        if intent_value != "calendar.add_event":
            return None
        if field == "event_title":
            return "What should I name the calendar event?"
        if field == "when_hint":
            return "When should I schedule it? You can say something like `tomorrow at noon` or `daily`."
        return None

    def continue_pending_interaction(
        self,
        *,
        intent: str,
        text: str,
        missing_fields: list[str],
        current_entities: dict[str, Any],
    ) -> dict[str, Any]:
        intent_value = str(intent or "").strip().lower()
        if intent_value in {"calendar.update_event", "calendar.delete_event"}:
            cleaned_text = re.sub(r"\s+", " ", str(text or "").strip())
            if not cleaned_text:
                return {}
            missing = [str(item).strip() for item in missing_fields if str(item).strip()]
            updates: dict[str, Any] = {}
            if "event_reference" in missing:
                candidate = cleaned_text.strip(" .,'\"")
                if candidate and not resolver_is_deictic_event(candidate):
                    updates["event_reference"] = candidate
            if intent_value == "calendar.update_event" and "changes" in missing:
                if re.search(r"\ball[ -]?day\b", cleaned_text, flags=re.IGNORECASE):
                    updates["all_day"] = True
                    remainder = re.sub(r"\ball[ -]?day\b", "", cleaned_text, flags=re.IGNORECASE)
                    remainder = re.sub(r"^(?:make|change|update)\s+(?:it|that|this|the event)?\s*", "", remainder, flags=re.IGNORECASE)
                    remainder = remainder.strip(" ,.-")
                    if remainder:
                        updates["new_when_hint"] = remainder
            return updates
        if intent_value != "calendar.add_event":
            return {}
        cleaned_text = re.sub(r"\s+", " ", str(text or "").strip())
        if not cleaned_text:
            return {}
        missing = [str(item).strip() for item in missing_fields if str(item).strip()]
        if not missing:
            return {}
        updates: dict[str, Any] = {}

        if "event_title" in missing:
            title = _extract_event_title_from_followup(cleaned_text)
            if title and not _is_placeholder_calendar_title(title):
                updates["event_title"] = title

        if "when_hint" in missing:
            when_hint = _extract_when_hint_from_followup(cleaned_text, missing_fields=missing)
            if when_hint and not _is_vague_calendar_when_hint(when_hint):
                updates["when_hint"] = when_hint

        invitee_names = _extract_calendar_invitee_names(cleaned_text)
        if invitee_names:
            updates["invitee_names"] = invitee_names
            updates["invite_explicit"] = True

        if not updates:
            return {}
        return updates

    def shape_tool_followup(
        self,
        *,
        intent: str,
        status: str,
        tool_result: dict[str, Any],
        entities: dict[str, Any],
        missing_fields: list[str],
        question: str | None,
        registry: EntityRegistry,
    ) -> dict[str, Any]:
        del registry
        intent_value = str(intent or "").strip().lower()
        if intent_value not in {
            "calendar.add_event",
            "calendar.update_event",
            "calendar.delete_event",
        }:
            return {}

        next_entities = dict(entities)
        next_missing = [str(item).strip() for item in missing_fields if str(item).strip()]
        tool_missing = tool_result.get("missing_fields")
        if isinstance(tool_missing, list):
            for item in tool_missing:
                cleaned = str(item).strip()
                if cleaned and cleaned not in next_missing:
                    next_missing.append(cleaned)

        next_question = str(question).strip() if isinstance(question, str) and question.strip() else None
        if isinstance(tool_result.get("question"), str):
            candidate = str(tool_result.get("question") or "").strip()
            if candidate:
                next_question = candidate

        status_value = str(status or "").strip().lower()
        if status_value in {"needs_input", "needs_clarification"} and next_question is None and next_missing:
            next_question = self.clarification_question(intent=intent_value, field_name=next_missing[0])

        return {
            "entities": next_entities,
            "missing_fields": next_missing,
            "question": next_question,
        }

    def legacy_main_handoff_hints(
        self,
        *,
        registry: EntityRegistry,
        context_reference: dict[str, Any],
        runtime_context: dict[str, Any] | None = None,
        intent: str | None = None,
        route: str | None = None,
    ) -> dict[str, Any]:
        del runtime_context
        del intent
        del route
        person_name = _latest_entity_display_name(
            registry=registry,
            domain="calendar",
            entity_type="person",
        )
        if not person_name:
            person_name = str(context_reference.get("last_calendar_person") or "").strip() or None
        event_name = _latest_entity_display_name(
            registry=registry,
            domain="calendar",
            entity_type="event",
        )
        hints: dict[str, Any] = {}
        if person_name:
            hints["last_calendar_person"] = person_name
        if event_name:
            hints["last_event_reference"] = event_name
        last_action = str(context_reference.get("last_calendar_action") or "").strip()
        if last_action:
            hints["last_calendar_action"] = last_action
        return hints

    def memory_handoff_hints(
        self,
        *,
        relevant_memory: list[dict[str, Any]],
        intent: str | None = None,
        request_text: str | None = None,
    ) -> dict[str, Any]:
        del request_text
        target_intent = str(intent or "").strip().casefold()
        if target_intent and target_intent not in CALENDAR_INTENTS and target_intent != "unknown":
            return {}
        for row in reversed(relevant_memory):
            if not isinstance(row, dict):
                continue
            row_intent = str(row.get("intent") or "").strip().casefold()
            if row_intent not in {
                "calendar.add_event",
                "calendar.update_event",
                "calendar.view",
            }:
                continue
            reference = _event_reference_from_memory_row(row)
            if reference:
                return {
                    "last_event_reference": reference,
                    "last_calendar_action": row_intent,
                }
        return {}


def _latest_entity_display_name(
    *,
    registry: EntityRegistry,
    domain: str,
    entity_type: str,
) -> str | None:
    candidates = [
        item
        for item in registry.entities
        if item.domain == domain and item.entity_type == entity_type and str(item.display_name or "").strip()
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            float(item.salience),
            _sort_timestamp(item.last_confirmed_at),
        ),
        reverse=True,
    )
    return str(candidates[0].display_name or "").strip() or None


def _sort_timestamp(value: str | None) -> float:
    if not isinstance(value, str) or not value.strip():
        return 0.0
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _is_placeholder_calendar_title(title: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(title or "").strip().lower())
    normalized = re.sub(r"^(?:a|an|the)\s+", "", normalized).strip()
    return normalized in {"event", "meeting", "appointment", "calendar event", "something", "it"}


def _is_vague_calendar_when_hint(when_hint: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(when_hint or "").strip().lower())
    return normalized in {"sometime", "soon", "later", "whenever", "anytime", "eventually"}


def _extract_event_title_from_followup(text: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if not cleaned:
        return None
    patterns = [
        r"^(?:let'?s\s+)?(?:call|name)\s+(?:it|this|the event)\s+(?:as\s+)?(?P<title>.+)$",
        r"^(?:it is|it's)\s+(?P<title>.+)$",
        r"^(?:title(?:\s+is)?|event title(?:\s+is)?)\s+(?P<title>.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, cleaned, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = str(match.group("title") or "").strip(" .,'\"")
        candidate = _strip_invite_clause(candidate)
        if candidate:
            return candidate
    candidate = _strip_invite_clause(cleaned).strip(" .,'\"")
    if candidate:
        return candidate
    return None


def _extract_when_hint_from_followup(text: str, *, missing_fields: list[str]) -> str | None:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if not cleaned:
        return None
    lowered = cleaned.lower()
    if re.match(r"^(?:yes|yeah|yep|yup|ok|okay|sure|correct|right)\b", lowered):
        cleaned = re.sub(r"^(?:yes|yeah|yep|yup|ok|okay|sure|correct|right)\b[\s,.:;-]*", "", cleaned, flags=re.IGNORECASE)
        lowered = cleaned.lower()
    if not cleaned:
        return None
    if re.match(r"^(?:let'?s\s+)?(?:call|name)\s+(?:it|this|the event)\b", lowered):
        return None
    if re.match(r"^(?:it is|it's)\s+", lowered):
        return None
    if _is_vague_calendar_when_hint(cleaned):
        return None

    temporal_markers = (
        r"\b(today|tomorrow|tonight|morning|afternoon|evening|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"next|this week|next week|daily|weekly|monthly|am|pm|\d{1,2}:\d{2})\b"
    )
    if re.search(temporal_markers, lowered):
        return cleaned
    if len(missing_fields) == 1 and str(missing_fields[0]).strip() == "when_hint":
        return cleaned
    return None


def _strip_invite_clause(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    splitter = re.search(
        r"\s+(?:and\s+)?(?:invite|send(?:\s+it)?\s+to|add\s+attendee(?:s)?)\b",
        cleaned,
        flags=re.IGNORECASE,
    )
    if splitter:
        cleaned = cleaned[: splitter.start()].strip()
    return cleaned


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
        names_text = str(match.group("names") or "").strip()
        if names_text:
            break
    if not names_text:
        return []
    parts = re.split(r"\s*(?:,| and | & )\s*", names_text)
    names = [
        part.strip(" .,'\"")
        for part in parts
        if part.strip(" .,'\"")
        and part.strip(" .,'\"").lower() not in {"him", "her", "them", "everyone", "all"}
    ]
    return _dedupe_names(names)


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


def resolver_is_deictic_event(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
    return normalized in {
        "it",
        "that",
        "this",
        "that event",
        "this event",
        "the event",
        "same event",
    }


def _event_reference_from_memory_row(row: dict[str, Any]) -> str | None:
    row_intent = str(row.get("intent") or "").strip().casefold()
    response = str(row.get("response_summary") or "").strip()
    response_match = re.search(
        r"\b(?:added|updated|deleted)\s+[\"`'](?P<title>.+?)[\"`']",
        response,
        flags=re.IGNORECASE,
    )
    if response_match:
        title = str(response_match.group("title") or "").strip()
        if title:
            return title

    request = re.sub(r"\s+", " ", str(row.get("request_text") or "").strip())
    quoted = re.search(r"[\"'](?P<title>[^\"']+)[\"']", request)
    if quoted:
        title = str(quoted.group("title") or "").strip()
        if title:
            return title
    if row_intent == "calendar.update_event" and response.casefold() in {"ok", "executed"}:
        # A clarification answer can be only the event title (for example,
        # `ICDP party`). Preserve that confirmed reference for a later turn.
        standalone = request.strip(" .,'\"")
        if (
            standalone
            and len(standalone.split()) <= 12
            and not resolver_is_deictic_event(standalone)
            and not re.search(
                r"\b(?:make|change|convert|move|reschedule|rename|update|all[ -]?day)\b",
                standalone,
                flags=re.IGNORECASE,
            )
        ):
            return standalone
    if re.search(r"\bcal(?:endar|andar)\b", request, flags=re.IGNORECASE):
        named_match = re.search(r"\b(?:called|named)\s+(?P<title>.+)$", request, flags=re.IGNORECASE)
        if named_match:
            title = str(named_match.group("title") or "").strip(" .,'\"")
            if title and not _is_placeholder_calendar_title(title):
                return title
    add_match = re.search(
        r"\b(?:add|put|schedule|create)\s+(?:an?\s+)?(?P<title>.+?)\s+"
        r"(?:to|on)\s+(?:my\s+|the\s+|our\s+)?cal(?:endar|andar)\b",
        request,
        flags=re.IGNORECASE,
    )
    if add_match:
        title = str(add_match.group("title") or "").strip(" .,'\"")
        title = re.sub(r"^(?:event|meeting|appointment)\s+", "", title, flags=re.IGNORECASE).strip()
        if title:
            return title
    return None
