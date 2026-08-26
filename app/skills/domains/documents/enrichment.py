from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from app.reviews.service import HumanReviewService
from app.reviews.types import ReviewKind
from app.skills.domains.documents.ports import (
    ArchiveAccessPolicyPort,
    DocumentClassifierPort,
    StructuredExtractorPort,
)
from app.skills.domains.documents.redaction import (
    contains_unmasked_restricted_value,
    redact_artifact_view,
)
from app.skills.domains.documents.schemas import (
    EXTRACTION_CONTRACT_VERSION,
    TAXONOMY_VERSION,
    phase6_allowed_classes,
    schema_for,
    validate_extraction,
)
from app.skills.domains.documents.storage import DocumentRepository
from app.skills.domains.documents.note_proposals import NoteProposalService
from app.skills.domains.documents.contact_proposals import ContactProposalService
from app.skills.domains.documents.intelligence import DocumentIntelligenceService
from app.skills.domains.documents.types import (
    ClassificationInput,
    DocumentArtifact,
    DocumentClass,
    ExtractionInput,
    ExtractionResult,
    Sensitivity,
)


@dataclass(frozen=True)
class EnrichmentOutcome:
    artifact: DocumentArtifact | None
    document_class: DocumentClass
    sensitivity: Sensitivity
    classification_confidence: float
    extraction: ExtractionResult | None
    protected_pending: bool
    review_ids: tuple[str, ...]
    redaction_count: int
    action_proposal_count: int = 0
    memory_proposal_count: int = 0
    contact_proposal_count: int = 0
    analysis_count: int = 0
    claim_count: int = 0


class DocumentEnrichmentService:
    """No-egress classification/extraction coordinator for one immutable processing run."""

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        classifier: DocumentClassifierPort,
        extractor: StructuredExtractorPort,
        reviews: HumanReviewService | None = None,
        archive_access: ArchiveAccessPolicyPort | None = None,
        note_proposals: NoteProposalService | None = None,
        contact_proposals: ContactProposalService | None = None,
        intelligence: DocumentIntelligenceService | None = None,
        classification_review_threshold: float = 0.8,
        field_review_threshold: float = 0.9,
        max_blocks: int = 2000,
        max_text_characters: int = 500_000,
    ) -> None:
        self.repository = repository
        self.classifier = classifier
        self.extractor = extractor
        self.reviews = reviews
        self.archive_access = archive_access
        self.note_proposals = note_proposals
        self.contact_proposals = contact_proposals
        self.intelligence = intelligence
        self.classification_review_threshold = max(0.0, min(classification_review_threshold, 1.0))
        self.field_review_threshold = max(0.0, min(field_review_threshold, 1.0))
        self.max_blocks = max(1, min(int(max_blocks), 10_000))
        self.max_text_characters = max(1_000, min(int(max_text_characters), 2_000_000))

    def enrich(self, artifact: DocumentArtifact) -> EnrichmentOutcome:
        blocks = self._bounded_blocks(artifact)
        classification = self.classifier.classify(
            ClassificationInput(
                contract_version="document-classification-v1",
                document_id=artifact.document_id,
                source_version_id=artifact.source_version_id,
                run_id=artifact.run_id,
                taxonomy_version=TAXONOMY_VERSION,
                allowed_labels=tuple(DocumentClass),
                blocks=blocks,
            )
        )
        classification_rows = self.repository.append_classification(
            document_id=artifact.document_id,
            source_version_id=artifact.source_version_id,
            run_id=artifact.run_id,
            result=classification,
        )
        review_ids: list[str] = []
        selected_row = next(
            (row for row in classification_rows if row.get("selected")),
            classification_rows[0] if classification_rows else None,
        )
        if selected_row is not None and (
            classification.confidence < self.classification_review_threshold
            or str(selected_row.get("state")) == "conflicted"
        ):
            review_id = self._create_review(
                kind=ReviewKind.CLASSIFICATION,
                subject_type="document_classification",
                subject_id=str(selected_row["classification_id"]),
                subject_version=artifact.source_version_id,
                item_hash=str(selected_row["item_hash"]),
                source_ref=artifact.document_id,
                sensitivity=classification.selected_sensitivity,
                confidence=classification.confidence,
                evidence=classification.evidence,
                reason="classification_low_confidence_or_conflict",
            )
            if review_id:
                review_ids.append(review_id)
        if classification.selected_sensitivity in {Sensitivity.IDENTITY, Sensitivity.HIGHLY_RESTRICTED}:
            self._set_archive_access(artifact.document_id, visible=False)
            self.repository.mark_protected_pending(
                document_id=artifact.document_id,
                run_id=artifact.run_id,
                sensitivity=classification.selected_sensitivity,
                reason_code="restricted_document_class",
            )
            return EnrichmentOutcome(
                None,
                classification.selected_label,
                classification.selected_sensitivity,
                classification.confidence,
                None,
                True,
                tuple(review_ids),
                0,
            )

        redacted = redact_artifact_view(
            blocks=blocks,
            tables=artifact.tables,
            markdown=artifact.markdown,
        )
        if self._contains_restricted(redacted):
            self._set_archive_access(artifact.document_id, visible=False)
            self.repository.mark_protected_pending(
                document_id=artifact.document_id,
                run_id=artifact.run_id,
                reason_code="restricted_redaction_incomplete",
            )
            return EnrichmentOutcome(
                None,
                classification.selected_label,
                Sensitivity.HIGHLY_RESTRICTED,
                classification.confidence,
                None,
                True,
                tuple(review_ids),
                redacted.replacement_count,
            )
        sanitized = replace(
            artifact,
            blocks=redacted.blocks,
            tables=redacted.tables,
            markdown=redacted.markdown,
            raw_provider={
                "redacted": True,
                "provider_name": artifact.provider_name,
                "provider_version": artifact.provider_version,
            },
        )
        archive_text_visible = redacted.replacement_count == 0
        self._set_archive_access(artifact.document_id, visible=archive_text_visible)
        extraction = None
        if classification.selected_label in phase6_allowed_classes():
            schema = schema_for(classification.selected_label)
            extraction = self.extractor.extract(
                ExtractionInput(
                    contract_version=EXTRACTION_CONTRACT_VERSION,
                    schema_name=schema.name,
                    schema_version=schema.version,
                    document_id=artifact.document_id,
                    source_version_id=artifact.source_version_id,
                    run_id=artifact.run_id,
                    document_class=classification.selected_label,
                    sensitivity=classification.selected_sensitivity,
                    blocks=redacted.blocks,
                )
            )
            validate_extraction(extraction, document_class=classification.selected_label)
            observations = self.repository.append_field_observations(
                document_id=artifact.document_id,
                source_version_id=artifact.source_version_id,
                run_id=artifact.run_id,
                result=extraction,
            )
            by_hash = {str(row["item_hash"]): row for row in observations}
            for observation in extraction.observations:
                if observation.confidence >= self.field_review_threshold:
                    continue
                item_hash = hashlib.sha256(
                    (observation.field_name + "\0" + observation.literal_text).encode("utf-8")
                ).hexdigest()
                row = next(
                    (
                        candidate
                        for candidate in by_hash.values()
                        if str(candidate.get("field_name")) == observation.field_name
                    ),
                    None,
                )
                review_id = self._create_review(
                    kind=ReviewKind.FIELD_CORRECTION,
                    subject_type="document_field_observation",
                    subject_id=str(row["observation_id"]) if row else observation.field_name,
                    subject_version=artifact.source_version_id,
                    item_hash=str(row["item_hash"]) if row else item_hash,
                    source_ref=artifact.document_id,
                    sensitivity=observation.sensitivity,
                    confidence=observation.confidence,
                    evidence=observation.evidence,
                    reason="field_low_confidence",
                )
                if review_id:
                    review_ids.append(review_id)
        action_proposal_count = 0
        memory_proposal_count = 0
        contact_proposal_count = 0
        analysis_count = 0
        claim_count = 0
        if self.note_proposals is not None and classification.selected_label in {
            DocumentClass.MEETING_NOTES,
            DocumentClass.GENERAL_NOTES,
        }:
            generated = self.note_proposals.generate(
                document_id=artifact.document_id,
                source_version_id=artifact.source_version_id,
                run_id=artifact.run_id,
                blocks=redacted.blocks,
            )
            review_ids.extend(generated.review_ids)
            action_proposal_count = len(generated.action_proposals)
            memory_proposal_count = len(generated.memory_proposals)
        if (
            self.contact_proposals is not None
            and classification.selected_label == DocumentClass.BUSINESS_CARD
            and extraction is not None
        ):
            generated_contact = self.contact_proposals.generate(
                document_id=artifact.document_id,
                source_version_id=artifact.source_version_id,
                run_id=artifact.run_id,
                extraction=extraction,
            )
            if generated_contact.review_id:
                review_ids.append(generated_contact.review_id)
            contact_proposal_count = 1 if generated_contact.proposal is not None else 0
        if self.intelligence is not None and extraction is not None:
            intelligence = self.intelligence.analyze(
                document_id=artifact.document_id,
                source_version_id=artifact.source_version_id,
                run_id=artifact.run_id,
                document_class=classification.selected_label,
                extraction=extraction,
                blocks=redacted.blocks,
            )
            review_ids.extend(intelligence.review_ids)
            analysis_count = intelligence.analysis_count
            claim_count = intelligence.claim_count
            action_proposal_count += intelligence.action_proposal_count
        return EnrichmentOutcome(
            sanitized,
            classification.selected_label,
            classification.selected_sensitivity,
            classification.confidence,
            extraction,
            False,
            tuple(review_ids),
            redacted.replacement_count,
            action_proposal_count,
            memory_proposal_count,
            contact_proposal_count,
            analysis_count,
            claim_count,
        )

    def _set_archive_access(self, document_id: str, *, visible: bool) -> None:
        self.repository.set_archive_text_visibility(document_id=document_id, visible=visible)
        if self.archive_access is None:
            return
        record = self.repository.get(document_id)
        if record is None or not record.source_ref:
            raise ValueError("archive_access_source_unavailable")
        source = self.repository.archive_source(record.source_ref)
        if source is None:
            raise ValueError("archive_access_mapping_unavailable")
        if visible:
            self.archive_access.grant_read_access(source.external_id)
        else:
            self.archive_access.revoke_read_access(source.external_id)

    def _bounded_blocks(self, artifact: DocumentArtifact):
        blocks = artifact.blocks[: self.max_blocks]
        total = sum(len(block.text) for block in blocks)
        if len(artifact.blocks) > self.max_blocks or total > self.max_text_characters:
            raise ValueError("classification_input_too_large")
        return blocks

    @staticmethod
    def _contains_restricted(redacted) -> bool:
        if contains_unmasked_restricted_value(redacted.markdown):
            return True
        if any(contains_unmasked_restricted_value(block.text) for block in redacted.blocks):
            return True
        return any(
            contains_unmasked_restricted_value(cell.text)
            for table in redacted.tables
            for cell in table.cells
        )

    def _create_review(
        self,
        *,
        kind: ReviewKind,
        subject_type: str,
        subject_id: str,
        subject_version: str,
        item_hash: str,
        source_ref: str,
        sensitivity: Sensitivity,
        confidence: float,
        evidence,
        reason: str,
    ) -> str | None:
        if self.reviews is None:
            return None
        review = self.reviews.create_review(
            review_kind=kind,
            subject_type=subject_type,
            subject_id=subject_id,
            subject_version=subject_version,
            item_hash=item_hash,
            source_ref=source_ref,
            sensitivity=sensitivity.value,
            confidence=confidence,
            validator_summary=[{"code": reason, "passed": False}],
            evidence_refs=[f"page:{item.page_number}:block:{item.block_id}" for item in evidence],
        )
        return str(review["review_id"])
