from __future__ import annotations

import io

import httpx
import pytest

from app.integrations.docling.adapter import DoclingParserAdapter
from app.integrations.docling.client import DoclingClient
from app.skills.domains.documents.ports import ParserOperationUnavailable


def _client(tmp_path, handler, *, max_response_bytes=4096) -> DoclingClient:
    key = tmp_path / "docling.key"
    key.write_text("test-secret", encoding="utf-8")
    return DoclingClient(
        base_url="http://docling-serve:5001",
        api_key_path=str(key),
        server_version="1.30.0",
        timeout_seconds=10,
        max_response_bytes=max_response_bytes,
        transport=httpx.MockTransport(handler),
    )


def _result_payload() -> dict:
    return {
        "status": "success",
        "document": {
            "md_content": "[provider markdown must not be trusted](https://example.com)",
            "json_content": {
                "pages": {"1": {"page_no": 1, "size": {"width": 612, "height": 792}}},
                "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
                "texts": [
                    {
                        "self_ref": "#/texts/0",
                        "label": "section_header",
                        "text": "Monthly # Statement",
                        "prov": [{"page_no": 1, "bbox": {"l": 10, "t": 20, "r": 200, "b": 40}}],
                    },
                    {
                        "self_ref": "#/texts/1",
                        "label": "paragraph",
                        "text": "Evidence-bearing native text with enough characters.",
                        "prov": [{"page_no": 1, "bbox": {"l": 10, "t": 50, "r": 400, "b": 90}}],
                    },
                ],
                "tables": [
                    {
                        "self_ref": "#/tables/0",
                        "prov": [{"page_no": 1, "bbox": {"l": 10, "t": 100, "r": 400, "b": 200}}],
                        "data": {
                            "num_rows": 1,
                            "num_cols": 1,
                            "table_cells": [
                                {
                                    "text": "Total $5",
                                    "start_row_offset_idx": 0,
                                    "end_row_offset_idx": 1,
                                    "start_col_offset_idx": 0,
                                    "end_col_offset_idx": 1,
                                }
                            ],
                        },
                    }
                ],
            },
        },
    }


def test_docling_uses_only_local_file_async_api_and_normalizes_evidence(tmp_path) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        seen.append((request.method, request.url.path, request.headers, body))
        if request.url.path == "/version":
            return httpx.Response(200, json={"docling-serve": "1.30.0"})
        if request.url.path == "/v1/convert/file/async":
            assert b'filename="statement.pdf"' in body
            assert b"%PDF-1.4" in body
            assert b"http://" not in body and b"https://" not in body
            return httpx.Response(200, json={"task_id": "task-1"})
        if request.url.path == "/v1/status/poll/task-1":
            return httpx.Response(200, json={"task_status": "success"})
        if request.url.path == "/v1/result/task-1":
            return httpx.Response(200, json=_result_payload())
        raise AssertionError(request.url.path)

    client = _client(tmp_path, handler)
    adapter = DoclingParserAdapter(client, provider_version="1.30.0")
    assert adapter.ready()
    submission = adapter.submit(
        stream=io.BytesIO(b"%PDF-1.4\n%%EOF"),
        filename="statement.pdf",
        media_type="application/pdf",
    )
    assert adapter.status(submission.operation_ref).state == "success"
    artifact = adapter.result(
        operation_ref=submission.operation_ref,
        document_id="doc-1",
        source_version_id="source-1",
        run_id="run-1",
    )
    assert seen[0][2]["x-api-key"] == "test-secret"
    assert artifact.pages[0].page_number == 1
    assert artifact.blocks[0].provider_ref == "#/texts/0"
    assert artifact.blocks[0].bbox == (10.0, 20.0, 200.0, 40.0)
    assert artifact.tables[0].cells[0].text == "Total $5"
    assert "provider markdown" not in artifact.markdown
    assert "Monthly \\# Statement" in artifact.markdown
    client.close()


def test_docling_rejects_non_pdf_remote_base_and_oversized_response(tmp_path) -> None:
    key = tmp_path / "docling.key"
    key.write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError, match="local/private"):
        DoclingClient(
            base_url="https://docling.example.com",
            api_key_path=str(key),
            server_version="1.30.0",
            timeout_seconds=10,
        )

    def large(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 2048)

    client = _client(tmp_path, large, max_response_bytes=1024)
    with pytest.raises(RuntimeError, match="docling_response_too_large"):
        client.request("GET", "/version")
    adapter = DoclingParserAdapter(client, provider_version="1.30.0")
    with pytest.raises(RuntimeError, match="pdf_only"):
        adapter.submit(
            stream=io.BytesIO(b"image"),
            filename="image.png",
            media_type="image/png",
        )
    client.close()


@pytest.mark.parametrize("path", ["/v1/status/poll/missing", "/v1/result/missing"])
def test_docling_maps_expired_provider_operations(tmp_path, path: str) -> None:
    client = _client(tmp_path, lambda request: httpx.Response(404, request=request))
    adapter = DoclingParserAdapter(client, provider_version="1.30.0")

    with pytest.raises(ParserOperationUnavailable, match="docling_operation_unavailable"):
        if "/status/" in path:
            adapter.status("missing")
        else:
            adapter.result(
                operation_ref="missing",
                document_id="doc-1",
                source_version_id="source-1",
                run_id="run-1",
            )
    client.close()
