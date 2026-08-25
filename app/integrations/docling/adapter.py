from __future__ import annotations

import json
import re
from typing import Any, BinaryIO

import httpx

from app.integrations.docling.client import DoclingClient
from app.skills.domains.documents.ports import (
    ParserOperation,
    ParserOperationUnavailable,
    ParserSubmission,
)
from app.skills.domains.documents.types import (
    DocumentArtifact,
    NormalizedBlock,
    NormalizedPage,
    NormalizedTable,
    NormalizedTableCell,
    QualityReport,
)


_TASK_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")


def _bounded_task_ref(value: object) -> str:
    normalized = str(value or "").strip()
    if not _TASK_REF.fullmatch(normalized):
        raise RuntimeError("docling_invalid_task_ref")
    return normalized


def _document_payload(response: dict[str, Any]) -> tuple[dict[str, Any], str]:
    status = str(response.get("status") or "success").strip().casefold()
    if status not in {"success", "partial_success"}:
        raise RuntimeError("docling_result_failed")
    document = response.get("document")
    if not isinstance(document, dict):
        raise RuntimeError("docling_result_document_missing")
    raw_json = document.get("json_content")
    if isinstance(raw_json, str):
        try:
            raw_json = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError("docling_result_json_invalid") from exc
    if not isinstance(raw_json, dict):
        raise RuntimeError("docling_result_json_missing")
    markdown = document.get("md_content")
    if markdown is None:
        markdown = ""
    if not isinstance(markdown, str):
        raise RuntimeError("docling_result_markdown_invalid")
    return raw_json, markdown


def _ref(value: object) -> str | None:
    if isinstance(value, dict):
        candidate = value.get("$ref") or value.get("ref")
    else:
        candidate = value
    normalized = str(candidate or "").strip()
    return normalized or None


def _bbox(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        return (
            float(value["l"]),
            float(value["t"]),
            float(value["r"]),
            float(value["b"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _span(value: object) -> tuple[int, int] | None:
    if isinstance(value, dict):
        value = value.get("charspan") or value.get("char_span")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            start, end = int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
        if 0 <= start <= end:
            return start, end
    return None


def _safe_markdown(blocks: list[NormalizedBlock]) -> str:
    lines: list[str] = []
    current_page: int | None = None
    for block in blocks:
        if block.page_number != current_page:
            current_page = block.page_number
            if lines:
                lines.append("")
            lines.append(f"# Page {current_page}")
            lines.append("")
        text = re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", block.text)
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


class DoclingParserAdapter:
    provider_name = "docling"

    def __init__(self, client: DoclingClient, *, provider_version: str) -> None:
        self.client = client
        self.provider_version = str(provider_version or "").strip()

    def submit(self, *, stream: BinaryIO, filename: str, media_type: str) -> ParserSubmission:
        if media_type != "application/pdf" or not filename.casefold().endswith(".pdf"):
            raise RuntimeError("docling_phase3_pdf_only")
        response = self.client.request(
            "POST",
            "/v1/convert/file/async",
            files={"files": (filename, stream, media_type)},
            data={
                "from_formats": ["pdf"],
                "to_formats": ["json", "md"],
                "image_export_mode": "placeholder",
                "do_ocr": "false",
                "force_ocr": "false",
                "abort_on_error": "true",
            },
        )
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("docling_invalid_submit_response")
        return ParserSubmission(operation_ref=_bounded_task_ref(value.get("task_id")))

    def status(self, operation_ref: str) -> ParserOperation:
        task_ref = _bounded_task_ref(operation_ref)
        try:
            response = self.client.request("GET", f"/v1/status/poll/{task_ref}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {404, 410}:
                raise ParserOperationUnavailable("docling_operation_unavailable") from exc
            raise
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("docling_invalid_status_response")
        state = str(value.get("task_status") or value.get("status") or "").strip().casefold()
        normalized = {
            "pending": "pending",
            "started": "started",
            "running": "running",
            "success": "success",
            "completed": "success",
            "failure": "failure",
            "failed": "failure",
        }.get(state)
        if normalized is None:
            raise RuntimeError("docling_unknown_task_state")
        error_code = "docling_conversion_failed" if normalized == "failure" else None
        return ParserOperation(operation_ref=task_ref, state=normalized, error_code=error_code)

    def result(
        self,
        *,
        operation_ref: str,
        document_id: str,
        source_version_id: str,
        run_id: str,
    ) -> DocumentArtifact:
        task_ref = _bounded_task_ref(operation_ref)
        try:
            response = self.client.request("GET", f"/v1/result/{task_ref}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {404, 410}:
                raise ParserOperationUnavailable("docling_operation_unavailable") from exc
            raise
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("docling_invalid_result_response")
        raw, _provider_markdown = _document_payload(value)
        pages = self._pages(raw)
        blocks = self._blocks(raw, pages)
        tables = self._tables(raw, pages)
        markdown = _safe_markdown(blocks)
        return DocumentArtifact(
            schema_version="1",
            document_id=document_id,
            source_version_id=source_version_id,
            run_id=run_id,
            provider_name=self.provider_name,
            provider_version=self.provider_version,
            pages=tuple(pages),
            blocks=tuple(blocks),
            quality=QualityReport(
                text_characters=0,
                page_count=len(pages),
                block_count=len(blocks),
                invalid_character_rate=0.0,
                text_coverage_score=0.0,
                reading_order_complete=False,
                processing_complete=False,
                review_reasons=(),
            ),
            raw_provider=value,
            markdown=markdown,
            tables=tuple(tables),
        )

    def ready(self) -> bool:
        return self.client.ready()

    @staticmethod
    def _pages(raw: dict[str, Any]) -> list[NormalizedPage]:
        pages_value = raw.get("pages")
        pages: list[NormalizedPage] = []
        if isinstance(pages_value, dict):
            for key, page in pages_value.items():
                if not isinstance(page, dict):
                    continue
                size = page.get("size") if isinstance(page.get("size"), dict) else {}
                try:
                    number = int(page.get("page_no") or page.get("page_number") or key)
                    width = float(size.get("width") or page.get("width") or 1.0)
                    height = float(size.get("height") or page.get("height") or 1.0)
                except (TypeError, ValueError):
                    continue
                pages.append(
                    NormalizedPage(
                        page_number=max(1, number),
                        width=max(1.0, width),
                        height=max(1.0, height),
                        coordinate_space="points",
                    )
                )
        pages.sort(key=lambda page: page.page_number)
        return pages

    @classmethod
    def _blocks(
        cls,
        raw: dict[str, Any],
        pages: list[NormalizedPage],
    ) -> list[NormalizedBlock]:
        objects: list[dict[str, Any]] = []
        by_ref: dict[str, dict[str, Any]] = {}
        for collection_name in ("texts", "tables", "pictures", "key_value_items", "form_items"):
            collection = raw.get(collection_name)
            if not isinstance(collection, list):
                continue
            for item in collection:
                if not isinstance(item, dict):
                    continue
                objects.append(item)
                item_ref = _ref(item.get("self_ref"))
                if item_ref:
                    by_ref[item_ref] = item
        ordered: list[dict[str, Any]] = []
        body = raw.get("body")
        children = body.get("children") if isinstance(body, dict) else None
        if isinstance(children, list):
            for child in children:
                item = by_ref.get(_ref(child) or "")
                if item is not None and item not in ordered:
                    ordered.append(item)
        ordered.extend(item for item in objects if item not in ordered)
        default_page = pages[0].page_number if pages else 1
        blocks: list[NormalizedBlock] = []
        for index, item in enumerate(ordered):
            text = str(item.get("text") or item.get("orig") or "").strip()
            if not text:
                continue
            provenance = item.get("prov")
            evidence = provenance[0] if isinstance(provenance, list) and provenance else {}
            if not isinstance(evidence, dict):
                evidence = {}
            try:
                page_number = max(1, int(evidence.get("page_no") or default_page))
            except (TypeError, ValueError):
                page_number = default_page
            provider_ref = _ref(item.get("self_ref"))
            blocks.append(
                NormalizedBlock(
                    block_id=f"b{index + 1}",
                    page_number=page_number,
                    kind=str(item.get("label") or "other").strip().casefold()[:40],
                    reading_order=index,
                    text=text,
                    bbox=_bbox(evidence.get("bbox")),
                    char_span=_span(evidence),
                    provider_ref=provider_ref,
                )
            )
        if not pages and blocks:
            page_numbers = sorted({block.page_number for block in blocks})
            pages.extend(
                NormalizedPage(
                    page_number=number,
                    width=1.0,
                    height=1.0,
                    coordinate_space="unknown",
                )
                for number in page_numbers
            )
        return blocks

    @classmethod
    def _tables(
        cls,
        raw: dict[str, Any],
        pages: list[NormalizedPage],
    ) -> list[NormalizedTable]:
        values = raw.get("tables")
        if not isinstance(values, list):
            return []
        default_page = pages[0].page_number if pages else 1
        tables: list[NormalizedTable] = []
        for table_index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            provenance = item.get("prov")
            evidence = provenance[0] if isinstance(provenance, list) and provenance else {}
            if not isinstance(evidence, dict):
                evidence = {}
            try:
                page_number = max(1, int(evidence.get("page_no") or default_page))
            except (TypeError, ValueError):
                page_number = default_page
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            raw_cells = data.get("table_cells") if isinstance(data, dict) else None
            cells: list[NormalizedTableCell] = []
            if isinstance(raw_cells, list):
                for cell_index, cell in enumerate(raw_cells):
                    if not isinstance(cell, dict):
                        continue
                    try:
                        row_start = max(0, int(cell.get("start_row_offset_idx") or 0))
                        row_end = max(row_start + 1, int(cell.get("end_row_offset_idx") or row_start + 1))
                        column_start = max(0, int(cell.get("start_col_offset_idx") or 0))
                        column_end = max(
                            column_start + 1,
                            int(cell.get("end_col_offset_idx") or column_start + 1),
                        )
                    except (TypeError, ValueError):
                        continue
                    cells.append(
                        NormalizedTableCell(
                            cell_id=f"t{table_index + 1}c{cell_index + 1}",
                            row_index=row_start,
                            column_index=column_start,
                            row_span=row_end - row_start,
                            column_span=column_end - column_start,
                            text=str(cell.get("text") or "")[:20000],
                            bbox=_bbox(cell.get("bbox")),
                            provider_ref=_ref(cell.get("self_ref")),
                        )
                    )
            row_count = int(data.get("num_rows") or 0) if isinstance(data, dict) else 0
            column_count = int(data.get("num_cols") or 0) if isinstance(data, dict) else 0
            if cells:
                row_count = max(row_count, max(cell.row_index + cell.row_span for cell in cells))
                column_count = max(
                    column_count,
                    max(cell.column_index + cell.column_span for cell in cells),
                )
            tables.append(
                NormalizedTable(
                    table_id=f"t{table_index + 1}",
                    page_number=page_number,
                    reading_order=table_index,
                    row_count=max(0, row_count),
                    column_count=max(0, column_count),
                    bbox=_bbox(evidence.get("bbox")),
                    provider_ref=_ref(item.get("self_ref")),
                    cells=tuple(cells),
                )
            )
        return tables
