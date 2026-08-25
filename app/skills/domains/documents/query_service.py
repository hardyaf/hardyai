from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.reviews.service import HumanReviewService
from app.reviews.types import ReviewKind
from app.skills.domains.documents.ports import DocumentQueryPort


DOCUMENT_INTENTS = {
    "documents.ingest",
    "documents.status",
    "documents.find",
    "documents.get",
    "documents.show_source",
    "documents.reprocess",
    "documents.list_reviews",
    "documents.propose_metadata",
}


class DocumentQueryService:
    """Main-only facade over the isolated, content-bounded gateway client."""

    def __init__(
        self,
        *,
        gateway: DocumentQueryPort,
        reviews: HumanReviewService | None = None,
    ) -> None:
        self.gateway = gateway
        self.reviews = reviews

    @staticmethod
    def _authorized(context: dict[str, Any]) -> bool:
        principal_kind = str(context.get("principal_kind") or "").strip().casefold()
        source = str(context.get("source") or context.get("request_source") or "dashboard").casefold()
        return principal_kind in {"operator", "test"} and source in {
            "dashboard",
            "web",
            "test",
        }

    def capability_access(self, *, context: dict[str, Any]) -> dict[str, Any]:
        authorized = self._authorized(context)
        configured = bool(self.gateway)
        available = configured and authorized and self.gateway.ready()
        return {
            "configured": configured,
            "authorized_here": authorized,
            "availability": "available" if available else "restricted" if configured else "disabled",
            "access_note": (
                "Document search and controls are available in this operator session."
                if available
                else "Documents require an authenticated operator session and a ready local gateway."
            ),
        }

    def execute(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_intent = str(intent or "").strip().casefold()
        if normalized_intent not in DOCUMENT_INTENTS:
            return self._restricted({"status": "unsupported", "message": "Unsupported document action."})
        if not self._authorized(context):
            return self._restricted(
                {
                    "status": "denied",
                    "message": "Documents are available only in an authenticated operator session.",
                }
            )
        try:
            return self._restricted(
                self._execute_authorized(
                    intent=normalized_intent,
                    entities=entities,
                    context=context,
                )
            )
        except (RuntimeError, ValueError, KeyError, OSError):
            return self._restricted(
                {
                    "status": "error",
                    "message": "The local document service could not complete that request.",
                }
            )

    def _execute_authorized(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        document_id = str(entities.get("document_id") or entities.get("id") or "").strip()
        if intent == "documents.ingest":
            return {
                "status": "ok",
                "message": "Use the authenticated Documents upload control to add a PDF or image.",
                "upload_path": "/documents",
                "accepted_formats": ["pdf", "jpeg", "png"],
            }
        if intent == "documents.find":
            query = " ".join(str(entities.get("query") or "").split())[:200]
            if not query:
                return {"status": "clarify", "message": "What should I search for?"}
            value = self.gateway.find(query=query, limit=int(entities.get("limit") or 10))
            results = value.get("results") if isinstance(value.get("results"), list) else []
            return {
                "status": "ok",
                "message": f"Found {len(results)} matching document result(s).",
                "query": query,
                "documents": results[:20],
                "document_context_entities": self._context_entities(results),
            }
        if intent in {"documents.status", "documents.get"}:
            if not document_id:
                return {"status": "clarify", "message": "Which document do you mean?"}
            status = self.gateway.status(document_id)
            result: dict[str, Any] = {
                "status": "ok",
                "message": "Document status retrieved.",
                "document": status,
                "document_context_entities": self._context_entities([status]),
            }
            if intent == "documents.get" and status.get("processing_state") == "complete":
                result["evidence"] = self.gateway.evidence(document_id=document_id, limit=10)
            return result
        if intent == "documents.show_source":
            if not document_id:
                return {"status": "clarify", "message": "Which document source should I show?"}
            status = self.gateway.status(document_id)
            if not status.get("source_available"):
                return {"status": "not_ready", "message": "That source is not available yet."}
            return {
                "status": "ok",
                "message": "Open the authenticated source link to view the original document.",
                "document_id": document_id,
                "source_path": self.gateway.source_path(document_id),
                "document_context_entities": self._context_entities([status]),
            }
        if intent == "documents.reprocess":
            if not document_id:
                return {"status": "clarify", "message": "Which document should I reprocess?"}
            request_id = str(context.get("request_id") or uuid4())
            result = self.gateway.reprocess(document_id=document_id, idempotency_key=request_id)
            return {
                "status": "queued" if result.get("enqueue_confirmed") else "processing",
                "message": "A new immutable document-processing run was queued.",
                **result,
                "document_context_entities": self._context_entities([result]),
            }
        if intent == "documents.list_reviews":
            if self.reviews is None:
                return {"status": "disabled", "message": "Document review controls are unavailable."}
            rows = [
                row
                for row in self.reviews.list_pending(limit=50)
                if str(row.get("subject_type") or "").startswith("document_")
            ]
            return {
                "status": "ok",
                "message": f"There are {len(rows)} pending document review(s).",
                "reviews": rows[:20],
            }
        if intent == "documents.propose_metadata":
            if self.reviews is None:
                return {"status": "disabled", "message": "Document review controls are unavailable."}
            field_name = str(entities.get("field_name") or "").strip().casefold()
            proposed_value = " ".join(str(entities.get("proposed_value") or "").split())[:500]
            if not document_id or not field_name or not proposed_value:
                return {
                    "status": "clarify",
                    "message": "A document, metadata field, and proposed value are required.",
                }
            proposal = self.gateway.propose_metadata(
                document_id=document_id,
                field_name=field_name,
                proposed_value=proposed_value,
            )
            review = self.reviews.create_review(
                review_kind=ReviewKind.METADATA_PROPOSAL,
                subject_type="document_metadata_proposal",
                subject_id=str(proposal["proposal_id"]),
                subject_version=str(proposal["source_version_id"]),
                item_hash=str(proposal["value_hash"]),
                sensitivity=str(proposal["sensitivity"]),
                source_ref=document_id,
                validator_summary=[{"code": f"field:{field_name}", "passed": True}],
                target_operation="documents.apply_metadata",
            )
            self.gateway.bind_metadata_review(
                document_id=document_id,
                proposal_id=str(proposal["proposal_id"]),
                review_id=str(review["review_id"]),
            )
            return {
                "status": "needs_review",
                "message": "The metadata change was saved as a proposal for human review.",
                "document_id": document_id,
                "proposal_id": proposal["proposal_id"],
                "review_id": review["review_id"],
                "field_name": field_name,
                "document_context_entities": self._context_entities([{"document_id": document_id}]),
            }
        raise ValueError("unsupported document intent")

    @staticmethod
    def _context_entities(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        for row in rows[:20]:
            document_id = str(row.get("document_id") or "").strip()
            if not document_id:
                continue
            entities.append(
                {
                    "domain": "documents",
                    "entity_type": "document",
                    "entity_id": document_id,
                    "display_name": f"Document {document_id[:8]}",
                    "aliases": [],
                    "salience": 0.9,
                    "resolution_hints": {
                        "document_id": document_id,
                        "sensitivity": str(row.get("sensitivity") or "private")[:40],
                    },
                }
            )
        return entities

    @staticmethod
    def _restricted(result: dict[str, Any]) -> dict[str, Any]:
        result["_persistence_policy"] = "restricted_read"
        return result
