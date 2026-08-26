from __future__ import annotations

from dataclasses import replace
from difflib import SequenceMatcher
import re

from app.skills.domains.documents.types import DocumentArtifact, QualityReport


def evaluate_native_artifact(artifact: DocumentArtifact) -> DocumentArtifact:
    text = "\n".join(block.text for block in artifact.blocks)
    invalid = text.count("\ufffd") + sum(1 for character in text if ord(character) == 0)
    invalid_rate = invalid / max(1, len(text))
    page_count = len(artifact.pages)
    block_count = len(artifact.blocks)
    page_numbers = {page.page_number for page in artifact.pages}
    referenced_pages = {block.page_number for block in artifact.blocks}
    ordered = all(
        left.reading_order <= right.reading_order
        for left, right in zip(artifact.blocks, artifact.blocks[1:])
        if left.page_number == right.page_number
    )
    reasons: list[str] = []
    if str(artifact.raw_provider.get("status") or "success").casefold() == "partial_success":
        reasons.append("provider_partial_success")
    if len(text.strip()) < 20:
        reasons.append("native_text_near_empty")
    if invalid_rate > 0.02:
        reasons.append("invalid_character_rate")
    if not page_count or not block_count:
        reasons.append("layout_missing")
    if referenced_pages - page_numbers:
        reasons.append("evidence_page_missing")
    if not ordered:
        reasons.append("reading_order_invalid")
    quality = QualityReport(
        text_characters=len(text),
        page_count=page_count,
        block_count=block_count,
        invalid_character_rate=invalid_rate,
        text_coverage_score=min(1.0, len(text.strip()) / max(20.0, page_count * 200.0)),
        reading_order_complete=ordered and not bool(referenced_pages - page_numbers),
        processing_complete=not reasons,
        review_reasons=tuple(reasons),
    )
    return replace(artifact, quality=quality)


def evaluate_conventional_ocr_artifact(artifact: DocumentArtifact) -> DocumentArtifact:
    text = "\n".join(block.text for block in artifact.blocks)
    invalid = text.count("\ufffd") + sum(1 for character in text if ord(character) == 0)
    invalid_rate = invalid / max(1, len(text))
    confidences = [
        float(block.confidence)
        for block in artifact.blocks
        if block.confidence is not None
    ]
    mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    low_confidence = sum(1 for value in confidences if value < 0.55)
    low_ratio = low_confidence / max(1, len(confidences))
    page_numbers = {page.page_number for page in artifact.pages}
    referenced_pages = {block.page_number for block in artifact.blocks}
    ordered = all(
        left.reading_order <= right.reading_order
        for left, right in zip(artifact.blocks, artifact.blocks[1:])
        if left.page_number == right.page_number
    )
    reasons: list[str] = []
    if len(text.strip()) < 8:
        reasons.append("ocr_text_near_empty")
    if confidences and mean_confidence < 0.65:
        reasons.append("ocr_low_mean_confidence")
    if confidences and low_ratio > 0.4:
        reasons.append("ocr_many_low_confidence_lines")
    if invalid_rate > 0.02:
        reasons.append("invalid_character_rate")
    if not artifact.pages or not artifact.blocks:
        reasons.append("layout_missing")
    if referenced_pages - page_numbers:
        reasons.append("evidence_page_missing")
    if not ordered:
        reasons.append("reading_order_invalid")
    quality = QualityReport(
        text_characters=len(text),
        page_count=len(artifact.pages),
        block_count=len(artifact.blocks),
        invalid_character_rate=invalid_rate,
        text_coverage_score=min(1.0, len(text.strip()) / max(8.0, len(artifact.pages) * 120.0)),
        reading_order_complete=ordered and not bool(referenced_pages - page_numbers),
        processing_complete=not reasons,
        review_reasons=tuple(reasons),
    )
    return replace(artifact, quality=quality)


def evaluate_vlm_fallback_artifact(
    artifact: DocumentArtifact,
    *,
    reference_texts: tuple[str, ...] = (),
) -> DocumentArtifact:
    """Evaluate VLM evidence conservatively; it never becomes active without review."""

    text = "\n".join(block.text for block in artifact.blocks)
    invalid = text.count("\ufffd") + sum(1 for character in text if ord(character) == 0)
    invalid_rate = invalid / max(1, len(text))
    page_numbers = {page.page_number for page in artifact.pages}
    referenced_pages = {block.page_number for block in artifact.blocks}
    ordered = all(
        left.reading_order <= right.reading_order
        for left, right in zip(artifact.blocks, artifact.blocks[1:])
        if left.page_number == right.page_number
    )
    reasons = ["vlm_human_review_required"]
    if len(text.strip()) < 8:
        reasons.append("vlm_text_near_empty")
    if invalid_rate > 0.02:
        reasons.append("invalid_character_rate")
    if not artifact.pages or not artifact.blocks:
        reasons.append("layout_missing")
    if referenced_pages - page_numbers:
        reasons.append("evidence_page_missing")
    if not ordered:
        reasons.append("reading_order_invalid")
    conventional = "\n".join(item.strip() for item in reference_texts if item.strip())
    if conventional and text.strip():
        similarity = SequenceMatcher(None, conventional.casefold(), text.casefold()).ratio()
        if similarity < 0.85:
            reasons.append("vlm_conventional_disagreement")
    if re.search(
        r"(?:[$\u00a3\u20ac]\s*\d|\b(?:total|amount|account|routing|ssn|date)\b)",
        text,
        flags=re.IGNORECASE,
    ):
        reasons.append("vlm_critical_field_present")
    quality = QualityReport(
        text_characters=len(text),
        page_count=len(artifact.pages),
        block_count=len(artifact.blocks),
        invalid_character_rate=invalid_rate,
        text_coverage_score=min(1.0, len(text.strip()) / max(8.0, len(artifact.pages) * 120.0)),
        reading_order_complete=ordered and not bool(referenced_pages - page_numbers),
        processing_complete=False,
        review_reasons=tuple(dict.fromkeys(reasons)),
    )
    return replace(artifact, quality=quality)
