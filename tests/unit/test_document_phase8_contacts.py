from __future__ import annotations

import json
import sqlite3

from app.people.matching import match_contacts
from app.people.types import ContactCandidate
from app.reviews.repository import HumanReviewRepository
from app.reviews.service import HumanReviewService
from app.skills.domains.documents.contact_proposals import ContactProposalService
from app.skills.domains.documents.types import EvidenceRef, ExtractionResult, FieldObservation, Sensitivity
from tests.unit.test_document_phase7_proposals import _ready_run


def _extraction(*, name: str = "Jordan Lee", email: str = "jordan@example.com") -> ExtractionResult:
    evidence = (EvidenceRef(1, "card-line-1"),)
    return ExtractionResult(
        contract_version="document-extraction-v1",
        schema_name="BusinessCard",
        schema_version="1",
        extractor_name="synthetic",
        extractor_version="1",
        observations=(
            FieldObservation("full_name", name, name, Sensitivity.PRIVATE, 0.9, evidence),
            FieldObservation("organization", "Field Works", "Field Works", Sensitivity.PRIVATE, 0.9, evidence),
            FieldObservation("email", email, email, Sensitivity.PRIVATE, 0.99, evidence),
            FieldObservation("phone", "(919) 555-0142", "(919) 555-0142", Sensitivity.PRIVATE, 0.99, evidence),
        ),
    )


def test_contact_match_prefers_exact_email_and_phone_with_explanations() -> None:
    result = match_contacts(
        {
            "full_name": "Jordan Lee",
            "organization": "Field Works",
            "email": "JORDAN@example.com",
            "phone": "+1 919-555-0142",
        },
        (
            ContactCandidate("person-2", "Jordan Li", "Field Works"),
            ContactCandidate(
                "person-1",
                "Jordan Lee",
                "Field Works",
                emails=("jordan@example.com",),
                phones=("919.555.0142",),
            ),
        ),
    )

    assert result.selected_ref == "person-1"
    assert result.proposed_operation == "update"
    assert not result.ambiguous
    assert {"exact_email", "exact_phone"} <= set(result.candidates[0].reasons)


def test_contact_match_never_auto_selects_close_ambiguous_names() -> None:
    result = match_contacts(
        {"full_name": "Alex Morgan", "organization": "North Field"},
        (
            ContactCandidate("person-1", "Alex Morgan", "North Field LLC"),
            ContactCandidate("person-2", "Alex Morgan", "North Fields"),
        ),
    )

    assert result.selected_ref is None
    assert result.ambiguous
    assert result.proposed_operation == "select_or_create"


def test_missing_contact_provider_creates_clear_gated_proposal_without_contact_storage(tmp_path) -> None:
    repository, record, run = _ready_run(tmp_path)
    review_repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = ContactProposalService(
        repository=repository,
        reviews=HumanReviewService(review_repository),
        provider=None,
    )

    first = service.generate(
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        run_id=run["run_id"],
        extraction=_extraction(),
    )
    second = service.generate(
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        run_id=run["run_id"],
        extraction=_extraction(),
    )

    assert first.proposal is not None and second.proposal is not None
    assert first.proposal["proposal_id"] == second.proposal["proposal_id"]
    assert first.proposal["capability_status"] == "capability_unavailable"
    assert first.proposal["state"] == "capability_unavailable"
    assert first.proposal["candidate_matches"] == []
    assert first.review_id == second.review_id
    review = review_repository.get(str(first.review_id))
    assert review is not None and review["target_operation"] == "contacts.create_or_update"
    serialized = json.dumps(review, sort_keys=True)
    assert "jordan@example.com" not in serialized
    assert "(919) 555-0142" not in serialized
    assert "9195550142" not in serialized

    with sqlite3.connect(tmp_path / "documents.db") as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "contacts" not in tables and "people" not in tables
    review_repository.close()
    repository.close()


class _Provider:
    provider_name = "synthetic-address-book"

    def search(self, *, query: str, limit: int):
        assert query == "jordan@example.com" and limit == 20
        return (
            ContactCandidate(
                "person-1",
                "Jordan Lee",
                "Field Works",
                emails=("jordan@example.com",),
            ),
        )

    def upsert(self, **kwargs):  # pragma: no cover - proposal generation cannot execute
        raise AssertionError("contact proposal generation must not write")


def test_available_provider_is_read_only_and_yields_reviewed_update_candidate(tmp_path) -> None:
    repository, record, run = _ready_run(tmp_path)
    review_repository = HumanReviewRepository(str(tmp_path / "core.db"))
    result = ContactProposalService(
        repository=repository,
        reviews=HumanReviewService(review_repository),
        provider=_Provider(),
    ).generate(
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        run_id=run["run_id"],
        extraction=_extraction(),
    )

    assert result.proposal is not None
    assert result.proposal["capability_status"] == "available"
    assert result.proposal["proposed_operation"] == "update"
    assert result.proposal["selected_contact_ref"] == "person-1"
    assert result.proposal["state"] == "pending_review"
    assert result.proposal["candidate_matches"][0]["reasons"] == ["exact_email", "exact_name", "exact_organization"]
    review_repository.close()
    repository.close()
