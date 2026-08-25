from __future__ import annotations

from dataclasses import replace

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
