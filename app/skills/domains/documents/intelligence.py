from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass

from app.reviews.service import HumanReviewService
from app.reviews.types import ReviewKind
from app.skills.domains.documents.financial_validation import (
    prior_period_change,
    reconcile_totals,
    validated_currency,
    validated_date,
    validated_decimal,
    validated_masked_identifier,
)
from app.skills.domains.documents.storage import DocumentRepository
from app.skills.domains.documents.types import DocumentClass, ExtractionResult, NormalizedBlock


_DATE = re.compile(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})(?!\d)")
_CLAIMS = (
    ("renewal", re.compile(r"\b(renew|renewal|auto-renew)\b", re.IGNORECASE)),
    ("notice", re.compile(r"\bnotice\b", re.IGNORECASE)),
    ("termination", re.compile(r"\b(terminate|termination|cancel)\b", re.IGNORECASE)),
    ("coverage", re.compile(r"\b(coverage|covered|exclusion)\b", re.IGNORECASE)),
    ("claim", re.compile(r"\bclaim\b", re.IGNORECASE)),
    ("warranty", re.compile(r"\bwarrant(?:y|ies)\b", re.IGNORECASE)),
)


@dataclass(frozen=True)
class IntelligenceOutcome:
    analysis_count: int
    claim_count: int
    action_proposal_count: int
    review_ids: tuple[str, ...]


class DocumentIntelligenceService:
    """Deterministic Phase 9 analysis; produces observations and reviewed reminders only."""

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        reviews: HumanReviewService,
        recurring_match_key: bytes | None = None,
    ) -> None:
        self.repository = repository
        self.reviews = reviews
        self.recurring_match_key = recurring_match_key if recurring_match_key and len(recurring_match_key) >= 32 else None

    def analyze(
        self,
        *,
        document_id: str,
        source_version_id: str,
        run_id: str,
        document_class: DocumentClass,
        extraction: ExtractionResult,
        blocks: tuple[NormalizedBlock, ...],
    ) -> IntelligenceOutcome:
        analyses = 0
        claims = 0
        proposals = 0
        review_ids: list[str] = []
        if document_class in {DocumentClass.BILL, DocumentClass.INVOICE, DocumentClass.RECEIPT}:
            self._financial(
                document_id=document_id,
                source_version_id=source_version_id,
                run_id=run_id,
                extraction=extraction,
            )
            analyses += 1
        if document_class in {
            DocumentClass.CONTRACT,
            DocumentClass.INSURANCE_DOCUMENT,
            DocumentClass.WARRANTY,
        }:
            created, created_reviews = self._important_record(
                document_id=document_id,
                source_version_id=source_version_id,
                run_id=run_id,
                document_class=document_class,
                blocks=blocks,
            )
            claims += created
            proposals += len(created_reviews)
            review_ids.extend(created_reviews)
        return IntelligenceOutcome(analyses, claims, proposals, tuple(review_ids))

    def _financial(self, *, document_id: str, source_version_id: str, run_id: str, extraction: ExtractionResult) -> None:
        raw = {item.field_name: item.value for item in extraction.observations}
        validated: dict[str, object] = {}
        invalid: list[str] = []
        for name in ("amount_due", "amount_paid", "subtotal", "tax_amount", "total_amount", "usage_quantity"):
            if name not in raw:
                continue
            value = validated_decimal(raw[name])
            if value is None:
                invalid.append(name)
            else:
                validated[name] = format(value, "f")
        for name in ("issue_date", "due_date", "service_period_start", "service_period_end"):
            if name not in raw:
                continue
            value = validated_date(raw[name])
            if value is None:
                invalid.append(name)
            else:
                validated[name] = value
        if "currency" in raw:
            currency = validated_currency(raw["currency"])
            (validated.__setitem__("currency", currency) if currency else invalid.append("currency"))
        if "account_identifier_masked" in raw:
            masked = validated_masked_identifier(raw["account_identifier_masked"])
            (validated.__setitem__("account_identifier_masked", masked) if masked else invalid.append("account_identifier_masked"))
        for name in ("issuer", "usage_unit", "payment_status", "autopay_status"):
            if name in raw:
                validated[name] = str(raw[name])[:200]
        token = self._recurring_token(validated)
        prior = (
            self.repository.previous_analysis_for_token(
                recurring_match_token=token,
                current_document_id=document_id,
            )
            if token
            else None
        )
        prior_fields = dict((prior or {}).get("result", {}).get("validated_fields") or {})
        result = {
            "machine_summary": True,
            "validated_fields": validated,
            "invalid_fields": sorted(invalid),
            "reconciliation": reconcile_totals(validated),
            "amount_change": prior_period_change(
                validated.get("amount_due") or validated.get("total_amount"),
                prior_fields.get("amount_due") or prior_fields.get("total_amount"),
            ),
            "usage_change": prior_period_change(
                validated.get("usage_quantity"), prior_fields.get("usage_quantity")
            ),
            "recurring_match": "matched_prior" if prior else "key_unavailable" if not token else "no_prior",
            "payment_status_basis": "explicit_only",
        }
        self.repository.append_analysis(
            document_id=document_id,
            source_version_id=source_version_id,
            run_id=run_id,
            analysis_kind="financial",
            result=result,
            state="needs_review" if invalid or result["reconciliation"]["state"] == "mismatch" else "observed",
            recurring_match_token=token,
        )

    def _important_record(
        self,
        *,
        document_id: str,
        source_version_id: str,
        run_id: str,
        document_class: DocumentClass,
        blocks: tuple[NormalizedBlock, ...],
    ) -> tuple[int, list[str]]:
        count = 0
        reviews: list[str] = []
        for block in blocks:
            for claim_kind, pattern in _CLAIMS:
                if not pattern.search(block.text):
                    continue
                normalized = _first_date(block.text)
                claim = self.repository.append_literal_claim(
                    document_id=document_id,
                    source_version_id=source_version_id,
                    run_id=run_id,
                    claim_kind=claim_kind,
                    machine_label="machine-extracted; verify against literal evidence",
                    literal_text=block.text,
                    normalized_date=normalized,
                    page_number=block.page_number,
                    block_id=block.block_id,
                    confidence=0.82 if normalized else 0.7,
                )
                count += 1
                if normalized:
                    proposal = self.repository.create_action_proposal(
                        document_id=document_id,
                        source_version_id=source_version_id,
                        run_id=run_id,
                        action_text=f"Review {document_class.value.replace('_', ' ')} {claim_kind} date",
                        target_list_name="to-do",
                        due_text=normalized,
                        normalized_due_date=normalized,
                        assignee_candidate=None,
                        confidence=0.75,
                        evidence=[{"page_number": block.page_number, "block_id": block.block_id}],
                    )
                    review = self.reviews.create_review(
                        review_kind=ReviewKind.DOWNSTREAM_ACTION,
                        subject_type="document_action_proposal",
                        subject_id=str(proposal["proposal_id"]),
                        subject_version=source_version_id,
                        item_hash=str(proposal["item_hash"]),
                        source_ref=document_id,
                        sensitivity="private",
                        confidence=0.75,
                        validator_summary=[{"code": "literal_clause_date", "passed": True}],
                        evidence_refs=[f"page:{block.page_number}:block:{block.block_id}"],
                        target_operation="lists.add_item",
                        authorization_binding=f"owner:{document_id}:source:{source_version_id}",
                    )
                    self.repository.bind_action_review(
                        proposal_id=str(proposal["proposal_id"]), review_id=str(review["review_id"])
                    )
                    reviews.append(str(review["review_id"]))
                break
        return count, reviews

    def _recurring_token(self, fields: dict[str, object]) -> str | None:
        issuer = str(fields.get("issuer") or "").strip().casefold()
        suffix = str(fields.get("account_identifier_masked") or "").strip().casefold()
        if self.recurring_match_key is None or not issuer or not suffix:
            return None
        return hmac.new(self.recurring_match_key, f"{issuer}\0{suffix}".encode("utf-8"), hashlib.sha256).hexdigest()


def _first_date(text: str) -> str | None:
    match = _DATE.search(text)
    if not match:
        return None
    from datetime import date

    try:
        year = int(match.group(3))
        if year < 100:
            year += 2000
        return date(year, int(match.group(1)), int(match.group(2))).isoformat()
    except ValueError:
        return None
