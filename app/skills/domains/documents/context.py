from __future__ import annotations

import re
from typing import Any, Callable

from app.context.reference_resolver import ReferenceResolver
from app.context.types import EntityRegistry
from app.core.micro_jarvis import MicroDecision
from app.core.types import Intent, SessionOwner
from app.skills.domains.documents.query_service import DOCUMENT_INTENTS


class DocumentsContextContract:
    contract_id = "documents"

    _ATTACHMENT_REFERENCE = re.compile(
        r"\b(?:image|photo|picture|attachment|document|scan|pdf|file|it|that)\b",
        flags=re.IGNORECASE,
    )
    _READ_REQUEST = re.compile(
        r"\b(?:read|says?|text|tell|show|transcrib\w*|extract|contents?|written|see)\b",
        flags=re.IGNORECASE,
    )

    @staticmethod
    def _bounded_attachment_ids(request_context: dict[str, Any], key: str) -> list[str]:
        raw_ids = request_context.get(key)
        if not isinstance(raw_ids, list):
            return []
        return [
            str(item).strip()
            for item in raw_ids[:4]
            if isinstance(item, str) and str(item).strip()
        ]

    @classmethod
    def _is_scoped_discord_read_request(
        cls,
        *,
        request_context: dict[str, Any],
        text: str,
    ) -> tuple[bool, list[str]]:
        if (
            str(request_context.get("principal_kind") or "").strip().casefold()
            != "discord_adapter"
            or not str(request_context.get("discord_channel_id") or "").strip()
        ):
            return False, []
        current_ids = cls._bounded_attachment_ids(
            request_context,
            "current_document_attachment_ids",
        )
        if current_ids and str(text or "").strip():
            return True, current_ids
        recent_ids = cls._bounded_attachment_ids(request_context, "document_attachment_ids")
        normalized_text = str(text or "").strip()
        return (
            bool(
                recent_ids
                and cls._ATTACHMENT_REFERENCE.search(normalized_text)
                and cls._READ_REQUEST.search(normalized_text)
            ),
            recent_ids,
        )

    def request_interrupts_pending(
        self,
        *,
        request_context: dict[str, Any],
        text: str,
        pending_intent: str,
    ) -> bool:
        del pending_intent
        matches, _document_ids = self._is_scoped_discord_read_request(
            request_context=request_context,
            text=text,
        )
        return matches

    def bind_request_decision(
        self,
        *,
        decision: Any,
        request_context: dict[str, Any],
        working_context: dict[str, Any],
        text: str,
    ) -> Any:
        del working_context
        intent = getattr(decision, "intent", None)
        if intent in {Intent.DOCUMENTS_ESCALATE_OCR, Intent.DOCUMENTS_CORRECT_FIELD}:
            entities = getattr(decision, "entities", None)
            if not isinstance(entities, dict):
                return decision
            if (
                str(request_context.get("principal_kind") or "").strip().casefold()
                != "discord_adapter"
                or not str(request_context.get("discord_channel_id") or "").strip()
            ):
                return decision
            if intent == Intent.DOCUMENTS_CORRECT_FIELD and not (
                str(entities.get("field_name") or "").strip()
                and str(entities.get("corrected_value") or "").strip()
            ):
                decision.intent = Intent.DOCUMENTS_ESCALATE_OCR
                entities = {
                    "document_id": str(entities.get("document_id") or "").strip(),
                }
                decision.entities = {
                    key: value
                    for key, value in entities.items()
                    if str(value or "").strip()
                }
            if str(entities.get("document_id") or "").strip():
                return decision
            document_ids = self._bounded_attachment_ids(
                request_context,
                "current_document_attachment_ids",
            ) or self._bounded_attachment_ids(request_context, "document_attachment_ids")
            if not document_ids:
                return decision
            decision.entities = {**entities, "document_id": document_ids[-1]}
            decision.confidence = max(float(getattr(decision, "confidence", 0.0)), 0.88)
            ambiguity_flags = list(getattr(decision, "ambiguity_flags", []) or [])
            if "trusted_discord_attachment_binding" not in ambiguity_flags:
                ambiguity_flags.append("trusted_discord_attachment_binding")
            decision.ambiguity_flags = ambiguity_flags
            decision.recommended_owner = SessionOwner.MAIN
            return decision
        if intent not in {Intent.UNKNOWN, Intent.CONVERSATIONAL}:
            return decision
        matches, document_ids = self._is_scoped_discord_read_request(
            request_context=request_context,
            text=text,
        )
        if not matches or not document_ids:
            return decision
        return MicroDecision(
            intent=Intent.DOCUMENTS_GET,
            confidence=0.99,
            entities={"document_id": document_ids[-1]},
            ambiguity_flags=["trusted_discord_attachment_binding"],
            recommended_owner=SessionOwner.MAIN,
            reasoning="documents_context_bound_recent_discord_attachment",
        )

    def supports_intent(self, *, intent: str) -> bool:
        return str(intent or "").strip().casefold() in DOCUMENT_INTENTS

    def normalize_entities(self, *, intent: str, entities: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(entities)
        aliases = {
            "document_id": ("document_id", "document", "id"),
            "query": ("query", "search", "terms"),
            "field_name": ("field_name", "field", "metadata_field"),
            "proposed_value": ("proposed_value", "value", "metadata_value"),
            "corrected_value": ("corrected_value", "new_value", "replacement"),
        }
        for target, candidates in aliases.items():
            for candidate in candidates:
                value = normalized.get(candidate)
                if value is not None and str(value).strip():
                    normalized[target] = str(value).strip()
                    break
        if str(intent or "").strip().casefold() == "documents.correct_field":
            normalized["field_name"] = self._canonical_field_name(normalized.get("field_name"))
        return normalized

    @staticmethod
    def _canonical_field_name(value: object) -> str:
        normalized = " ".join(str(value or "").strip().casefold().replace("_", " ").split())
        aliases = {
            "name": "full_name",
            "full name": "full_name",
            "person": "full_name",
            "company": "organization",
            "business": "organization",
            "employer": "organization",
            "organisation": "organization",
            "title": "job_title",
            "role": "job_title",
            "job title": "job_title",
            "email address": "email",
            "phone number": "phone",
            "telephone": "phone",
            "web site": "website",
            "site": "website",
            "url": "website",
        }
        return aliases.get(normalized, normalized.replace(" ", "_"))

    def apply_text_constraints(
        self,
        *,
        intent: str,
        text: str,
        entities: dict[str, Any],
    ) -> dict[str, Any]:
        del intent, text
        return dict(entities)

    def emit_context_updates(self, *, intent: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        del intent
        rows = result.get("document_context_entities")
        return [dict(item) for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []

    def enrich_working_context(
        self,
        *,
        request_context: dict[str, Any],
        working_context: dict[str, Any],
    ) -> dict[str, Any]:
        raw_ids = request_context.get("document_attachment_ids")
        ids = [
            str(item).strip()
            for item in (raw_ids[:4] if isinstance(raw_ids, list) else [])
            if isinstance(item, str) and str(item).strip()
        ]
        result_contexts = self._bounded_result_contexts(request_context)
        for item in result_contexts:
            document_id = str(item.get("document_id") or "").strip()
            if document_id and document_id not in ids:
                ids.append(document_id)
        if not ids:
            return {}
        existing = working_context.get("entity_hints")
        hints = [dict(item) for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
        known = {
            str(item.get("entity_id") or "").strip()
            for item in hints
            if str(item.get("domain") or "").casefold() == "documents"
        }
        for index, document_id in enumerate(ids):
            if document_id in known:
                continue
            result_context = next(
                (
                    item
                    for item in reversed(result_contexts)
                    if str(item.get("document_id") or "").strip() == document_id
                ),
                None,
            )
            document_class = (
                str(result_context.get("document_class") or "").strip().replace("_", " ")
                if isinstance(result_context, dict)
                else ""
            )
            field_names = (
                list(result_context.get("field_names") or [])
                if isinstance(result_context, dict)
                else []
            )
            aliases = ["this image", "this attachment", "the image"]
            for field_name in field_names:
                spoken = str(field_name).replace("_", " ")
                aliases.extend((spoken, f"the {spoken}", f"{spoken} field"))
            hints.append(
                {
                    "domain": "documents",
                    "entity_type": "document",
                    "entity_id": document_id,
                    "display_name": (
                        f"recent {document_class} OCR result"
                        if document_class
                        else "recent Discord attachment"
                    ),
                    "aliases": list(dict.fromkeys(aliases))[:24],
                    "salience": max(0.7, 1.0 - (index * 0.1)),
                    "resolution_hints": {
                        "document_id": document_id,
                        "source": (
                            "discord_document_result"
                            if isinstance(result_context, dict)
                            else "discord_attachment"
                        ),
                    },
                }
            )
        return {
            "entity_hints": hints,
            "active_skill_context": {
                "attached_document_ids": ids,
                "last_document_id": ids[-1],
                "document_result_shapes": result_contexts,
            },
        }

    @staticmethod
    def _bounded_result_contexts(request_context: dict[str, Any]) -> list[dict[str, Any]]:
        raw = request_context.get("document_result_contexts")
        if not isinstance(raw, list):
            return []
        results: list[dict[str, Any]] = []
        for row in raw[:4]:
            if not isinstance(row, dict) or row.get("schema_version") != 1:
                continue
            document_id = str(row.get("document_id") or "").strip()
            if not document_id or len(document_id) > 128:
                continue
            document_class = str(row.get("document_class") or "").strip().casefold()
            if document_class and not re.fullmatch(r"[a-z0-9_-]{1,64}", document_class):
                document_class = ""
            processing_state = str(row.get("processing_state") or "").strip().casefold()
            if processing_state and not re.fullmatch(r"[a-z0-9_-]{1,40}", processing_state):
                processing_state = ""
            field_names: list[str] = []
            raw_fields = row.get("field_names")
            if isinstance(raw_fields, list):
                for value in raw_fields[:16]:
                    field_name = str(value or "").strip().casefold()
                    if re.fullmatch(r"[a-z0-9_]{1,64}", field_name):
                        field_names.append(field_name)
            results.append(
                {
                    "schema_version": 1,
                    "document_id": document_id,
                    "document_class": document_class or None,
                    "processing_state": processing_state or None,
                    "field_names": list(dict.fromkeys(field_names)),
                }
            )
        return results

    def resolve_followup(
        self,
        *,
        decision: Any,
        registry: EntityRegistry,
        resolver: ReferenceResolver,
        required_fields_for_intent: Callable[[Any, dict[str, Any]], list[str]],
        has_blocking_ambiguity: Callable[[Any], bool],
    ) -> Any:
        intent = str(getattr(getattr(decision, "intent", None), "value", "") or "").casefold()
        if intent not in DOCUMENT_INTENTS:
            return decision
        entities = getattr(decision, "entities", None)
        if not isinstance(entities, dict):
            return decision
        candidate = str(entities.get("document_id") or "").strip()
        resolved = resolver.resolve_reference(
            value=candidate,
            registry=registry,
            domain="documents",
            entity_type="document",
            deictic_only=True,
        )
        if resolved is None:
            return decision
        document_id = str(
            resolved.entity.entity_id
            or resolved.entity.resolution_hints.get("document_id")
            or ""
        ).strip()
        if not document_id:
            return decision
        entities["document_id"] = document_id
        decision.entities = entities
        decision.confidence = max(float(getattr(decision, "confidence", 0.0)), 0.88)
        if not required_fields_for_intent(decision.intent, entities) and not has_blocking_ambiguity(decision):
            decision.recommended_owner = SessionOwner.MAIN
        return decision

    def refine_missing_fields(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        missing_fields: list[str],
        resolver: ReferenceResolver,
    ) -> list[str]:
        del intent, entities, resolver
        return list(dict.fromkeys(str(item).strip() for item in missing_fields if str(item).strip()))

    def required_fields(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        resolver: ReferenceResolver,
    ) -> list[str] | None:
        del resolver
        requirements = {
            "documents.find": ["query"],
            "documents.status": ["document_id"],
            "documents.get": ["document_id"],
            "documents.show_source": ["document_id"],
            "documents.reprocess": ["document_id"],
            "documents.escalate_ocr": ["document_id"],
            "documents.propose_metadata": ["document_id", "field_name", "proposed_value"],
            "documents.correct_field": ["document_id", "field_name", "corrected_value"],
            "documents.confirm_fields": ["document_id"],
            "documents.ingest": [],
            "documents.list_reviews": [],
        }.get(str(intent or "").casefold())
        if requirements is None:
            return None
        return [name for name in requirements if not str(entities.get(name) or "").strip()]

    def clarification_question(self, *, intent: str, field_name: str) -> str | None:
        del intent
        return {
            "document_id": "Which document do you mean?",
            "query": "What should I search for in your documents?",
            "field_name": "Which low-risk metadata field should change?",
            "proposed_value": "What value should I propose for review?",
            "corrected_value": "What should that field say instead?",
        }.get(str(field_name))

    def continue_pending_interaction(
        self,
        *,
        intent: str,
        text: str,
        missing_fields: list[str],
        current_entities: dict[str, Any],
    ) -> dict[str, Any]:
        del intent, current_entities
        if len(missing_fields) != 1 or not str(text).strip():
            return {}
        return {str(missing_fields[0]): str(text).strip()}

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
        del intent, status, tool_result, entities, missing_fields, question, registry
        return {}

    def legacy_main_handoff_hints(
        self,
        *,
        registry: EntityRegistry,
        context_reference: dict[str, Any],
        runtime_context: dict[str, Any] | None = None,
        intent: str | None = None,
        route: str | None = None,
    ) -> dict[str, Any]:
        del context_reference, runtime_context, intent, route
        candidates = [
            item
            for item in registry.entities
            if item.domain == "documents" and item.entity_type == "document"
        ]
        candidates.sort(key=lambda item: float(item.salience), reverse=True)
        if not candidates:
            return {}
        document_id = str(
            candidates[0].entity_id
            or candidates[0].resolution_hints.get("document_id")
            or ""
        ).strip()
        return {"last_document_id": document_id} if document_id else {}

    def memory_handoff_hints(
        self,
        *,
        relevant_memory: list[dict[str, Any]],
        intent: str | None = None,
        request_text: str | None = None,
    ) -> dict[str, Any]:
        del relevant_memory, intent, request_text
        return {}
