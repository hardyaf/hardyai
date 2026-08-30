from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.reviews.service import HumanReviewService
from app.reviews.types import ReviewKind
from app.skills.domains.documents.ports import DocumentQueryPort
from app.skills.domains.documents.review_corrections import DocumentFieldReviewCoordinator
from app.skills.domains.documents.schemas import validate_field_correction


DOCUMENT_INTENTS = {
    "documents.ingest",
    "documents.status",
    "documents.find",
    "documents.get",
    "documents.show_source",
    "documents.reprocess",
    "documents.escalate_ocr",
    "documents.list_reviews",
    "documents.propose_metadata",
    "documents.correct_field",
    "documents.confirm_fields",
}

DOCUMENT_INTENT_CONTRACTS = (
    {
        "intent": "documents.escalate_ocr",
        "purpose": (
            "Escalate a recent image to the deeper local review-only OCR tier when the user says "
            "the prior read, extraction, or OCR result was wrong, poor, incomplete, or missed text. "
            "Use field correction instead when the user supplies the exact replacement value."
        ),
        "operation": "write",
        "entity_fields": ["document_id"],
    },
    {
        "intent": "documents.correct_field",
        "purpose": (
            "Correct one named field on an identified document, including a missing business-card "
            "field the user supplies."
        ),
        "operation": "write",
        "entity_fields": ["document_id", "field_name", "corrected_value"],
    },
    {
        "intent": "documents.confirm_fields",
        "purpose": "Confirm all currently extracted fields on one identified document as accurate.",
        "operation": "write",
        "entity_fields": ["document_id"],
    },
)

_BUSINESS_CARD_FIELD_LABELS = {
    "full_name": "Name",
    "organization": "Organization",
    "job_title": "Job title",
    "email": "Email",
    "phone": "Phone",
    "website": "Website",
}
_BUSINESS_CARD_FIELD_ORDER = tuple(_BUSINESS_CARD_FIELD_LABELS)


class DocumentQueryService:
    """Main-only facade over the isolated, content-bounded gateway client."""

    def __init__(
        self,
        *,
        gateway: DocumentQueryPort,
        reviews: HumanReviewService | None = None,
        field_reviews: DocumentFieldReviewCoordinator | None = None,
    ) -> None:
        self.gateway = gateway
        self.reviews = reviews
        self.field_reviews = field_reviews

    @staticmethod
    def _authorized(context: dict[str, Any]) -> bool:
        principal_kind = str(context.get("principal_kind") or "").strip().casefold()
        source = str(context.get("source") or context.get("request_source") or "dashboard").casefold()
        operator_authorized = principal_kind in {"operator", "test"} and source in {
            "dashboard",
            "web",
            "test",
        }
        discord_ids = DocumentQueryService._discord_document_ids(context)
        return operator_authorized or (
            principal_kind == "discord_adapter" and source == "discord" and bool(discord_ids)
        )

    @staticmethod
    def _discord_document_ids(context: dict[str, Any]) -> frozenset[str]:
        raw = context.get("document_attachment_ids")
        if not isinstance(raw, list):
            return frozenset()
        return frozenset(
            str(item).strip()
            for item in raw[:4]
            if isinstance(item, str) and str(item).strip()
        )

    @classmethod
    def _intent_authorized(
        cls,
        *,
        intent: str,
        document_id: str,
        context: dict[str, Any],
    ) -> bool:
        principal_kind = str(context.get("principal_kind") or "").strip().casefold()
        if principal_kind != "discord_adapter":
            return cls._authorized(context)
        return (
            intent
            in {
                "documents.status",
                "documents.get",
                "documents.escalate_ocr",
                "documents.correct_field",
                "documents.confirm_fields",
            }
            and bool(document_id)
            and document_id in cls._discord_document_ids(context)
        )

    def capability_access(self, *, context: dict[str, Any]) -> dict[str, Any]:
        authorized = self._authorized(context)
        configured = bool(self.gateway)
        available = configured and authorized and self.gateway.ready()
        discord_scoped = str(context.get("principal_kind") or "").strip().casefold() == "discord_adapter"
        main_intents = (
            {
                "documents.status",
                "documents.get",
                "documents.escalate_ocr",
                "documents.correct_field",
                "documents.confirm_fields",
            }
            if discord_scoped
            else DOCUMENT_INTENTS
        )
        if self.field_reviews is None:
            main_intents = main_intents - {
                "documents.correct_field",
                "documents.confirm_fields",
            }
        return {
            "configured": configured,
            "authorized_here": authorized,
            "availability": "available" if available else "restricted" if configured else "disabled",
            "access_note": (
                (
                    "The recent Discord attachment can be read in this user/channel context."
                    if discord_scoped
                    else "Document search and controls are available in this operator session."
                )
                if available
                else "Documents require an authenticated operator session and a ready local gateway."
            ),
            "main_intents": sorted(main_intents),
            "intent_contracts": [dict(item) for item in DOCUMENT_INTENT_CONTRACTS],
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
        document_id = str(entities.get("document_id") or entities.get("id") or "").strip()
        if not self._intent_authorized(
            intent=normalized_intent,
            document_id=document_id,
            context=context,
        ):
            return self._restricted(
                {
                    "status": "denied",
                    "message": (
                        "Discord can read only a recent attachment from this user and channel; "
                        "document controls require an authenticated operator session."
                    ),
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

    def processing_run_status(
        self,
        *,
        document_id: str,
        run_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Return content-free run state for an authorized completion subscriber."""

        if not self._intent_authorized(
            intent="documents.escalate_ocr",
            document_id=str(document_id),
            context=context,
        ):
            raise PermissionError("document_processing_run_denied")
        return self.gateway.processing_run(document_id=str(document_id), run_id=str(run_id))

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
                "message": self._status_message(status=status, include_text=intent == "documents.get"),
                "document": status,
                "document_context_entities": self._context_entities([status]),
            }
            if intent == "documents.get" and status.get("processing_state") == "complete":
                evidence = self.gateway.evidence(document_id=document_id, limit=10)
                result["evidence"] = evidence
                fields = self.gateway.fields(document_id=document_id)
                result["structured_fields"] = (
                    fields.get("fields") if isinstance(fields.get("fields"), list) else []
                )[:64]
                readable_text = self._bounded_evidence_text(evidence)
                result["message"] = (
                    f"Here is the text I could read:\n{readable_text}"
                    if readable_text
                    else "OCR completed, but it did not return any readable text."
                )
            elif (
                intent == "documents.get"
                and status.get("processing_state") == "needs_review"
                and str(status.get("document_class") or "").strip().casefold() == "business_card"
            ):
                preview = self._business_card_review_preview(document_id=document_id)
                if preview:
                    result["unverified_structured_fields"] = preview
                    result["message"] = self._business_card_review_message(preview)
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
        if intent == "documents.escalate_ocr":
            if not document_id:
                return {
                    "status": "clarify",
                    "message": "Which recent image should I run through the deeper OCR pass?",
                }
            request_id = str(context.get("request_id") or uuid4())
            result = self.gateway.reprocess(
                document_id=document_id,
                idempotency_key=request_id,
                processing_tier="review_fallback",
            )
            return {
                "status": "queued" if result.get("enqueue_confirmed") else "processing",
                "message": (
                    "I got it - the first read was not good enough. I queued a deeper local OCR "
                    "pass on the GPU and will post the review-only result here when it finishes."
                ),
                **result,
                "async_followup": {
                    "kind": "document_processing",
                    "document_id": document_id,
                    "operation_id": str(result.get("run_id") or ""),
                },
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
        if intent == "documents.correct_field":
            return self._correct_field(
                document_id=document_id,
                entities=entities,
                context=context,
            )
        if intent == "documents.confirm_fields":
            return self._confirm_fields(document_id=document_id, context=context)
        raise ValueError("unsupported document intent")

    def _correct_field(
        self,
        *,
        document_id: str,
        entities: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if self.field_reviews is None:
            return {"status": "disabled", "message": "Document correction controls are unavailable."}
        field_name = self._canonical_field_name(entities.get("field_name"))
        raw_value = str(entities.get("corrected_value") or "")
        if not document_id or not field_name or not raw_value.strip():
            return {
                "status": "clarify",
                "message": "A document, field name, and corrected value are required.",
            }
        status = self.gateway.status(document_id)
        denied = self._discord_mutation_denied(status=status, context=context)
        if denied is not None:
            return denied
        document_class = str(status.get("document_class") or "").strip().casefold()
        try:
            corrected_value = validate_field_correction(
                document_class=document_class,
                field_name=field_name,
                value=raw_value,
            )
        except ValueError:
            return {
                "status": "clarify",
                "message": (
                    "I couldn't apply that field correction. For business cards, use name, "
                    "organization, job title, email, phone, or website."
                ),
            }
        fields_response, rows = self._field_rows(document_id=document_id)
        current = self._field_row(rows=rows, field_name=field_name)
        if (
            current is not None
            and str(current.get("value") or "") == corrected_value
            and str(current.get("decision_kind") or "").strip().casefold()
            in {"confirm", "correct"}
        ):
            preview = self._business_card_review_preview(document_id=document_id)
            label = _BUSINESS_CARD_FIELD_LABELS.get(
                field_name,
                field_name.replace("_", " ").title(),
            )
            return {
                "status": "ok",
                "message": f"That {label} value is already saved.\n{self._business_card_review_message(preview)}",
                "document_id": document_id,
                "field_name": field_name,
                "decision_kind": str(current.get("decision_kind")),
                "unverified_structured_fields": preview,
                "document_context_entities": self._context_entities([status]),
            }
        decision_kind = "correct"
        applied_value: str | None = corrected_value
        if current is not None and str(current.get("value") or "") == corrected_value:
            decision_kind = "confirm"
            applied_value = None
        decision = self.field_reviews.apply(
            document_id=document_id,
            document_class=document_class,
            fields_response=fields_response,
            current=current,
            field_name=field_name,
            decision_kind=decision_kind,
            corrected_value=applied_value,
            context=context,
        )
        preview = self._business_card_review_preview(document_id=document_id)
        label = _BUSINESS_CARD_FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())
        verb = "confirmed" if decision_kind == "confirm" else "corrected"
        message = f"I saved your {verb} value for {label}."
        if preview:
            message = f"{message}\n{self._business_card_review_message(preview)}"
        return {
            "status": "ok",
            "message": message,
            "document_id": document_id,
            "field_name": field_name,
            "decision_kind": decision_kind,
            "field_decision_id": decision["field_decision_id"],
            "unverified_structured_fields": preview,
            "document_context_entities": self._context_entities([status]),
        }

    def _confirm_fields(self, *, document_id: str, context: dict[str, Any]) -> dict[str, Any]:
        if self.field_reviews is None:
            return {"status": "disabled", "message": "Document correction controls are unavailable."}
        if not document_id:
            return {"status": "clarify", "message": "Which document should I confirm?"}
        status = self.gateway.status(document_id)
        denied = self._discord_mutation_denied(status=status, context=context)
        if denied is not None:
            return denied
        document_class = str(status.get("document_class") or "").strip().casefold()
        fields_response, rows = self._field_rows(document_id=document_id)
        candidates = [
            row
            for row in rows
            if str(row.get("decision_kind") or "").strip().casefold()
            not in {"confirm", "correct"}
        ]
        if not rows:
            return {"status": "not_ready", "message": "There are no extracted fields to confirm."}
        confirmed: list[str] = []
        for row in candidates[:64]:
            field_name = self._canonical_field_name(row.get("field_name"))
            if not field_name:
                continue
            self.field_reviews.apply(
                document_id=document_id,
                document_class=document_class,
                fields_response=fields_response,
                current=row,
                field_name=field_name,
                decision_kind="confirm",
                corrected_value=None,
                context=context,
            )
            confirmed.append(field_name)
        preview = self._business_card_review_preview(document_id=document_id)
        message = (
            f"I confirmed {len(confirmed)} extracted field(s)."
            if confirmed
            else "All extracted fields were already confirmed or corrected."
        )
        if preview:
            message = f"{message}\n{self._business_card_review_message(preview)}"
        return {
            "status": "ok",
            "message": message,
            "document_id": document_id,
            "confirmed_fields": confirmed,
            "unverified_structured_fields": preview,
            "document_context_entities": self._context_entities([status]),
        }

    def _field_rows(self, *, document_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        response = self.gateway.fields(document_id=document_id)
        if not isinstance(response, dict):
            raise ValueError("document fields response is invalid")
        raw_rows = response.get("fields")
        rows = [dict(row) for row in raw_rows[:64] if isinstance(row, dict)] if isinstance(raw_rows, list) else []
        return response, rows

    @staticmethod
    def _field_row(*, rows: list[dict[str, Any]], field_name: str) -> dict[str, Any] | None:
        return next(
            (
                row
                for row in rows
                if str(row.get("field_name") or "").strip().casefold() == field_name
            ),
            None,
        )

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
            "organization": "organization",
            "organisation": "organization",
            "title": "job_title",
            "role": "job_title",
            "job title": "job_title",
            "email": "email",
            "email address": "email",
            "phone": "phone",
            "phone number": "phone",
            "telephone": "phone",
            "website": "website",
            "web site": "website",
            "site": "website",
            "url": "website",
        }
        return aliases.get(normalized, normalized.replace(" ", "_"))

    @staticmethod
    def _discord_mutation_denied(
        *,
        status: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        if str(context.get("principal_kind") or "").strip().casefold() != "discord_adapter":
            return None
        if str(status.get("document_class") or "").strip().casefold() == "business_card":
            return None
        return {
            "status": "denied",
            "message": (
                "Conversational field correction in Discord is currently limited to the recent "
                "business-card attachment."
            ),
        }

    @staticmethod
    def _status_message(*, status: dict[str, Any], include_text: bool) -> str:
        processing_state = str(status.get("processing_state") or "").strip().casefold()
        document_state = str(status.get("state") or "").strip().casefold()
        if processing_state == "needs_review":
            return (
                "I received the attachment and OCR produced a candidate result, but the result did "
                "not pass verification. It needs human review before being treated as verified text."
                if include_text
                else "Document processing finished and needs human review."
            )
        if processing_state == "processing_incomplete":
            return (
                "I received the attachment, but processing ended without reliable text. It needs "
                "human review."
                if include_text
                else "Document processing ended incomplete and needs human review."
            )
        if processing_state == "cancelled":
            return "Document processing was cancelled; the retained source can be reprocessed."
        if processing_state == "protected_pending":
            return (
                "I received the attachment and classified it as protected. Exact contents were not "
                "placed in ordinary OCR results or search; protected review remains disabled until "
                "the restricted workflow is certified."
            )
        if processing_state == "failed" or document_state == "failed":
            return "Document processing failed; the source was retained for retry or review."
        if processing_state != "complete":
            return (
                "The attachment is still being archived or processed. Ask me again shortly."
                if include_text
                else "Document processing is still in progress."
            )
        return "Document processing is complete."

    def _business_card_review_preview(self, *, document_id: str) -> list[dict[str, Any]]:
        """Return only bounded contact candidates; raw OCR evidence remains review-gated."""

        try:
            response = self.gateway.fields(document_id=document_id)
        except (RuntimeError, ValueError, KeyError, OSError):
            return []
        rows = response.get("fields")
        if not isinstance(rows, list):
            return []
        selected: dict[str, dict[str, Any]] = {}
        for row in rows[:64]:
            if not isinstance(row, dict):
                continue
            field_name = str(row.get("field_name") or "").strip().casefold()
            if field_name not in _BUSINESS_CARD_FIELD_LABELS or field_name in selected:
                continue
            raw_value = row.get("value")
            if not isinstance(raw_value, str):
                continue
            value = " ".join(raw_value.split())[:240]
            if not value:
                continue
            try:
                confidence = max(0.0, min(float(row.get("confidence") or 0.0), 1.0))
            except (TypeError, ValueError):
                confidence = 0.0
            decision_kind = str(row.get("decision_kind") or "").strip().casefold()
            selected[field_name] = {
                "field_name": field_name,
                "label": _BUSINESS_CARD_FIELD_LABELS[field_name],
                "value": value,
                "confidence": confidence,
                "verification": {
                    "confirm": "human_confirmed",
                    "correct": "human_corrected",
                }.get(decision_kind, "unverified"),
            }
        return [selected[name] for name in _BUSINESS_CARD_FIELD_ORDER if name in selected]

    @staticmethod
    def _business_card_review_message(fields: list[dict[str, Any]]) -> str:
        has_unverified = any(
            str(field.get("verification") or "unverified") == "unverified"
            for field in fields
        )
        lines = ["I read this as a business card and extracted candidate contact details."]
        lines.append(
            "These values are unverified and need to be checked against the image:"
            if has_unverified
            else "All shown values have been human-confirmed or corrected:"
        )
        for field in fields[: len(_BUSINESS_CARD_FIELD_ORDER)]:
            label = str(field.get("label") or "Field")
            value = str(field.get("value") or "")
            verification = str(field.get("verification") or "unverified")
            suffix = {
                "human_confirmed": " (confirmed)",
                "human_corrected": " (corrected)",
            }.get(verification, "")
            lines.append(f"- {label}: {value}{suffix}")
        lines.append("This preview did not create or update a contact.")
        return "\n".join(lines)

    @staticmethod
    def _bounded_evidence_text(evidence: dict[str, Any], *, max_chars: int = 1600) -> str:
        rows = evidence.get("blocks")
        if not isinstance(rows, list):
            rows = evidence.get("evidence")
        if not isinstance(rows, list):
            return ""
        parts: list[str] = []
        seen: set[str] = set()
        for row in rows[:20]:
            if not isinstance(row, dict):
                continue
            literal = " ".join(str(row.get("literal_text") or "").split())
            if not literal or literal in seen:
                continue
            seen.add(literal)
            projected = "\n".join([*parts, literal]) if parts else literal
            if len(projected) > max_chars:
                existing_chars = len("\n".join(parts))
                remaining = max_chars - existing_chars - (1 if parts else 0)
                if remaining > 3:
                    parts.append(f"{literal[: remaining - 3]}...")
                break
            parts.append(literal)
        return "\n".join(parts)

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
