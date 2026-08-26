from __future__ import annotations

from dataclasses import asdict, dataclass

from app.people.matching import contact_search_query, match_contacts
from app.people.types import ContactProvider
from app.reviews.service import HumanReviewService
from app.reviews.types import ReviewKind
from app.skills.domains.documents.storage import DocumentRepository
from app.skills.domains.documents.types import ExtractionResult


@dataclass(frozen=True)
class ContactProposalResult:
    proposal: dict | None
    review_id: str | None


class ContactProposalService:
    """Maps a business-card observation set to a gated provider-neutral proposal."""

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        reviews: HumanReviewService,
        provider: ContactProvider | None = None,
    ) -> None:
        self.repository = repository
        self.reviews = reviews
        self.provider = provider

    def generate(
        self,
        *,
        document_id: str,
        source_version_id: str,
        run_id: str,
        extraction: ExtractionResult,
    ) -> ContactProposalResult:
        fields = {
            observation.field_name: str(observation.value)
            for observation in extraction.observations
            if observation.field_name
            in {"full_name", "organization", "job_title", "email", "phone", "website"}
            and str(observation.value or "").strip()
        }
        if not fields:
            return ContactProposalResult(None, None)
        evidence = []
        seen = set()
        for observation in extraction.observations:
            if observation.field_name not in fields:
                continue
            for reference in observation.evidence:
                key = (reference.page_number, reference.block_id)
                if key in seen:
                    continue
                seen.add(key)
                evidence.append({"page_number": reference.page_number, "block_id": reference.block_id})
        provider_name = None
        capability_status = "capability_unavailable"
        matches = match_contacts(fields, ())
        if self.provider is not None:
            provider_name = str(self.provider.provider_name)[:80]
            query = contact_search_query(fields)
            candidates = self.provider.search(query=query, limit=20) if query else ()
            matches = match_contacts(fields, tuple(candidates))
            capability_status = "available"
        confidence = min(
            (float(item.confidence) for item in extraction.observations if item.field_name in fields),
            default=0.0,
        )
        proposal = self.repository.create_contact_proposal(
            document_id=document_id,
            source_version_id=source_version_id,
            run_id=run_id,
            proposed_fields=fields,
            candidate_matches=[asdict(item) for item in matches.candidates],
            provider_name=provider_name,
            capability_status=capability_status,
            proposed_operation=matches.proposed_operation,
            selected_contact_ref=matches.selected_ref,
            confidence=confidence,
            evidence=evidence,
        )
        review = self.reviews.create_review(
            review_kind=ReviewKind.DOWNSTREAM_ACTION,
            subject_type="document_contact_proposal",
            subject_id=str(proposal["proposal_id"]),
            subject_version=source_version_id,
            item_hash=str(proposal["item_hash"]),
            source_ref=document_id,
            sensitivity="private",
            confidence=confidence,
            validator_summary=[
                {"code": "contact_provider_available", "passed": self.provider is not None},
                {"code": "candidate_unambiguous", "passed": not matches.ambiguous},
            ],
            evidence_refs=[
                f"page:{item['page_number']}:block:{item['block_id']}" for item in evidence
            ],
            target_operation="contacts.create_or_update",
            authorization_binding=f"owner:{document_id}:source:{source_version_id}",
        )
        if not self.repository.bind_contact_review(
            proposal_id=str(proposal["proposal_id"]),
            review_id=str(review["review_id"]),
        ):
            raise RuntimeError("contact proposal review binding failed")
        proposal["review_id"] = str(review["review_id"])
        return ContactProposalResult(proposal, str(review["review_id"]))
