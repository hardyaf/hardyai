from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.skills.domains.documents.redaction import contains_unmasked_restricted_value
from app.skills.domains.documents.types import (
    DocumentClass,
    ExtractionResult,
    Sensitivity,
)


TAXONOMY_VERSION = "document-taxonomy-v1"
EXTRACTION_CONTRACT_VERSION = "document-extraction-v1"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    sensitivity: Sensitivity
    value_kind: str = "text"


@dataclass(frozen=True)
class DocumentSchema:
    name: str
    version: str
    document_classes: tuple[DocumentClass, ...]
    fields: tuple[FieldSpec, ...]
    phase6_enabled: bool


_FINANCIAL_FIELDS = (
    FieldSpec("subtype", Sensitivity.FINANCIAL),
    FieldSpec("issuer", Sensitivity.PRIVATE),
    FieldSpec("payee_customer", Sensitivity.PRIVATE),
    FieldSpec("account_identifier_masked", Sensitivity.FINANCIAL),
    FieldSpec("issue_date", Sensitivity.FINANCIAL, "date"),
    FieldSpec("due_date", Sensitivity.FINANCIAL, "date"),
    FieldSpec("currency", Sensitivity.FINANCIAL),
    FieldSpec("amount_due", Sensitivity.FINANCIAL, "decimal"),
    FieldSpec("amount_paid", Sensitivity.FINANCIAL, "decimal"),
    FieldSpec("subtotal", Sensitivity.FINANCIAL, "decimal"),
    FieldSpec("tax_amount", Sensitivity.FINANCIAL, "decimal"),
    FieldSpec("total_amount", Sensitivity.FINANCIAL, "decimal"),
    FieldSpec("service_period_start", Sensitivity.FINANCIAL, "date"),
    FieldSpec("service_period_end", Sensitivity.FINANCIAL, "date"),
    FieldSpec("usage_quantity", Sensitivity.FINANCIAL, "decimal"),
    FieldSpec("usage_unit", Sensitivity.FINANCIAL),
    FieldSpec("payment_status", Sensitivity.FINANCIAL),
    FieldSpec("autopay_status", Sensitivity.FINANCIAL),
)


SCHEMAS: tuple[DocumentSchema, ...] = (
    DocumentSchema(
        "FinancialDocument",
        "2",
        (DocumentClass.BILL, DocumentClass.INVOICE, DocumentClass.RECEIPT),
        _FINANCIAL_FIELDS,
        True,
    ),
    DocumentSchema(
        "NotesDocument",
        "1",
        (DocumentClass.MEETING_NOTES, DocumentClass.GENERAL_NOTES),
        (
            FieldSpec("note_kind", Sensitivity.PRIVATE),
            FieldSpec("title_candidate", Sensitivity.PRIVATE),
            FieldSpec("occurred_on", Sensitivity.PRIVATE, "date"),
            FieldSpec("action_item", Sensitivity.PRIVATE),
            FieldSpec("decision", Sensitivity.PRIVATE),
        ),
        True,
    ),
    DocumentSchema(
        "BusinessCard",
        "1",
        (DocumentClass.BUSINESS_CARD,),
        (
            FieldSpec("full_name", Sensitivity.PRIVATE),
            FieldSpec("organization", Sensitivity.PRIVATE),
            FieldSpec("job_title", Sensitivity.PRIVATE),
            FieldSpec("email", Sensitivity.PRIVATE),
            FieldSpec("phone", Sensitivity.PRIVATE),
            FieldSpec("website", Sensitivity.PRIVATE),
        ),
        True,
    ),
    DocumentSchema(
        "ContractDocument",
        "1",
        (DocumentClass.CONTRACT,),
        (
            FieldSpec("document_title", Sensitivity.PRIVATE),
            FieldSpec("party", Sensitivity.PRIVATE),
            FieldSpec("effective_date", Sensitivity.PRIVATE, "date"),
            FieldSpec("expiration_date", Sensitivity.PRIVATE, "date"),
            FieldSpec("notice_period", Sensitivity.PRIVATE),
        ),
        True,
    ),
    DocumentSchema(
        "InsuranceDocument",
        "1",
        (DocumentClass.INSURANCE_DOCUMENT,),
        (
            FieldSpec("provider", Sensitivity.PRIVATE),
            FieldSpec("coverage_type", Sensitivity.PRIVATE),
            FieldSpec("policy_identifier_masked", Sensitivity.FINANCIAL),
            FieldSpec("effective_date", Sensitivity.FINANCIAL, "date"),
            FieldSpec("expiration_date", Sensitivity.FINANCIAL, "date"),
        ),
        True,
    ),
    DocumentSchema(
        "WarrantyDocument",
        "1",
        (DocumentClass.WARRANTY,),
        (
            FieldSpec("provider", Sensitivity.PRIVATE),
            FieldSpec("product", Sensitivity.PRIVATE),
            FieldSpec("purchase_date", Sensitivity.PRIVATE, "date"),
            FieldSpec("expiration_date", Sensitivity.PRIVATE, "date"),
        ),
        True,
    ),
    DocumentSchema(
        "RestrictedIdentityDocument",
        "1",
        (
            DocumentClass.IDENTITY_DOCUMENT,
            DocumentClass.GOVERNMENT_DOCUMENT,
            DocumentClass.TAX_DOCUMENT,
        ),
        (),
        False,
    ),
    DocumentSchema("UnknownDocument", "1", (DocumentClass.UNKNOWN,), (), False),
)


_SCHEMA_BY_CLASS = {
    document_class: schema
    for schema in SCHEMAS
    for document_class in schema.document_classes
}
_SENSITIVITY_RANK = {
    Sensitivity.NORMAL: 0,
    Sensitivity.PRIVATE: 1,
    Sensitivity.FINANCIAL: 2,
    Sensitivity.IDENTITY: 3,
    Sensitivity.HIGHLY_RESTRICTED: 4,
}


def schema_for(document_class: DocumentClass | str) -> DocumentSchema:
    return _SCHEMA_BY_CLASS[DocumentClass(document_class)]


def phase6_allowed_classes() -> tuple[DocumentClass, ...]:
    return tuple(
        document_class
        for schema in SCHEMAS
        if schema.phase6_enabled
        for document_class in schema.document_classes
    )


def validate_extraction(result: ExtractionResult, *, document_class: DocumentClass) -> None:
    schema = schema_for(document_class)
    if not schema.phase6_enabled:
        raise ValueError("document class is not enabled for extraction")
    if result.contract_version != EXTRACTION_CONTRACT_VERSION:
        raise ValueError("extractor contract version mismatch")
    if result.schema_name != schema.name or result.schema_version != schema.version:
        raise ValueError("extractor schema version mismatch")
    allowed = {field.name: field for field in schema.fields}
    seen: set[str] = set()
    for observation in result.observations:
        if observation.field_name not in allowed or observation.field_name in seen:
            raise ValueError("extractor returned an unknown or duplicate field")
        seen.add(observation.field_name)
        if not observation.evidence:
            raise ValueError("field observation requires evidence")
        if not 0.0 <= float(observation.confidence) <= 1.0:
            raise ValueError("field confidence is out of range")
        if _SENSITIVITY_RANK[observation.sensitivity] < _SENSITIVITY_RANK[allowed[observation.field_name].sensitivity]:
            raise ValueError("field sensitivity was downgraded")
        encoded = json.dumps(observation.value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > 4096 or len(observation.literal_text) > 500:
            raise ValueError("field observation is too large")
        if contains_unmasked_restricted_value(encoded) or contains_unmasked_restricted_value(
            observation.literal_text
        ):
            raise ValueError("field observation contains an exact restricted value")
        _validate_value_kind(allowed[observation.field_name].value_kind, observation.value)


def _validate_value_kind(kind: str, value: Any) -> None:
    if kind == "text" and not isinstance(value, str):
        raise ValueError("field value must be text")
    if kind == "date":
        if not isinstance(value, str) or len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError("field value must be an ISO date")
    if kind == "decimal":
        if not isinstance(value, str):
            raise ValueError("decimal value must be a string")
        from decimal import Decimal, InvalidOperation

        try:
            Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("field value must be a decimal string") from exc
