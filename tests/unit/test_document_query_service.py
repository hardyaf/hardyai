from __future__ import annotations

import json

from app.core.persistence_policy import (
    most_restrictive_persistence_policy,
    persistence_policy,
    persistence_policy_for_intent,
)
from app.reviews.repository import HumanReviewRepository
from app.reviews.service import HumanReviewService
from app.skills.domains.documents.corrections import field_review_binding_hash
from app.skills.domains.documents.query_service import DocumentQueryService
from app.skills.domains.documents.review_corrections import DocumentFieldReviewCoordinator


class Gateway:
    def ready(self) -> bool:
        return True

    def find(self, *, query: str, limit: int):
        return {
            "results": [
                {
                    "document_id": "doc-private-1",
                    "title": "Secret Financial Statement",
                    "snippet": "account balance 12345",
                    "sensitivity": "financial",
                    "page_number": 2,
                    "block_id": "b7",
                }
            ]
        }

    def status(self, document_id: str):
        return {
            "document_id": document_id,
            "title": "Secret Financial Statement",
            "sensitivity": "financial",
            "processing_state": "complete",
            "source_available": True,
        }

    def evidence(self, **kwargs):
        return {"evidence": [{"literal_text": "account balance 12345", "block_id": "b7"}]}

    def fields(self, **kwargs):
        return {
            "fields": [
                {
                    "field_name": "account_identifier_masked",
                    "value": "****2345",
                    "evidence": [{"page_number": 2, "block_id": "b7"}],
                }
            ]
        }

    def classifications(self, **kwargs):
        return {"classifications": []}

    def reprocess(
        self,
        *,
        document_id: str,
        idempotency_key: str,
        processing_tier: str = "default",
    ):
        return {
            "document_id": document_id,
            "run_id": "run-2",
            "enqueue_confirmed": True,
            "processing_tier": processing_tier,
            "route": "vlm_fallback" if processing_tier == "review_fallback" else "conventional_ocr",
        }

    def processing_run(self, *, document_id: str, run_id: str):
        return {
            "document_id": document_id,
            "run_id": run_id,
            "status": "needs_review",
            "route": "vlm_fallback",
            "processing_tier": "review_fallback",
        }

    def propose_metadata(self, **kwargs):
        raise AssertionError("unused")

    def bind_metadata_review(self, **kwargs):
        raise AssertionError("unused")

    def source_path(self, document_id: str) -> str:
        return f"/documents/{document_id}/source"


def test_document_reads_require_operator_context_and_declare_restricted_persistence() -> None:
    service = DocumentQueryService(gateway=Gateway())
    denied = service.execute(
        intent="documents.find",
        entities={"query": "balance"},
        context={"principal_kind": "user", "source": "discord"},
    )
    assert denied["status"] == "denied"
    assert denied["_persistence_policy"] == "restricted_read"

    result = service.execute(
        intent="documents.find",
        entities={"query": "balance"},
        context={"principal_kind": "operator", "source": "web"},
    )
    assert result["status"] == "ok"
    assert result["_persistence_policy"] == "restricted_read"
    assert result["documents"][0]["snippet"] == "account balance 12345"
    context_entity = result["document_context_entities"][0]
    assert context_entity["entity_id"] == "doc-private-1"
    assert context_entity["display_name"] == "Document doc-priv"
    assert "Secret Financial" not in str(context_entity)
    assert "12345" not in str(context_entity)

    policy = persistence_policy(result["_persistence_policy"])
    assert policy.record_entity_context
    assert not policy.record_recent_turns
    assert not policy.record_conversation_history
    assert not policy.record_memory
    assert not policy.capture_ticket

    fallback_policy = most_restrictive_persistence_policy(
        persistence_policy_for_intent("documents.find"),
        "standard",
    )
    assert fallback_policy.name.value == "restricted_read"
    assert not fallback_policy.record_memory
    assert not fallback_policy.capture_ticket


def test_document_reprocess_is_main_operator_only_and_idempotency_bound_to_request() -> None:
    service = DocumentQueryService(gateway=Gateway())
    result = service.execute(
        intent="documents.reprocess",
        entities={"document_id": "doc-private-1"},
        context={"principal_kind": "operator", "source": "dashboard", "request_id": "request-9"},
    )
    assert result["status"] == "queued"
    assert result["run_id"] == "run-2"
    assert result["_persistence_policy"] == "restricted_read"


def test_discord_negative_ocr_feedback_escalates_only_the_recent_image() -> None:
    service = DocumentQueryService(gateway=Gateway())
    context = {
        "principal_kind": "discord_adapter",
        "source": "discord",
        "request_id": "discord:feedback-9",
        "document_attachment_ids": ["doc-private-1"],
    }

    result = service.execute(
        intent="documents.escalate_ocr",
        entities={"document_id": "doc-private-1"},
        context=context,
    )
    denied = service.execute(
        intent="documents.escalate_ocr",
        entities={"document_id": "doc-private-2"},
        context=context,
    )

    assert result["status"] == "queued"
    assert result["processing_tier"] == "review_fallback"
    assert result["route"] == "vlm_fallback"
    assert "first read was not good enough" in result["message"]
    assert "will post" in result["message"]
    assert result["async_followup"] == {
        "kind": "document_processing",
        "document_id": "doc-private-1",
        "operation_id": "run-2",
    }
    assert result["_persistence_policy"] == "restricted_read"
    assert denied["status"] == "denied"


def test_document_completion_can_poll_the_exact_escalated_run() -> None:
    service = DocumentQueryService(gateway=Gateway())
    status = service.processing_run_status(
        document_id="doc-private-1",
        run_id="run-2",
        context={
            "principal_kind": "discord_adapter",
            "source": "discord",
            "document_attachment_ids": ["doc-private-1"],
        },
    )

    assert status["run_id"] == "run-2"
    assert status["status"] == "needs_review"


def test_discord_reads_only_the_recent_scoped_attachment_id() -> None:
    service = DocumentQueryService(gateway=Gateway())
    context = {
        "principal_kind": "discord_adapter",
        "source": "discord",
        "document_attachment_ids": ["doc-private-1"],
    }
    allowed = service.execute(
        intent="documents.get",
        entities={"document_id": "doc-private-1"},
        context=context,
    )
    assert allowed["status"] == "ok"
    assert allowed["evidence"]["evidence"][0]["literal_text"] == "account balance 12345"
    assert allowed["message"] == "Here is the text I could read:\naccount balance 12345"

    wrong_id = service.execute(
        intent="documents.get",
        entities={"document_id": "doc-private-2"},
        context=context,
    )
    search = service.execute(
        intent="documents.find",
        entities={"query": "balance"},
        context=context,
    )
    assert wrong_id["status"] == "denied"
    assert search["status"] == "denied"


def test_review_required_attachment_reports_verification_hold_without_candidate_evidence() -> None:
    class NeedsReviewGateway(Gateway):
        def status(self, document_id: str):
            return {
                "document_id": document_id,
                "title": "Difficult phone image",
                "sensitivity": "private",
                "state": "ready",
                "processing_state": "needs_review",
                "source_available": True,
            }

        def evidence(self, **kwargs):
            raise AssertionError("unreviewed candidate evidence must remain behind the review gate")

    service = DocumentQueryService(gateway=NeedsReviewGateway())
    result = service.execute(
        intent="documents.get",
        entities={"document_id": "doc-review-1"},
        context={
            "principal_kind": "discord_adapter",
            "source": "discord",
            "document_attachment_ids": ["doc-review-1"],
        },
    )

    assert result["status"] == "ok"
    assert "produced a candidate result" in result["message"]
    assert "did not pass verification" in result["message"]
    assert "human review" in result["message"]
    assert "evidence" not in result


def test_review_required_business_card_returns_bounded_unverified_field_preview() -> None:
    class BusinessCardGateway(Gateway):
        def status(self, document_id: str):
            return {
                "document_id": document_id,
                "title": "Card image",
                "sensitivity": "private",
                "state": "ready",
                "processing_state": "needs_review",
                "document_class": "business_card",
                "source_available": True,
            }

        def evidence(self, **kwargs):
            raise AssertionError("unreviewed raw OCR evidence must remain behind the review gate")

        def fields(self, **kwargs):
            return {
                "fields": [
                    {
                        "field_name": "full_name",
                        "value": "Jordan Lee",
                        "confidence": 0.82,
                        "literal_text": "must not be projected separately",
                        "evidence": [{"page_number": 1, "block_id": "b1"}],
                        "decision_kind": None,
                    },
                    {
                        "field_name": "organization",
                        "value": "Field Works LLC",
                        "confidence": 0.86,
                        "decision_kind": None,
                    },
                    {
                        "field_name": "email",
                        "value": "jordan@example.test",
                        "confidence": 0.98,
                        "decision_kind": "confirm",
                    },
                    {
                        "field_name": "unsupported_private_field",
                        "value": "must not escape",
                        "confidence": 1.0,
                    },
                ]
            }

    service = DocumentQueryService(gateway=BusinessCardGateway())
    result = service.execute(
        intent="documents.get",
        entities={"document_id": "doc-card-1"},
        context={
            "principal_kind": "discord_adapter",
            "source": "discord",
            "document_attachment_ids": ["doc-card-1"],
        },
    )

    assert result["status"] == "ok"
    assert "I read this as a business card" in result["message"]
    assert "These values are unverified" in result["message"]
    assert "- Name: Jordan Lee" in result["message"]
    assert "- Organization: Field Works LLC" in result["message"]
    assert "- Email: jordan@example.test (confirmed)" in result["message"]
    assert "must not escape" not in str(result)
    assert "must not be projected separately" not in str(result)
    assert "evidence" not in result
    assert result["unverified_structured_fields"] == [
        {
            "field_name": "full_name",
            "label": "Name",
            "value": "Jordan Lee",
            "confidence": 0.82,
            "verification": "unverified",
        },
        {
            "field_name": "organization",
            "label": "Organization",
            "value": "Field Works LLC",
            "confidence": 0.86,
            "verification": "unverified",
        },
        {
            "field_name": "email",
            "label": "Email",
            "value": "jordan@example.test",
            "confidence": 0.98,
            "verification": "human_confirmed",
        },
    ]


def test_discord_can_correct_add_and_confirm_business_card_fields_without_core_pii(tmp_path) -> None:
    class CorrectableBusinessCardGateway(Gateway):
        source_version_id = "source-version-card-1"

        def __init__(self) -> None:
            self.rows = [
                {
                    "observation_id": "observation-name-1",
                    "field_name": "full_name",
                    "value": "Jordan Lee",
                    "literal_text": "Jordan Lee",
                    "sensitivity": "private",
                    "confidence": 0.91,
                    "evidence": [{"page_number": 1, "block_id": "b1"}],
                    "observation_state": "candidate",
                    "item_hash": "1" * 64,
                    "created_at": "2026-08-26T12:00:00+00:00",
                    "review_decision_id": None,
                    "decision_kind": None,
                },
                {
                    "observation_id": "observation-company-1",
                    "field_name": "organization",
                    "value": "Field Warks LLC",
                    "literal_text": "Field Warks LLC",
                    "sensitivity": "private",
                    "confidence": 0.72,
                    "evidence": [{"page_number": 1, "block_id": "b2"}],
                    "observation_state": "candidate",
                    "item_hash": "2" * 64,
                    "created_at": "2026-08-26T12:00:00+00:00",
                    "review_decision_id": None,
                    "decision_kind": None,
                },
            ]
            self.applied: list[dict[str, object]] = []

        def status(self, document_id: str):
            return {
                "document_id": document_id,
                "title": "Card image",
                "sensitivity": "private",
                "state": "ready",
                "processing_state": "needs_review",
                "document_class": "business_card",
                "source_version_id": self.source_version_id,
                "source_available": True,
            }

        def fields(self, **kwargs):
            document_id = str(kwargs["document_id"])
            projected = []
            for row in self.rows:
                value = dict(row)
                value["review_binding_hash"] = field_review_binding_hash(
                    document_id=document_id,
                    source_version_id=self.source_version_id,
                    field_name=str(value["field_name"]),
                    observation_id=(
                        str(value["observation_id"]) if value.get("observation_id") else None
                    ),
                    observation_item_hash=str(value["item_hash"]),
                    review_decision_id=(
                        str(value["review_decision_id"])
                        if value.get("review_decision_id")
                        else None
                    ),
                    effective_value=value["value"],
                )
                projected.append(value)
            return {"source_version_id": self.source_version_id, "fields": projected}

        def apply_field_decision(self, **kwargs):
            document_id = str(kwargs["document_id"])
            current_fields = self.fields(document_id=document_id)["fields"]
            current = next(
                (
                    row
                    for row in current_fields
                    if row["field_name"] == kwargs["field_name"]
                ),
                None,
            )
            assert kwargs["review_binding_hash"] == (
                current["review_binding_hash"]
                if current is not None
                else field_review_binding_hash(
                    document_id=document_id,
                    source_version_id=self.source_version_id,
                    field_name=str(kwargs["field_name"]),
                    observation_id=None,
                    observation_item_hash=None,
                    review_decision_id=None,
                    effective_value=None,
                )
            )
            if current is None:
                target = {
                    "observation_id": None,
                    "field_name": kwargs["field_name"],
                    "value": kwargs["corrected_value"],
                    "literal_text": "",
                    "sensitivity": "private",
                    "confidence": 1.0,
                    "evidence": [],
                    "observation_state": "human_corrected",
                    "item_hash": "3" * 64,
                    "created_at": "2026-08-26T12:01:00+00:00",
                    "review_decision_id": kwargs["review_decision_id"],
                    "decision_kind": kwargs["decision_kind"],
                }
                self.rows.append(target)
            else:
                target = next(row for row in self.rows if row["field_name"] == kwargs["field_name"])
                if kwargs["corrected_value"] is not None:
                    target["value"] = kwargs["corrected_value"]
                target["review_decision_id"] = kwargs["review_decision_id"]
                target["decision_kind"] = kwargs["decision_kind"]
            receipt = {
                "field_decision_id": f"field-decision-{len(self.applied) + 1}",
                "document_id": document_id,
                "source_version_id": self.source_version_id,
                "field_name": kwargs["field_name"],
                "review_decision_id": kwargs["review_decision_id"],
                "selected_observation_id": kwargs["observation_id"],
                "decision_kind": kwargs["decision_kind"],
            }
            self.applied.append(dict(receipt))
            return receipt

    gateway = CorrectableBusinessCardGateway()
    review_repository = HumanReviewRepository(str(tmp_path / "core.db"))
    review_service = HumanReviewService(review_repository)
    service = DocumentQueryService(
        gateway=gateway,
        reviews=review_service,
        field_reviews=DocumentFieldReviewCoordinator(
            gateway=gateway,
            reviews=review_service,
        ),
    )
    context = {
        "principal_kind": "discord_adapter",
        "source": "discord",
        "external_user_id": "42",
        "requested_by_user_id": "jordan",
        "request_id": "request-correct-company",
        "document_attachment_ids": ["doc-card-1"],
    }

    corrected = service.execute(
        intent="documents.correct_field",
        entities={"document_id": "doc-card-1", "field_name": "company", "corrected_value": "Field Works LLC"},
        context=context,
    )
    repeated = service.execute(
        intent="documents.correct_field",
        entities={"document_id": "doc-card-1", "field_name": "company", "corrected_value": "Field Works LLC"},
        context=context,
    )
    added = service.execute(
        intent="documents.correct_field",
        entities={"document_id": "doc-card-1", "field_name": "title", "corrected_value": "Field Director"},
        context={**context, "request_id": "request-add-title"},
    )
    confirmed = service.execute(
        intent="documents.confirm_fields",
        entities={"document_id": "doc-card-1"},
        context={**context, "request_id": "request-confirm-card"},
    )

    assert corrected["status"] == repeated["status"] == added["status"] == confirmed["status"] == "ok"
    assert len(gateway.applied) == 3
    effective = {row["field_name"]: row for row in gateway.rows}
    assert effective["organization"]["value"] == "Field Works LLC"
    assert effective["organization"]["decision_kind"] == "correct"
    assert effective["job_title"]["value"] == "Field Director"
    assert effective["job_title"]["decision_kind"] == "correct"
    assert effective["full_name"]["decision_kind"] == "confirm"
    assert "- Organization: Field Works LLC (corrected)" in confirmed["message"]
    assert "- Job title: Field Director (corrected)" in confirmed["message"]
    assert "- Name: Jordan Lee (confirmed)" in confirmed["message"]

    review_items = review_repository.list_items(limit=20)
    review_decisions = [
        review_repository.latest_decision(str(item["review_id"]))
        for item in review_items
    ]
    core_ledger = json.dumps(
        {"items": review_items, "decisions": review_decisions},
        ensure_ascii=True,
        sort_keys=True,
    )
    assert "Field Works LLC" not in core_ledger
    assert "Field Director" not in core_ledger
    assert "Jordan Lee" not in core_ledger
    assert all(
        str(decision.get("actor_principal")) == "discord:42"
        for decision in review_decisions
        if isinstance(decision, dict)
    )
    review_repository.close()
