from __future__ import annotations

from dataclasses import asdict

import pytest

from app.skills.domains.documents.classification import DeterministicDocumentClassifier
from app.skills.domains.documents.artifacts import ContentAddressedArtifactStore
from app.skills.domains.documents.enrichment import DocumentEnrichmentService
from app.skills.domains.documents.extraction import DeterministicStructuredExtractor
from app.skills.domains.documents.ingestion import TransientDocumentSpool
from app.skills.domains.documents.metadata import DocumentMetadataSyncService
from app.skills.domains.documents.ports import ArchiveMetadataSnapshot
from app.skills.domains.documents.ports import ParserOperation, ParserSubmission
from app.skills.domains.documents.processing import DocumentProcessingPending, DocumentProcessingService
from app.skills.domains.documents.redaction import contains_unmasked_restricted_value, redact_text
from app.skills.domains.documents.schemas import SCHEMAS, TAXONOMY_VERSION, validate_extraction
from app.skills.domains.documents.storage import DocumentRepository
from app.skills.domains.documents.types import (
    DocumentArtifact,
    DocumentClass,
    EvidenceRef,
    ExtractionResult,
    FieldObservation,
    NormalizedBlock,
    NormalizedPage,
    ProcessingRoute,
    QualityReport,
    Sensitivity,
)


PDF = b"%PDF-1.4\nsynthetic\n%%EOF\n"


def _ready_run(tmp_path, *, request_key: str = "phase6:one"):
    repository = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=4096, quota_bytes=16384)
    writer = spool.begin(filename="synthetic.pdf", declared_media_type="application/pdf", title="Synthetic")
    writer.write(PDF)
    record, _ = repository.create_or_get(owner_id="operator", staged=writer.finish())
    repository.mark_archiving(document_id=record.document_id, task_ref="archive-task")
    record = repository.mark_ready(
        document_id=record.document_id,
        provider="paperless",
        external_id="41",
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
        request_key=request_key,
    )
    repository.begin_processing_run(run_id=run["run_id"], fencing_token=1)
    return repository, record, run


def _artifact(record, run, texts: list[str]) -> DocumentArtifact:
    blocks = tuple(
        NormalizedBlock(
            block_id=f"b{index}",
            page_number=1,
            kind="paragraph",
            reading_order=index,
            text=text,
            bbox=None,
            char_span=None,
            provider_ref=f"#/blocks/{index}",
        )
        for index, text in enumerate(texts, start=1)
    )
    return DocumentArtifact(
        schema_version="2",
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        run_id=run["run_id"],
        provider_name="synthetic",
        provider_version="1",
        pages=(NormalizedPage(1, 100.0, 100.0, "pixels"),),
        blocks=blocks,
        quality=QualityReport(200, 1, len(blocks), 0.0, 1.0, True, True, ()),
        raw_provider={"content": "must not survive enrichment"},
        markdown="\n".join(texts),
    )


def _service(repository, archive_access=None) -> DocumentEnrichmentService:
    return DocumentEnrichmentService(
        repository=repository,
        classifier=DeterministicDocumentClassifier(),
        extractor=DeterministicStructuredExtractor(),
        archive_access=archive_access,
    )


def test_registry_contains_exact_versioned_taxonomy_and_protected_schemas_are_disabled() -> None:
    labels = {label for schema in SCHEMAS for label in schema.document_classes}
    assert labels == set(DocumentClass)
    assert TAXONOMY_VERSION == "document-taxonomy-v1"
    protected = next(schema for schema in SCHEMAS if schema.name == "RestrictedIdentityDocument")
    assert not protected.phase6_enabled
    assert set(protected.document_classes) == {
        DocumentClass.IDENTITY_DOCUMENT,
        DocumentClass.GOVERNMENT_DOCUMENT,
        DocumentClass.TAX_DOCUMENT,
    }


def test_financial_enrichment_masks_exact_identifier_and_persists_typed_evidence(tmp_path) -> None:
    repository, record, run = _ready_run(tmp_path)
    access_events = []

    class ArchiveAccess:
        def grant_read_access(self, external_id):
            access_events.append(("grant", external_id))

        def revoke_read_access(self, external_id):
            access_events.append(("revoke", external_id))

    artifact = _artifact(
        record,
        run,
        [
            "Acme Energy",
            "Account number: 123456789012",
            "Amount Due $42.50",
            "Due Date 09/15/2026",
        ],
    )

    outcome = _service(repository, ArchiveAccess()).enrich(artifact)

    assert outcome.document_class == DocumentClass.BILL
    assert not outcome.protected_pending and outcome.artifact is not None
    persisted_view = "\n".join(block.text for block in outcome.artifact.blocks)
    assert "123456789012" not in persisted_view
    assert "****9012" in persisted_view
    assert outcome.artifact.raw_provider == {
        "redacted": True,
        "provider_name": "synthetic",
        "provider_version": "1",
    }
    fields = {row["field_name"]: row["value"] for row in repository.effective_fields(document_id=record.document_id)}
    assert fields["amount_due"] == "42.50"
    assert fields["due_date"] == "2026-09-15"
    assert fields["account_identifier_masked"] == "****9012"
    assert repository.list_classifications(document_id=record.document_id)[0]["label"] == "bill"
    assert repository.get(record.document_id).archive_text_visible is False
    assert access_events == [("revoke", "41")]
    repository.close()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("United States Passport\nPassport number: X12345678", DocumentClass.IDENTITY_DOCUMENT),
        ("Internal Revenue Service Form 1099", DocumentClass.TAX_DOCUMENT),
        ("Certificate of Birth government identification", DocumentClass.GOVERNMENT_DOCUMENT),
    ],
)
def test_restricted_classes_fail_closed_without_general_observations(tmp_path, text, expected) -> None:
    repository, record, run = _ready_run(tmp_path)
    outcome = _service(repository).enrich(_artifact(record, run, [text]))

    assert outcome.document_class == expected
    assert outcome.protected_pending and outcome.artifact is None
    assert repository.effective_fields(document_id=record.document_id) == []
    current = repository.get(record.document_id)
    assert current is not None and not current.search_visible
    assert not current.archive_text_visible
    assert current.sensitivity in {Sensitivity.IDENTITY, Sensitivity.HIGHLY_RESTRICTED}
    repository.close()


def test_safe_unredacted_document_receives_archive_read_access_after_classification(tmp_path) -> None:
    repository, record, run = _ready_run(tmp_path)
    access_events = []

    class ArchiveAccess:
        def grant_read_access(self, external_id):
            access_events.append(("grant", external_id))

        def revoke_read_access(self, external_id):
            access_events.append(("revoke", external_id))

    outcome = _service(repository, ArchiveAccess()).enrich(
        _artifact(record, run, ["Meeting Notes", "Attendees", "Action Items"])
    )
    assert outcome.document_class == DocumentClass.MEETING_NOTES
    assert access_events == [("grant", "41")]
    assert repository.get(record.document_id).archive_text_visible is True
    repository.close()


def test_schema_rejects_unknown_fields_missing_evidence_and_exact_restricted_values() -> None:
    base = dict(
        contract_version="document-extraction-v1",
        schema_name="FinancialDocument",
        schema_version="2",
        extractor_name="fake",
        extractor_version="1",
    )
    exact = ExtractionResult(
        **base,
        observations=(
            FieldObservation(
                "account_identifier_masked",
                "123456789012",
                "123456789012",
                Sensitivity.FINANCIAL,
                1.0,
                (EvidenceRef(1, "b1"),),
            ),
        ),
    )
    with pytest.raises(ValueError, match="exact restricted"):
        validate_extraction(exact, document_class=DocumentClass.BILL)
    missing_evidence = ExtractionResult(
        **base,
        observations=(FieldObservation("issuer", "Acme", "Acme", Sensitivity.PRIVATE, 1.0, ()),),
    )
    with pytest.raises(ValueError, match="requires evidence"):
        validate_extraction(missing_evidence, document_class=DocumentClass.BILL)
    assert {"contract_version", "schema_name", "schema_version", "extractor_name", "extractor_version", "observations"} == set(asdict(exact))
    assert not ({"tools", "intents", "approvals"} & set(asdict(exact)))


def test_human_correction_precedes_reprocessed_machine_observation(tmp_path) -> None:
    repository, record, run = _ready_run(tmp_path)
    first = _service(repository).enrich(
        _artifact(record, run, ["Acme Energy", "Invoice", "Amount Due $42.50"])
    )
    amount = next(row for row in repository.effective_fields(document_id=record.document_id) if row["field_name"] == "amount_due")
    repository.record_field_decision(
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        field_name="amount_due",
        review_decision_id="review-decision-1",
        decision_kind="correct",
        selected_observation_id=amount["observation_id"],
        applied_value="41.75",
    )
    second_run = repository.create_processing_run(
        document_id=record.document_id,
        route=ProcessingRoute.CONVENTIONAL_OCR,
        parser_name="synthetic",
        parser_version="2",
        parser_image_digest="sha256:" + "3" * 64,
        configuration_sha256="4" * 64,
        resource_lane="cpu_ocr",
        request_key="phase6:two",
    )
    repository.begin_processing_run(run_id=second_run["run_id"], fencing_token=1)
    _service(repository).enrich(
        _artifact(record, second_run, ["Acme Energy", "Invoice", "Amount Due $99.00"])
    )

    effective = {row["field_name"]: row for row in repository.effective_fields(document_id=record.document_id)}
    assert effective["amount_due"]["value"] == "41.75"
    assert effective["amount_due"]["decision_kind"] == "correct"
    assert effective["amount_due"]["observation_state"] == "conflicted"
    assert first.extraction is not None
    repository.close()


def test_redactor_leaves_ordinary_account_language_but_masks_identifiers() -> None:
    text, count = redact_text("Account balance is $20. Account number: AB-12345678")
    assert "Account balance is $20" in text
    assert "AB-12345678" not in text
    assert "****5678" in text and count == 1
    assert not contains_unmasked_restricted_value(text)


def test_processing_stops_protected_document_before_general_artifact_or_search_storage(tmp_path) -> None:
    repository, record, run = _ready_run(tmp_path)

    class Archive:
        def download_original(self, source_external_id: str):
            assert source_external_id == "41"
            yield PDF

    class Parser:
        provider_name = "synthetic"
        provider_version = "1"

        def submit(self, **kwargs):
            return ParserSubmission("protected-operation")

        def status(self, operation_ref: str):
            return ParserOperation(operation_ref, "success")

        def result(self, **kwargs):
            return _artifact(
                record,
                run,
                ["United States Passport", "Passport number: X12345678"],
            )

        def ready(self):
            return True

    service = DocumentProcessingService(
        repository=repository,
        archive=Archive(),
        parser=Parser(),
        artifact_store=ContentAddressedArtifactStore(str(tmp_path / "artifacts")),
        enrichment=_service(repository),
    )
    with pytest.raises(DocumentProcessingPending):
        service.process(
            document_id=record.document_id,
            source_version_id=record.source_version_id,
            run_id=run["run_id"],
            fencing_token=1,
        )
    result = service.process(
        document_id=record.document_id,
        source_version_id=record.source_version_id,
        run_id=run["run_id"],
        fencing_token=1,
    )
    assert result["status"] == "protected_pending"
    assert repository.processing_run_blocks(run["run_id"]) == []
    assert repository.search_blocks(owner_id="operator", query="passport", limit=5) == []
    assert not list((tmp_path / "artifacts").rglob("*.*"))
    repository.close()


def test_approved_metadata_sync_is_version_bound_idempotent_and_readback_verified(tmp_path) -> None:
    repository, record, _ = _ready_run(tmp_path)
    proposal = repository.create_metadata_proposal(
        document_id=record.document_id,
        field_name="safe_title",
        proposed_value="September utility bill",
        sensitivity=Sensitivity.PRIVATE,
    )
    repository.bind_metadata_review(
        document_id=record.document_id,
        proposal_id=proposal["proposal_id"],
        review_id="review-1",
    )

    class ArchiveMetadata:
        calls = 0

        def write_metadata(self, **kwargs):
            self.calls += 1
            assert kwargs["expected_external_version"] == "paperless-v1"
            assert kwargs["changes"] == {"safe_title": "September utility bill"}
            return ArchiveMetadataSnapshot("paperless-v2", dict(kwargs["changes"]))

    archive = ArchiveMetadata()
    service = DocumentMetadataSyncService(repository=repository, archive=archive)
    review = {
        "review_id": "review-1",
        "subject_type": "document_metadata_proposal",
        "subject_id": proposal["proposal_id"],
        "subject_version": record.source_version_id,
        "item_hash": proposal["value_hash"],
        "state": "approved",
    }
    decision = {
        "review_id": "review-1",
        "decision": "approve",
        "bound_item_hash": proposal["value_hash"],
    }
    first = service.apply_approved(
        proposal_id=proposal["proposal_id"],
        review=review,
        decision=decision,
        operation_id="metadata-operation-1",
        expected_external_version="paperless-v1",
    )
    second = service.apply_approved(
        proposal_id=proposal["proposal_id"],
        review=review,
        decision=decision,
        operation_id="metadata-operation-1",
        expected_external_version="paperless-v1",
    )
    assert first["state"] == second["state"] == "applied"
    assert first["observed_hash"] == first["desired_hash"]
    assert archive.calls == 1
    assert repository.get_metadata_proposal(proposal_id=proposal["proposal_id"])["state"] == "applied"
    repository.close()
