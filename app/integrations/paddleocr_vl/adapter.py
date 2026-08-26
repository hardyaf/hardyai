from __future__ import annotations

import re
from typing import Any, BinaryIO
from uuid import uuid4

from app.integrations.paddleocr_vl.client import PaddleOCRVLClient
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


def _bbox(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        left, top, right, bottom = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    return (left, top, right, bottom) if left <= right and top <= bottom else None


def _markdown(blocks: list[NormalizedBlock]) -> str:
    lines: list[str] = []
    current_page = 0
    for block in blocks:
        if block.page_number != current_page:
            current_page = block.page_number
            if lines:
                lines.append("")
            lines.extend((f"# Page {current_page}", ""))
        lines.extend((re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", block.text), ""))
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


class PaddleOCRVLParserAdapter:
    provider_name = "paddleocr_vl"

    def __init__(self, client: PaddleOCRVLClient, *, provider_version: str) -> None:
        self.client = client
        self.provider_version = str(provider_version or "").strip()
        self._results: dict[str, dict[str, Any]] = {}

    def submit(self, *, stream: BinaryIO, filename: str, media_type: str) -> ParserSubmission:
        if media_type not in {"image/jpeg", "image/png"}:
            raise RuntimeError("paddleocr_vl_media_type_unsupported")
        value = self.client.infer(stream=stream, filename=filename, media_type=media_type)
        operation_ref = f"vlm-{uuid4()}"
        self._results[operation_ref] = value
        return ParserSubmission(operation_ref=operation_ref)

    def status(self, operation_ref: str) -> ParserOperation:
        value = self._results.get(operation_ref)
        if value is None:
            raise ParserOperationUnavailable("paddleocr_vl_operation_unavailable")
        if str(value.get("status") or "").strip().casefold() != "success":
            return ParserOperation(
                operation_ref=operation_ref,
                state="failure",
                error_code=str(value.get("error_code") or "paddleocr_vl_inference_failed")[:120],
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
            raise ParserOperationUnavailable("paddleocr_vl_operation_unavailable")
        raw_pages = value.get("pages")
        if not isinstance(raw_pages, list):
            raise RuntimeError("paddleocr_vl_pages_missing")
        pages: list[NormalizedPage] = []
        blocks: list[NormalizedBlock] = []
        char_offset = 0
        for page_index, raw_page in enumerate(raw_pages):
            if not isinstance(raw_page, dict):
                continue
            try:
                page_number = max(1, int(raw_page.get("page_index", page_index)) + 1)
                width = max(1.0, float(raw_page.get("width") or 1.0))
                height = max(1.0, float(raw_page.get("height") or 1.0))
            except (TypeError, ValueError):
                page_number, width, height = page_index + 1, 1.0, 1.0
            pages.append(NormalizedPage(page_number, width, height, "pixels"))
            raw_blocks = raw_page.get("blocks")
            if not isinstance(raw_blocks, list):
                continue
            for block_index, raw_block in enumerate(raw_blocks):
                if not isinstance(raw_block, dict):
                    continue
                text = " ".join(str(raw_block.get("text") or "").split())
                if not text:
                    continue
                confidence = raw_block.get("confidence")
                if not isinstance(confidence, (int, float)):
                    confidence = None
                blocks.append(
                    NormalizedBlock(
                        block_id=f"p{page_number}v{block_index + 1}",
                        page_number=page_number,
                        kind=str(raw_block.get("kind") or "vlm_block")[:60],
                        reading_order=block_index,
                        text=text,
                        bbox=_bbox(raw_block.get("bbox")),
                        char_span=(char_offset, char_offset + len(text)),
                        provider_ref=f"#/pages/{page_index}/blocks/{block_index}",
                        confidence=(
                            max(0.0, min(float(confidence), 1.0))
                            if confidence is not None
                            else None
                        ),
                        language=None,
                    )
                )
                char_offset += len(text) + 1
        return DocumentArtifact(
            schema_version="3",
            document_id=document_id,
            source_version_id=source_version_id,
            run_id=run_id,
            provider_name=self.provider_name,
            provider_version=self.provider_version,
            pages=tuple(pages),
            blocks=tuple(blocks),
            quality=QualityReport(0, 0, 0, 0.0, 0.0, False, False, ()),
            raw_provider=value,
            markdown=_markdown(blocks),
        )

    def ready(self) -> bool:
        return self.client.ready()
