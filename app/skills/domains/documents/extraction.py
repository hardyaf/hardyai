from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.skills.domains.documents.schemas import EXTRACTION_CONTRACT_VERSION, schema_for
from app.skills.domains.documents.types import (
    DocumentClass,
    EvidenceRef,
    ExtractionInput,
    ExtractionResult,
    FieldObservation,
    Sensitivity,
)


_DATE = re.compile(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})(?!\d)")
_AMOUNT = re.compile(r"(?<![\w.])\$\s*([0-9]{1,9}(?:,[0-9]{3})*(?:\.\d{2})?)(?!\d)")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")
_URL = re.compile(r"\b(?:https?://|www\.)[^\s]+", re.IGNORECASE)
_MASKED = re.compile(r"<REDACTED:(?:NUMBER|IDENTIFIER):\*{4}([A-Z0-9]{0,4})>")
_USAGE = re.compile(r"(?i)\busage\b\s*[:=-]?\s*([0-9]{1,9}(?:\.[0-9]{1,2})?)\s*([A-Za-z]{1,12})\b")


class DeterministicStructuredExtractor:
    name = "deterministic-structured-extractor"
    version = "1"

    def extract(self, request: ExtractionInput) -> ExtractionResult:
        if request.contract_version != EXTRACTION_CONTRACT_VERSION:
            raise ValueError("extractor contract version mismatch")
        schema = schema_for(request.document_class)
        if request.schema_name != schema.name or request.schema_version != schema.version:
            raise ValueError("extractor schema version mismatch")
        observations: list[FieldObservation] = []
        if request.document_class in {DocumentClass.BILL, DocumentClass.INVOICE, DocumentClass.RECEIPT}:
            observations.extend(self._financial(request))
        elif request.document_class in {DocumentClass.MEETING_NOTES, DocumentClass.GENERAL_NOTES}:
            observations.extend(self._notes(request))
        elif request.document_class == DocumentClass.BUSINESS_CARD:
            observations.extend(self._business_card(request))
        elif request.document_class in {
            DocumentClass.CONTRACT,
            DocumentClass.INSURANCE_DOCUMENT,
            DocumentClass.WARRANTY,
        }:
            observations.extend(self._important_record(request))
        return ExtractionResult(
            request.contract_version,
            request.schema_name,
            request.schema_version,
            self.name,
            self.version,
            tuple(observations),
        )

    def _financial(self, request: ExtractionInput) -> list[FieldObservation]:
        values: list[FieldObservation] = []
        values.append(self._observation(request.blocks[0], "subtype", request.document_class.value, Sensitivity.FINANCIAL, 0.99))
        issuer = _first_plain_line(request)
        if issuer:
            values.append(self._observation(issuer[0], "issuer", issuer[1], Sensitivity.PRIVATE, 0.7))
        amount_labels = (
            ("subtotal", ("subtotal",)),
            ("tax_amount", ("sales tax", "tax")),
            ("total_amount", ("grand total", "total")),
            ("amount_due", ("amount due", "balance due")),
            ("amount_paid", ("amount paid", "payment received")),
        )
        for field_name, labels in amount_labels:
            found_amount = _labeled_amount(request, labels)
            if found_amount:
                values.append(
                    self._observation(
                        found_amount[0], field_name, found_amount[1], Sensitivity.FINANCIAL,
                        0.94, found_amount[2],
                    )
                )
        if not any(item.field_name == "amount_due" for item in values):
            total = next((item for item in values if item.field_name == "total_amount"), None)
            if total is not None:
                values.append(self._observation(request.blocks[0], "amount_due", str(total.value), Sensitivity.FINANCIAL, 0.8, total.literal_text))
        for field_name, labels in (("due_date", ("due date", "due by")), ("issue_date", ("invoice date", "statement date", "date:"))):
            found = _labeled_date(request, labels)
            if found:
                values.append(self._observation(found[0], field_name, found[1], Sensitivity.FINANCIAL, 0.92, found[2]))
        for block in request.blocks:
            match = _MASKED.search(block.text)
            if match:
                display = f"****{match.group(1)}"
                values.append(self._observation(block, "account_identifier_masked", display, Sensitivity.FINANCIAL, 0.99, display))
                break
        if any("$" in block.text for block in request.blocks):
            values.append(self._observation(request.blocks[0], "currency", "USD", Sensitivity.FINANCIAL, 0.99, "$"))
        start, end = _labeled_date_range(request, ("billing period", "service period"))
        if start:
            values.append(self._observation(start[0], "service_period_start", start[1], Sensitivity.FINANCIAL, 0.9, start[2]))
        if end:
            values.append(self._observation(end[0], "service_period_end", end[1], Sensitivity.FINANCIAL, 0.9, end[2]))
        for block in request.blocks:
            usage = _USAGE.search(block.text)
            if usage:
                amount = _decimal(usage.group(1))
                if amount is not None:
                    values.append(self._observation(block, "usage_quantity", amount, Sensitivity.FINANCIAL, 0.9, usage.group(0)))
                    values.append(self._observation(block, "usage_unit", usage.group(2), Sensitivity.FINANCIAL, 0.9, usage.group(0)))
                break
        text = "\n".join(block.text for block in request.blocks).casefold()
        if re.search(r"\b(paid in full|payment received)\b", text):
            values.append(self._observation(request.blocks[0], "payment_status", "paid", Sensitivity.FINANCIAL, 0.98, "explicit payment status"))
        elif re.search(r"\b(unpaid|past due|payment due)\b", text):
            values.append(self._observation(request.blocks[0], "payment_status", "unpaid", Sensitivity.FINANCIAL, 0.92, "explicit payment status"))
        if re.search(r"\bautopay\b.{0,20}\b(not enrolled|disabled|off)\b", text):
            values.append(self._observation(request.blocks[0], "autopay_status", "disabled", Sensitivity.FINANCIAL, 0.96, "explicit autopay status"))
        elif re.search(r"\bautopay\b.{0,20}\b(enrolled|enabled|on)\b", text):
            values.append(self._observation(request.blocks[0], "autopay_status", "enabled", Sensitivity.FINANCIAL, 0.96, "explicit autopay status"))
        return _dedupe(values)

    def _notes(self, request: ExtractionInput) -> list[FieldObservation]:
        values: list[FieldObservation] = []
        first = _first_plain_line(request)
        if first:
            values.append(self._observation(first[0], "title_candidate", first[1], Sensitivity.PRIVATE, 0.65))
        kind = "meeting" if request.document_class == DocumentClass.MEETING_NOTES else "other"
        values.append(self._observation(request.blocks[0], "note_kind", kind, Sensitivity.PRIVATE, 0.9))
        found = _labeled_date(request, ("date", "meeting date"))
        if found:
            values.append(self._observation(found[0], "occurred_on", found[1], Sensitivity.PRIVATE, 0.85, found[2]))
        return _dedupe(values)

    def _business_card(self, request: ExtractionInput) -> list[FieldObservation]:
        values: list[FieldObservation] = []
        first = _first_plain_line(request)
        if first:
            values.append(self._observation(first[0], "full_name", first[1], Sensitivity.PRIVATE, 0.7))
        for block in request.blocks:
            for field_name, pattern in (("email", _EMAIL), ("phone", _PHONE), ("website", _URL)):
                match = pattern.search(block.text)
                if match:
                    values.append(self._observation(block, field_name, match.group(0), Sensitivity.PRIVATE, 0.98))
        return _dedupe(values)

    def _important_record(self, request: ExtractionInput) -> list[FieldObservation]:
        values: list[FieldObservation] = []
        first = _first_plain_line(request)
        title_field = "document_title" if request.document_class == DocumentClass.CONTRACT else "provider"
        if first:
            values.append(self._observation(first[0], title_field, first[1], Sensitivity.PRIVATE, 0.65))
        for field_name, labels in (("effective_date", ("effective date", "effective")), ("expiration_date", ("expiration date", "expires", "valid until")), ("purchase_date", ("purchase date",))):
            if field_name not in {field.name for field in schema_for(request.document_class).fields}:
                continue
            found = _labeled_date(request, labels)
            if found:
                values.append(self._observation(found[0], field_name, found[1], request.sensitivity, 0.88, found[2]))
        if request.document_class == DocumentClass.INSURANCE_DOCUMENT:
            for block in request.blocks:
                match = _MASKED.search(block.text)
                if match:
                    display = f"****{match.group(1)}"
                    values.append(self._observation(block, "policy_identifier_masked", display, Sensitivity.FINANCIAL, 0.99, display))
                    break
        return _dedupe(values)

    @staticmethod
    def _observation(block, field_name: str, value: str, sensitivity: Sensitivity, confidence: float, literal: str | None = None) -> FieldObservation:
        return FieldObservation(
            field_name,
            value,
            str(literal if literal is not None else value)[:500],
            sensitivity,
            confidence,
            (EvidenceRef(block.page_number, block.block_id, block.bbox, block.char_span),),
        )


def _first_plain_line(request: ExtractionInput):
    for block in request.blocks:
        text = " ".join(block.text.split())[:200]
        if text and "<REDACTED:" not in text:
            return block, text
    return None


def _labeled_date(request: ExtractionInput, labels: tuple[str, ...]):
    for block in request.blocks:
        if not any(label in block.text.casefold() for label in labels):
            continue
        match = _DATE.search(block.text)
        if match:
            try:
                year = int(match.group(3))
                if year < 100:
                    year += 2000
                normalized = date(year, int(match.group(1)), int(match.group(2))).isoformat()
            except ValueError:
                continue
            return block, normalized, match.group(0)
    return None


def _labeled_amount(request: ExtractionInput, labels: tuple[str, ...]):
    for block in request.blocks:
        if not any(re.search(rf"\b{re.escape(label)}\b", block.text, re.IGNORECASE) for label in labels):
            continue
        match = _AMOUNT.search(block.text)
        if match and (amount := _decimal(match.group(1))) is not None:
            return block, amount, match.group(0)
    return None


def _labeled_date_range(request: ExtractionInput, labels: tuple[str, ...]):
    for block in request.blocks:
        if not any(label in block.text.casefold() for label in labels):
            continue
        matches = list(_DATE.finditer(block.text))[:2]
        values = []
        for match in matches:
            try:
                year = int(match.group(3)) + (2000 if int(match.group(3)) < 100 else 0)
                values.append((block, date(year, int(match.group(1)), int(match.group(2))).isoformat(), match.group(0)))
            except ValueError:
                continue
        return (values[0] if values else None, values[1] if len(values) > 1 else None)
    return None, None


def _decimal(value: str) -> str | None:
    try:
        return format(Decimal(value.replace(",", "")), "f")
    except InvalidOperation:
        return None


def _dedupe(values: list[FieldObservation]) -> list[FieldObservation]:
    result: list[FieldObservation] = []
    seen: set[str] = set()
    for value in values:
        if value.field_name not in seen:
            result.append(value)
            seen.add(value.field_name)
    return result
