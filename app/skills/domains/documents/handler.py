from __future__ import annotations

import re
from typing import Any

from app.skills.domains.documents.permissions import DocumentRequestAccessPolicy
from app.skills.tool_contracts import (
    ToolArgumentCanonicalizationError,
    ToolCallEnvelope,
    thaw_json,
)


DOCUMENT_READ_TOOLS = {
    "documents.upload_capability": "documents.ingest",
    "documents.search": "documents.find",
    "documents.status": "documents.status",
    "documents.inspect": "documents.get",
    "documents.source_link": "documents.show_source",
    "documents.list_reviews": "documents.list_reviews",
}
DOCUMENT_WRITE_TOOLS = frozenset(
    {
        "documents.queue_processing",
        "documents.propose_metadata",
        "documents.review_field",
        "documents.confirm_fields",
    }
)
_DOCUMENT_ID_TOOLS = frozenset(
    {"documents.status", "documents.inspect", "documents.source_link"}
)
_SENSITIVITIES = frozenset(
    {"normal", "private", "financial", "identity", "highly_restricted"}
)
_SAFE_SOURCE_LINK = re.compile(r"^/documents/[A-Za-z0-9._~%+-]{1,256}/source$")


def describe_capability(*, services: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    service = services.get("documents_service")
    if service is None:
        return {
            "configured": False,
            "authorized_here": False,
            "availability": "disabled",
            "access_note": "The local Documents service is disabled.",
        }
    return service.capability_access(context=context)


class DocumentsToolHandler:
    """Typed, no-store access over the existing restricted Documents facade."""

    SKILL_ID = "skill.documents.local"

    def __init__(self, *, documents_service: Any) -> None:
        self._documents_service = documents_service

    def canonicalize_tool_arguments(
        self,
        *,
        tool_id: str,
        validated_arguments: dict[str, Any],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_tool_id = str(tool_id or "").strip().casefold()
        legacy_operation = DOCUMENT_READ_TOOLS.get(normalized_tool_id)
        if legacy_operation is None and normalized_tool_id not in DOCUMENT_WRITE_TOOLS:
            raise ToolArgumentCanonicalizationError("documents_tool_unsupported")
        arguments = dict(validated_arguments)

        if normalized_tool_id in DOCUMENT_WRITE_TOOLS:
            document_id = DocumentRequestAccessPolicy.resolve_document_id(
                operation=normalized_tool_id,
                requested_document_id=str(arguments.get("document_id") or "").strip() or None,
                context=request_context,
            )
            if document_id is None or not DocumentRequestAccessPolicy.operation_authorized(
                operation=normalized_tool_id,
                document_id=document_id,
                context=request_context,
            ):
                raise ToolArgumentCanonicalizationError("document_scope_denied")
            canonical: dict[str, Any] = {"document_id": document_id}
            if normalized_tool_id == "documents.queue_processing":
                tier = str(arguments.get("processing_tier") or "standard").strip().casefold()
                if tier not in {"standard", "review_fallback"}:
                    raise ToolArgumentCanonicalizationError("document_processing_tier_unsupported")
                if (
                    str(request_context.get("principal_kind") or "").strip().casefold()
                    == "discord_adapter"
                    and tier != "review_fallback"
                ):
                    raise ToolArgumentCanonicalizationError("document_scope_denied")
                canonical["processing_tier"] = tier
            elif normalized_tool_id == "documents.propose_metadata":
                field_name = str(arguments.get("field_name") or "").strip().casefold()
                value = " ".join(str(arguments.get("proposed_value") or "").split())
                if field_name not in {"safe_title", "archive_class", "filing_tag"} or not value:
                    raise ToolArgumentCanonicalizationError("document_metadata_proposal_invalid")
                canonical.update({"field_name": field_name, "proposed_value": value})
            elif normalized_tool_id == "documents.review_field":
                field_name = str(arguments.get("field_name") or "").strip().casefold()
                decision = str(arguments.get("decision") or "").strip().casefold()
                corrected_value = " ".join(str(arguments.get("corrected_value") or "").split())
                if not field_name or decision not in {"confirm", "correct"}:
                    raise ToolArgumentCanonicalizationError("document_field_review_invalid")
                if decision == "correct" and not corrected_value:
                    raise ToolArgumentCanonicalizationError("document_field_correction_missing")
                if decision == "confirm" and corrected_value:
                    raise ToolArgumentCanonicalizationError("document_field_confirmation_has_value")
                canonical.update({"field_name": field_name, "decision": decision})
                if corrected_value:
                    canonical["corrected_value"] = corrected_value
            return canonical

        if normalized_tool_id == "documents.upload_capability":
            if arguments or not DocumentRequestAccessPolicy.operation_authorized(
                operation=legacy_operation,
                document_id="",
                context=request_context,
            ):
                raise ToolArgumentCanonicalizationError("document_scope_denied")
            return {}
        if normalized_tool_id == "documents.search":
            if not DocumentRequestAccessPolicy.operation_authorized(
                operation=legacy_operation,
                document_id="",
                context=request_context,
            ):
                raise ToolArgumentCanonicalizationError("document_scope_denied")
            query = " ".join(str(arguments.get("query") or "").split())
            if not query:
                raise ToolArgumentCanonicalizationError("document_query_missing")
            return {"query": query, "limit": int(arguments.get("limit", 10))}
        if normalized_tool_id == "documents.list_reviews":
            if not DocumentRequestAccessPolicy.operation_authorized(
                operation=legacy_operation,
                document_id="",
                context=request_context,
            ):
                raise ToolArgumentCanonicalizationError("document_scope_denied")
            return {"limit": int(arguments.get("limit", 20))}

        requested = str(arguments.get("document_id") or "").strip() or None
        document_id = DocumentRequestAccessPolicy.resolve_document_id(
            operation=legacy_operation,
            requested_document_id=requested,
            context=request_context,
        )
        if document_id is None:
            code = "document_scope_denied" if requested else "document_id_missing_or_ambiguous"
            raise ToolArgumentCanonicalizationError(code)
        if not DocumentRequestAccessPolicy.operation_authorized(
            operation=legacy_operation,
            document_id=document_id,
            context=request_context,
        ):
            raise ToolArgumentCanonicalizationError("document_scope_denied")
        canonical: dict[str, Any] = {"document_id": document_id}
        if normalized_tool_id == "documents.inspect":
            canonical["limit"] = int(arguments.get("limit", 10))
            block_id = str(arguments.get("block_id") or "").strip()
            if block_id:
                canonical["block_id"] = block_id
            if arguments.get("page_number") is not None:
                canonical["page_number"] = int(arguments["page_number"])
        return canonical

    def execute_tool(
        self,
        *,
        envelope: ToolCallEnvelope,
        services: dict[str, Any],
    ) -> dict[str, Any]:
        del services
        if not isinstance(envelope, ToolCallEnvelope) or envelope.skill_id != self.SKILL_ID:
            return self._denied("documents_tool_envelope_invalid")
        legacy_operation = DOCUMENT_READ_TOOLS.get(envelope.tool_id)
        if legacy_operation is None and envelope.tool_id not in DOCUMENT_WRITE_TOOLS:
            return self._denied("documents_tool_unsupported")
        arguments = thaw_json(envelope.arguments)
        context = self._execution_context(envelope=envelope, arguments=arguments)
        document_id = str(arguments.get("document_id") or "").strip()
        if not DocumentRequestAccessPolicy.operation_authorized(
            operation=legacy_operation or envelope.tool_id,
            document_id=document_id,
            context=context,
        ):
            return self._denied("document_scope_denied")
        legacy = self._documents_service.execute(
            intent=legacy_operation or envelope.tool_id,
            entities=arguments,
            context=context,
        )
        if envelope.tool_id in DOCUMENT_WRITE_TOOLS:
            return self._project_write_result(
                tool_id=envelope.tool_id,
                result=legacy if isinstance(legacy, dict) else {},
                envelope=envelope,
            )
        return self._project_result(
            tool_id=envelope.tool_id,
            arguments=arguments,
            result=legacy if isinstance(legacy, dict) else {},
        )

    @staticmethod
    def _execution_context(
        *,
        envelope: ToolCallEnvelope,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "principal_kind": envelope.principal_kind,
            "principal_subject": envelope.principal_subject,
            "external_user_id": envelope.external_user_id,
            "requested_by_user_id": envelope.user_id,
            "agent_id": envelope.agent_id,
            "source": envelope.source_interface,
            "source_interface": envelope.source_interface,
            "request_id": envelope.root_request_id,
            "operation_id": envelope.operation_id,
            "arguments_hash": envelope.arguments_hash,
            "tool_id": envelope.tool_id,
        }
        if envelope.principal_kind == "discord_adapter":
            context["discord_channel_id"] = envelope.channel_scope
            document_id = str(arguments.get("document_id") or "").strip()
            if document_id:
                context["current_document_attachment_ids"] = [document_id]
        return context

    def _project_write_result(
        self,
        *,
        tool_id: str,
        result: dict[str, Any],
        envelope: ToolCallEnvelope,
    ) -> dict[str, Any]:
        raw_status = str(result.get("status") or "error").strip().casefold()
        status = {
            "ok": "ok",
            "queued": "queued",
            "processing": "queued",
            "needs_review": "ok",
            "clarify": "needs_input",
            "not_ready": "needs_input",
            "denied": "policy_denied",
            "unsupported": "policy_denied",
            "disabled": "policy_denied",
        }.get(raw_status, "error")
        document_id = self._text(result.get("document_id"), 128)
        payload: dict[str, Any] = {
            "document_id": document_id,
            "idempotent_replay": bool(result.get("idempotent_replay")),
        }
        for key in ("run_id", "job_id", "proposal_id", "review_id", "field_decision_id"):
            value = self._text(result.get(key), 128)
            if value:
                payload[key] = value
        for key in ("processing_tier", "field_name", "decision_kind"):
            value = self._text(result.get(key), 64)
            if value:
                payload[key] = value
        confirmed = result.get("confirmed_fields")
        if isinstance(confirmed, list):
            payload["confirmed_count"] = min(len(confirmed), 64)
        message = {
            "documents.queue_processing": "Queued the authorized document processing run.",
            "documents.propose_metadata": "Saved the metadata proposal for human review.",
            "documents.review_field": "Saved the authorized field review decision.",
            "documents.confirm_fields": "Saved the authorized field confirmations.",
        }[tool_id]
        response: dict[str, Any] = {"status": status, "message": message, "payload": payload}
        if status == "ok":
            response["receipt_id"] = "documents_receipt:" + envelope.operation_id
            response["committed_effect"] = not payload["idempotent_replay"]
        elif status == "queued":
            response["committed_effect"] = False
        elif status == "needs_input":
            response["missing_fields"] = ["document_id"] if not document_id else []
        elif status == "policy_denied":
            response["denial_reason"] = "document_scope_denied"
        return response

    def _project_result(
        self,
        *,
        tool_id: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        raw_status = str(result.get("status") or "error").strip().casefold()
        status = {
            "ok": "ok",
            "clarify": "needs_input",
            "denied": "policy_denied",
            "unsupported": "policy_denied",
            "disabled": "policy_denied",
        }.get(raw_status, "error")
        payload, untrusted = self._payload(
            tool_id=tool_id,
            arguments=arguments,
            result=result,
        )
        if tool_id == "documents.source_link" and status == "ok" and not payload["source_link"]:
            status = "error"
        message = {
            "documents.upload_capability": "The authenticated Documents upload control is available.",
            "documents.search": "Searched authorized Documents results.",
            "documents.status": "Read the authorized document status.",
            "documents.inspect": "Inspected the authorized document with bounded evidence.",
            "documents.source_link": "Resolved the authenticated document source link.",
            "documents.list_reviews": "Listed pending content-free document reviews.",
        }[tool_id]
        if status == "policy_denied":
            message = "This Documents read is not available in the current request context."
        elif status == "needs_input":
            message = "Which authorized document do you mean?"
        elif status == "error":
            message = "The local Documents service could not complete that read safely."
        response: dict[str, Any] = {
            "status": status,
            "message": message,
            "payload": payload,
        }
        if untrusted:
            response["untrusted"] = True
        if status == "needs_input" and tool_id in _DOCUMENT_ID_TOOLS:
            response["missing_fields"] = ["document_id"]
        return response

    def _payload(
        self,
        *,
        tool_id: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        if tool_id == "documents.upload_capability":
            accepted = [
                item
                for item in ("pdf", "jpeg", "png")
                if item in {str(value).strip().casefold() for value in result.get("accepted_formats") or []}
            ]
            return {
                "upload_path": "/documents" if result.get("upload_path") == "/documents" else "",
                "accepted_formats": accepted,
            }, False
        if tool_id == "documents.search":
            limit = max(1, min(int(arguments.get("limit", 10)), 20))
            raw_rows = result.get("documents") if isinstance(result.get("documents"), list) else []
            documents = [
                item
                for item in (self._search_hit(row) for row in raw_rows[:limit])
                if item is not None
            ]
            return {
                "query": self._text(arguments.get("query"), 200),
                "documents": documents,
                "truncated": bool(result.get("truncated")) or len(raw_rows) > limit,
                "untrusted": True,
            }, True
        if tool_id in {"documents.status", "documents.inspect"}:
            raw_document = result.get("document") if isinstance(result.get("document"), dict) else {}
            document = self._document_status(
                raw_document,
                fallback_id=str(arguments.get("document_id") or ""),
            )
            if tool_id == "documents.status":
                return {"document": document, "untrusted": True}, True
            return {
                "document": document,
                "evidence": self._evidence(result.get("evidence"), limit=int(arguments.get("limit", 10))),
                "structured_fields": self._fields(result),
                "untrusted": True,
            }, True
        if tool_id == "documents.source_link":
            raw_link = str(result.get("source_path") or "").strip()
            safe_link = raw_link if _SAFE_SOURCE_LINK.fullmatch(raw_link) else ""
            return {
                "document_id": self._text(
                    result.get("document_id") or arguments.get("document_id"),
                    128,
                ),
                "source_link": safe_link,
            }, False
        raw_reviews = result.get("reviews") if isinstance(result.get("reviews"), list) else []
        limit = max(1, min(int(arguments.get("limit", 20)), 20))
        reviews = [
            item
            for item in (self._review(row) for row in raw_reviews[:limit])
            if item is not None
        ]
        return {
            "reviews": reviews,
            "truncated": bool(result.get("truncated")) or len(raw_reviews) > limit,
        }, False

    @classmethod
    def _search_hit(cls, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        document_id = cls._text(value.get("document_id"), 128)
        if not document_id:
            return None
        result: dict[str, Any] = {
            "document_id": document_id,
            "title": cls._text(value.get("title"), 200),
            "snippet": cls._text(value.get("snippet"), 500),
            "sensitivity": cls._sensitivity(value.get("sensitivity")),
        }
        if isinstance(value.get("page_number"), int) and not isinstance(value.get("page_number"), bool):
            result["page_number"] = max(1, min(int(value["page_number"]), 1_000_000))
        block_id = cls._text(value.get("block_id"), 120)
        if block_id:
            result["block_id"] = block_id
        return result

    @classmethod
    def _document_status(cls, value: Any, *, fallback_id: str) -> dict[str, Any]:
        row = value if isinstance(value, dict) else {}
        result: dict[str, Any] = {
            "document_id": cls._text(row.get("document_id") or fallback_id, 128),
            "title": cls._text(row.get("title"), 200),
            "state": cls._text(row.get("state"), 40) or "unknown",
            "processing_state": cls._text(row.get("processing_state"), 40) or "unknown",
            "sensitivity": cls._sensitivity(row.get("sensitivity")),
            "source_available": row.get("source_available") is True,
        }
        document_class = cls._text(row.get("document_class"), 64)
        if document_class:
            result["document_class"] = document_class
        return result

    @classmethod
    def _evidence(cls, value: Any, *, limit: int) -> list[dict[str, Any]]:
        container = value if isinstance(value, dict) else {}
        rows = container.get("blocks")
        if not isinstance(rows, list):
            rows = container.get("evidence")
        if not isinstance(rows, list):
            return []
        bounded_limit = max(1, min(int(limit), 20))
        projected: list[dict[str, Any]] = []
        remaining_chars = 1_600
        for row in rows[:bounded_limit]:
            if not isinstance(row, dict) or remaining_chars <= 0:
                continue
            literal = cls._text(row.get("literal_text"), min(500, remaining_chars))
            if not literal:
                continue
            item: dict[str, Any] = {"literal_text": literal}
            block_id = cls._text(row.get("block_id"), 120)
            if block_id:
                item["block_id"] = block_id
            page_number = row.get("page_number")
            if isinstance(page_number, int) and not isinstance(page_number, bool):
                item["page_number"] = max(1, min(page_number, 1_000_000))
            projected.append(item)
            remaining_chars -= len(literal)
        return projected

    @classmethod
    def _fields(cls, result: dict[str, Any]) -> list[dict[str, Any]]:
        rows = result.get("structured_fields")
        if not isinstance(rows, list):
            rows = result.get("unverified_structured_fields")
        if not isinstance(rows, list):
            return []
        fields: list[dict[str, Any]] = []
        for row in rows[:64]:
            if not isinstance(row, dict):
                continue
            field_name = cls._text(row.get("field_name"), 64)
            raw_value = row.get("value")
            if not field_name or not isinstance(raw_value, (str, int, float, bool)):
                continue
            value = cls._text(raw_value, 500)
            if not value:
                continue
            try:
                confidence = max(0.0, min(float(row.get("confidence") or 0.0), 1.0))
            except (TypeError, ValueError):
                confidence = 0.0
            verification = cls._text(
                row.get("verification") or row.get("observation_state"),
                40,
            ) or "unverified"
            fields.append(
                {
                    "field_name": field_name,
                    "value": value,
                    "sensitivity": cls._sensitivity(row.get("sensitivity")),
                    "confidence": confidence,
                    "verification": verification,
                }
            )
        return fields

    @classmethod
    def _review(cls, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        review_id = cls._text(value.get("review_id"), 128)
        subject_type = cls._text(value.get("subject_type"), 80)
        if not review_id or not subject_type.startswith("document_"):
            return None
        return {
            "review_id": review_id,
            "subject_type": subject_type,
            "state": cls._text(value.get("state"), 40) or "pending",
            "sensitivity": cls._sensitivity(value.get("sensitivity")),
            "created_at": cls._text(value.get("created_at"), 64),
        }

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split())[: max(0, int(limit))]

    @staticmethod
    def _sensitivity(value: Any) -> str:
        normalized = str(value or "private").strip().casefold()
        return normalized if normalized in _SENSITIVITIES else "private"

    @staticmethod
    def _denied(reason: str) -> dict[str, Any]:
        return {
            "status": "policy_denied",
            "message": "This Documents read is not available in the current request context.",
            "denial_reason": reason,
        }


def run(
    *,
    intent: str,
    entities: dict[str, Any],
    services: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    service = services.get("documents_service")
    if service is None:
        return {
            "status": "disabled",
            "message": "The local Documents service is disabled.",
            "_persistence_policy": "restricted_read",
        }
    return service.execute(intent=intent, entities=entities, context=context)
