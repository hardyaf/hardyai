from __future__ import annotations

import re

from app.skills.domains.documents.schemas import TAXONOMY_VERSION
from app.skills.domains.documents.types import (
    ClassificationCandidate,
    ClassificationInput,
    ClassificationResult,
    DocumentClass,
    EvidenceRef,
    Sensitivity,
)


_RULES: tuple[tuple[DocumentClass, Sensitivity, tuple[str, ...]], ...] = (
    (DocumentClass.IDENTITY_DOCUMENT, Sensitivity.IDENTITY, ("passport", "driver's license", "drivers license", "date of birth")),
    (DocumentClass.TAX_DOCUMENT, Sensitivity.HIGHLY_RESTRICTED, ("internal revenue service", "form w-2", "form 1099", "tax return")),
    (DocumentClass.GOVERNMENT_DOCUMENT, Sensitivity.IDENTITY, ("certificate of birth", "social security administration", "government identification")),
    (DocumentClass.INSURANCE_DOCUMENT, Sensitivity.FINANCIAL, ("insurance policy", "coverage period", "insured", "policy number")),
    (DocumentClass.INVOICE, Sensitivity.FINANCIAL, ("invoice", "invoice date", "bill to")),
    (DocumentClass.BILL, Sensitivity.FINANCIAL, ("amount due", "due date", "billing period", "account balance")),
    (DocumentClass.RECEIPT, Sensitivity.FINANCIAL, ("receipt", "subtotal", "change due", "thank you for your purchase")),
    (DocumentClass.CONTRACT, Sensitivity.PRIVATE, ("agreement", "whereas", "terms and conditions", "effective date")),
    (DocumentClass.WARRANTY, Sensitivity.PRIVATE, ("warranty", "limited warranty", "warranty period")),
    (DocumentClass.MEETING_NOTES, Sensitivity.PRIVATE, ("meeting notes", "attendees", "agenda", "action items")),
    (DocumentClass.BUSINESS_CARD, Sensitivity.PRIVATE, ("linkedin.com", "www.", "email", "mobile")),
    (DocumentClass.GENERAL_NOTES, Sensitivity.PRIVATE, ("notes", "todo", "to-do", "follow up")),
)
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")


class DeterministicDocumentClassifier:
    name = "deterministic-document-classifier"
    version = "1"

    def classify(self, request: ClassificationInput) -> ClassificationResult:
        if request.contract_version != "document-classification-v1":
            raise ValueError("classifier contract version mismatch")
        if request.taxonomy_version != TAXONOMY_VERSION:
            raise ValueError("classifier taxonomy version mismatch")
        allowed = set(request.allowed_labels)
        candidates: list[ClassificationCandidate] = []
        for label, sensitivity, terms in _RULES:
            if label not in allowed:
                continue
            matched = []
            for block in request.blocks:
                folded = block.text.casefold()
                if any(term in folded for term in terms):
                    matched.append(_evidence(block))
            if matched:
                score = min(0.98, 0.72 + (0.08 * min(len(matched), 3)))
                candidates.append(ClassificationCandidate(label, sensitivity, score, tuple(matched[:8])))
        if DocumentClass.BUSINESS_CARD in allowed and _business_card_shape(request):
            evidence = tuple(_evidence(block) for block in request.blocks[:4])
            candidates.append(
                ClassificationCandidate(DocumentClass.BUSINESS_CARD, Sensitivity.PRIVATE, 0.88, evidence)
            )
        if not candidates:
            first = tuple(_evidence(block) for block in request.blocks[:1])
            candidates.append(
                ClassificationCandidate(DocumentClass.UNKNOWN, Sensitivity.PRIVATE, 0.5, first)
            )
        candidates.sort(key=lambda item: (_risk_rank(item.sensitivity), item.confidence), reverse=True)
        selected = candidates[0]
        return ClassificationResult(
            contract_version=request.contract_version,
            taxonomy_version=request.taxonomy_version,
            classifier_name=self.name,
            classifier_version=self.version,
            candidates=tuple(candidates[:5]),
            selected_label=selected.label,
            selected_sensitivity=selected.sensitivity,
            confidence=selected.confidence,
            evidence=selected.evidence,
        )


def _business_card_shape(request: ClassificationInput) -> bool:
    text = "\n".join(block.text for block in request.blocks[:12])
    return len(text) <= 1200 and bool(_EMAIL.search(text) and _PHONE.search(text))


def _evidence(block) -> EvidenceRef:
    return EvidenceRef(block.page_number, block.block_id, block.bbox, block.char_span)


def _risk_rank(value: Sensitivity) -> int:
    return {
        Sensitivity.NORMAL: 0,
        Sensitivity.PRIVATE: 1,
        Sensitivity.FINANCIAL: 2,
        Sensitivity.IDENTITY: 3,
        Sensitivity.HIGHLY_RESTRICTED: 4,
    }[value]
