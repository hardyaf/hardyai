from __future__ import annotations

import json
import sqlite3

import pytest

from app.core.action_execution import DirectActionOutcome
from app.provenance.repository import ProvenanceRepository
from app.reviews.repository import HumanReviewRepository
from app.reviews.service import HumanReviewService
from app.reviews.types import ReviewDecisionKind
from app.services.document_proposal_execution_service import DocumentProposalExecutionService
from app.skills.domains.documents.ingestion import TransientDocumentSpool
from app.skills.domains.documents.note_proposals import NoteProposalService
from app.skills.domains.documents.storage import DocumentRepository
from app.skills.domains.documents.types import NormalizedBlock, ProcessingRoute


PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


def _ready_run(tmp_path):
    repository = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=4096, quota_bytes=16384)
    writer = spool.begin(filename="notes.pdf", declared_media_type="application/pdf", title="Notes")
    writer.write(PDF)
    record, _ = repository.create_or_get(owner_id="operator", staged=writer.finish())
    repository.mark_archiving(document_id=record.document_id, task_ref="archive-task")
    record = repository.mark_ready(
        document_id=record.document_id,
        provider="paperless",
        external_id="73",
        verified_sha256=record.sha256,
    )
    run = repository.create_processing_run(
        document_id=record.document_id,
        route=ProcessingRoute.CONVENTIONAL_OCR,
        parser_name="synthetic",
        parser_version="1",
        parser_image_digest="sha256:" + "1" * 64,
        configuration_sha256="2" * 64,
        resource_lane="cpu_ocr",
        request_key="phase7:one",
    )
    repository.begin_processing_run(run_id=run["run_id"], fencing_token=1)
    return repository, record, run


def _blocks(*lines: str) -> tuple[NormalizedBlock, ...]:
    return tuple(
        NormalizedBlock(
            block_id=f"b{index}",
            page_number=index,
            kind="paragraph",
            reading_order=index,
            text=line,
            bbox=None,
            char_span=None,
            provider_ref=f"#/blocks/{index}",
            confidence=0.95,
        )
        for index, line in enumerate(lines, start=1)
    )


def _proposal_services(tmp_path):
    repository, record, run = _ready_run(tmp_path)
    reviews_repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = NoteProposalService(
        repository=repository,
        reviews=HumanReviewService(reviews_repository),
    )
    return repository, reviews_repository, service, record, run


def test_note_proposals_preserve_evidence_and_abstain_on_ambiguous_date(tmp_path) -> None:
    repository, reviews, service, record, run = _proposal_services(tmp_path)
    result = service.generate(
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        run_id=run["run_id"],
        blocks=_blocks(
            "Action: Paint south field due 09/01/2026",
            "TODO: Call referee due tomorrow owner: Alex",
            "Remember: East field opens at 8 AM.",
        ),
    )

    assert len(result.action_proposals) == 2
    explicit = next(item for item in result.action_proposals if item["action_text"] == "Paint south field")
    ambiguous = next(item for item in result.action_proposals if item["action_text"] == "Call referee")
    assert explicit["normalized_due_date"] == "2026-09-01"
    assert explicit["evidence"] == [{"page_number": 1, "block_id": "b1"}]
    assert ambiguous["due_text"] == "tomorrow"
    assert ambiguous["normalized_due_date"] is None
    assert ambiguous["assignee_candidate"] == "Alex"
    assert ambiguous["confidence"] <= 0.65
    assert len(result.memory_proposals) == 1
    assert result.memory_proposals[0]["state"] == "capability_unavailable"

    review = reviews.get(str(explicit["review_id"]))
    assert review is not None and review["target_operation"] == "lists.add_item"
    serialized = json.dumps(review, sort_keys=True)
    assert "Paint south field" not in serialized
    assert "East field opens" not in serialized

    with sqlite3.connect(tmp_path / "core.db") as connection:
        assert connection.execute("SELECT count(*) FROM memory_entries").fetchone()[0] == 0
    reviews.close()
    repository.close()


def test_note_proposal_generation_is_idempotent_and_restricted_values_are_skipped(tmp_path) -> None:
    repository, reviews, service, record, run = _proposal_services(tmp_path)
    blocks = _blocks(
        "Action: File permit application",
        "Action: Verify account number 123456789012",
        "Remember: Passport number X12345678",
    )
    first = service.generate(
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        run_id=run["run_id"],
        blocks=blocks,
    )
    second = service.generate(
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        run_id=run["run_id"],
        blocks=blocks,
    )

    assert len(first.action_proposals) == len(second.action_proposals) == 1
    assert first.action_proposals[0]["proposal_id"] == second.action_proposals[0]["proposal_id"]
    assert first.action_proposals[0]["review_id"] == second.action_proposals[0]["review_id"]
    assert first.memory_proposals == second.memory_proposals == ()
    assert len(reviews.list_items(subject_type="document_action_proposal")) == 1
    reviews.close()
    repository.close()


class _Gateway:
    def __init__(self, repository: DocumentRepository) -> None:
        self.repository = repository

    def action_proposal(self, *, proposal_id: str):
        value = self.repository.get_action_proposal(proposal_id=proposal_id)
        if value is None:
            raise KeyError(proposal_id)
        return value

    def bind_action_execution(self, **kwargs):
        return self.repository.mark_action_proposal_executed(**kwargs)


class _Actions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute_direct(self, **kwargs):
        self.calls.append(kwargs)
        return DirectActionOutcome(
            authorized=True,
            response={
                "status": "ok",
                "item_id": "list-item-1",
                "request_id": kwargs["request_id"],
                "ticket": {"ticket_id": "ticket-1"},
            },
        )


def test_approved_action_executes_once_through_canonical_path_and_links_source(tmp_path) -> None:
    repository, reviews, proposals, record, run = _proposal_services(tmp_path)
    generated = proposals.generate(
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        run_id=run["run_id"],
        blocks=_blocks("Action: Paint south field due 2026-09-01"),
    )
    proposal = generated.action_proposals[0]
    decision = HumanReviewService(reviews).decide(
        review_id=str(proposal["review_id"]),
        bound_item_hash=str(proposal["item_hash"]),
        decision=ReviewDecisionKind.APPROVE,
        actor_principal="operator",
        reason="Confirmed against the source page",
        idempotency_key="phase7-decision-1",
    )
    actions = _Actions()
    provenance = ProvenanceRepository(str(tmp_path / "core.db"))
    executor = DocumentProposalExecutionService(
        gateway=_Gateway(repository),
        reviews=reviews,
        actions=actions,
        provenance=provenance,
    )

    first = executor.execute_action_proposal(
        review_id=str(proposal["review_id"]),
        proposal_id=str(proposal["proposal_id"]),
        decision_id=str(decision["decision_id"]),
        operation_id="phase7-execute-1",
    )
    second = executor.execute_action_proposal(
        review_id=str(proposal["review_id"]),
        proposal_id=str(proposal["proposal_id"]),
        decision_id=str(decision["decision_id"]),
        operation_id="phase7-execute-1",
    )

    assert first["target_item_ref"] == second["target_item_ref"] == "list-item-1"
    assert second["idempotent_replay"] is True
    assert len(actions.calls) == 1
    assert actions.calls[0]["entities"] == {
        "list_name": "to-do",
        "item_text": "Paint south field",
    }
    links = provenance.for_target(
        target_domain="lists",
        target_type="list_item",
        target_ref="list-item-1",
    )
    assert len(links) == 1
    assert links[0]["source_ref"] == proposal["proposal_id"]
    assert repository.get_action_proposal(proposal_id=proposal["proposal_id"])["state"] == "executed"
    assert reviews.get(str(proposal["review_id"]))["state"] == "executed"
    provenance.close()
    reviews.close()
    repository.close()


def test_rejected_action_cannot_execute(tmp_path) -> None:
    repository, reviews, proposals, record, run = _proposal_services(tmp_path)
    proposal = proposals.generate(
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        run_id=run["run_id"],
        blocks=_blocks("Action: Paint south field"),
    ).action_proposals[0]
    decision = HumanReviewService(reviews).decide(
        review_id=str(proposal["review_id"]),
        bound_item_hash=str(proposal["item_hash"]),
        decision=ReviewDecisionKind.REJECT,
        actor_principal="operator",
        reason="Not a real action item",
        idempotency_key="phase7-decision-reject",
    )
    provenance = ProvenanceRepository(str(tmp_path / "core.db"))
    executor = DocumentProposalExecutionService(
        gateway=_Gateway(repository),
        reviews=reviews,
        actions=_Actions(),
        provenance=provenance,
    )

    with pytest.raises(ValueError, match="not approved|rejected"):
        executor.execute_action_proposal(
            review_id=str(proposal["review_id"]),
            proposal_id=str(proposal["proposal_id"]),
            decision_id=str(decision["decision_id"]),
            operation_id="phase7-execute-rejected",
        )
    provenance.close()
    reviews.close()
    repository.close()
