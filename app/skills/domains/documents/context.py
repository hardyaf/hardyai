from __future__ import annotations

from typing import Any, Callable

from app.context.reference_resolver import ReferenceResolver
from app.context.types import EntityRegistry
from app.core.types import SessionOwner
from app.skills.domains.documents.query_service import DOCUMENT_INTENTS


class DocumentsContextContract:
    contract_id = "documents"

    def supports_intent(self, *, intent: str) -> bool:
        return str(intent or "").strip().casefold() in DOCUMENT_INTENTS

    def normalize_entities(self, *, intent: str, entities: dict[str, Any]) -> dict[str, Any]:
        del intent
        normalized = dict(entities)
        aliases = {
            "document_id": ("document_id", "document", "id"),
            "query": ("query", "search", "terms"),
            "field_name": ("field_name", "field", "metadata_field"),
            "proposed_value": ("proposed_value", "value", "metadata_value"),
        }
        for target, candidates in aliases.items():
            for candidate in candidates:
                value = normalized.get(candidate)
                if value is not None and str(value).strip():
                    normalized[target] = str(value).strip()
                    break
        return normalized

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
        if not isinstance(raw_ids, list):
            return {}
        ids = [
            str(item).strip()
            for item in raw_ids[:4]
            if isinstance(item, str) and str(item).strip()
        ]
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
            hints.append(
                {
                    "domain": "documents",
                    "entity_type": "document",
                    "entity_id": document_id,
                    "display_name": "recent Discord attachment",
                    "aliases": ["this image", "this attachment", "the image"],
                    "salience": max(0.7, 1.0 - (index * 0.1)),
                    "resolution_hints": {"document_id": document_id, "source": "discord_attachment"},
                }
            )
        return {
            "entity_hints": hints,
            "active_skill_context": {
                "attached_document_ids": ids,
                "last_document_id": ids[-1],
            },
        }

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
            "documents.propose_metadata": ["document_id", "field_name", "proposed_value"],
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
