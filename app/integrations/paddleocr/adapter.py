from __future__ import annotations

import re
from typing import Any, BinaryIO
from uuid import uuid4

from app.integrations.paddleocr.client import PaddleOCRClient
from app.skills.domains.documents.ports import (
    ParserOperation,
    ParserOperationUnavailable,
    ParserSubmission,
)
from app.skills.domains.documents.types import (
    DocumentArtifact,
    NormalizedBlock,
    NormalizedPage,
    QualityReport,
)


_MEDIA_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png"})


def _bbox(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, list) or len(value) < 4:
        return None
    try:
        if all(isinstance(item, (int, float)) for item in value[:4]):
            left, top, right, bottom = (float(item) for item in value[:4])
        else:
            points = [item for item in value if isinstance(item, list) and len(item) >= 2]
            xs = [float(item[0]) for item in points]
            ys = [float(item[1]) for item in points]
            left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
        if left <= right and top <= bottom:
            return left, top, right, bottom
    except (TypeError, ValueError):
        pass
    return None


def _safe_markdown(blocks: list[NormalizedBlock]) -> str:
    lines: list[str] = []
    page = 0
    for block in blocks:
        if block.page_number != page:
            page = block.page_number
            if lines:
                lines.append("")
            lines.extend((f"# Page {page}", ""))
        lines.extend((re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", block.text), ""))
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


class PaddleOCRParserAdapter:
    provider_name = "paddleocr"

    def __init__(self, client: PaddleOCRClient, *, provider_version: str) -> None:
        self.client = client
        self.provider_version = str(provider_version or "").strip()
        self._results: dict[str, dict[str, Any]] = {}

    def submit(self, *, stream: BinaryIO, filename: str, media_type: str) -> ParserSubmission:
        if media_type not in _MEDIA_TYPES:
            raise RuntimeError("paddleocr_media_type_unsupported")
        value = self.client.infer(stream=stream, filename=filename, media_type=media_type)
        operation_ref = f"ocr-{uuid4()}"
        self._results[operation_ref] = value
        return ParserSubmission(operation_ref=operation_ref)

    def status(self, operation_ref: str) -> ParserOperation:
        if operation_ref not in self._results:
            raise ParserOperationUnavailable("paddleocr_operation_unavailable")
        value = self._results[operation_ref]
        state = str(value.get("status") or "").strip().casefold()
        if state != "success":
            return ParserOperation(
                operation_ref=operation_ref,
                state="failure",
                error_code=str(value.get("error_code") or "paddleocr_inference_failed")[:120],
            )
        return ParserOperation(operation_ref=operation_ref, state="success")

    def result(
        self,
        *,
        operation_ref: str,
        document_id: str,
        source_version_id: str,
        run_id: str,
    ) -> DocumentArtifact:
        value = self._results.get(operation_ref)
        if value is None:
            raise ParserOperationUnavailable("paddleocr_operation_unavailable")
        raw_pages = value.get("pages")
        if not isinstance(raw_pages, list):
            raise RuntimeError("paddleocr_pages_missing")
        pages: list[NormalizedPage] = []
        blocks: list[NormalizedBlock] = []
        char_offset = 0
        for page_index, raw_page in enumerate(raw_pages):
            if not isinstance(raw_page, dict):
                continue
            page_number = page_index + 1
            try:
                page_number = max(1, int(raw_page.get("page_index", page_index)) + 1)
                width = max(1.0, float(raw_page.get("width") or 1.0))
                height = max(1.0, float(raw_page.get("height") or 1.0))
            except (TypeError, ValueError):
                width, height = 1.0, 1.0
            pages.append(NormalizedPage(page_number, width, height, "pixels"))
            texts = raw_page.get("rec_texts")
            scores = raw_page.get("rec_scores")
            boxes = raw_page.get("rec_boxes") or raw_page.get("rec_polys")
            if not isinstance(texts, list):
                continue
            for line_index, raw_text in enumerate(texts):
                text = " ".join(str(raw_text or "").split())
                if not text:
                    continue
                confidence = None
                if isinstance(scores, list) and line_index < len(scores):
                    try:
                        confidence = max(0.0, min(float(scores[line_index]), 1.0))
                    except (TypeError, ValueError):
                        confidence = None
                bbox = _bbox(boxes[line_index]) if isinstance(boxes, list) and line_index < len(boxes) else None
                block_id = f"p{page_number}b{line_index + 1}"
                blocks.append(
                    NormalizedBlock(
                        block_id=block_id,
                        page_number=page_number,
                        kind="ocr_line",
                        reading_order=line_index,
                        text=text,
                        bbox=bbox,
                        char_span=(char_offset, char_offset + len(text)),
                        provider_ref=f"#/pages/{page_index}/lines/{line_index}",
                        confidence=confidence,
                        language=str(value.get("language") or "")[:40] or None,
                    )
                )
                char_offset += len(text) + 1
        return DocumentArtifact(
            schema_version="2",
            document_id=document_id,
            source_version_id=source_version_id,
            run_id=run_id,
            provider_name=self.provider_name,
            provider_version=self.provider_version,
            pages=tuple(pages),
            blocks=tuple(blocks),
            quality=QualityReport(0, 0, 0, 0.0, 0.0, False, False, ()),
            raw_provider=value,
            markdown=_safe_markdown(blocks),
        )

    def ready(self) -> bool:
        return self.client.ready()
