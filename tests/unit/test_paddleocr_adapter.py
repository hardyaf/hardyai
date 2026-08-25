from __future__ import annotations

import io

import httpx
import pytest

from app.integrations.paddleocr.adapter import PaddleOCRParserAdapter
from app.integrations.paddleocr.client import PaddleOCRClient
from app.skills.domains.documents.ports import ParserOperationUnavailable
from app.skills.domains.documents.quality import evaluate_conventional_ocr_artifact


def _client(tmp_path, handler) -> PaddleOCRClient:
    key = tmp_path / "ocr.key"
    key.write_text("secret-key", encoding="utf-8")
    return PaddleOCRClient(
        base_url="http://paddleocr-serve:8030",
        api_key_path=str(key),
        server_version="3.7.0",
        transport=httpx.MockTransport(handler),
    )


def test_adapter_preserves_geometry_confidence_and_page_provenance(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "secret-key"
        if request.url.path == "/ready":
            return httpx.Response(200, json={"status": "ready", "version": "3.7.0"})
        assert request.url.path == "/ocr"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "provider_version": "3.7.0",
                "language": "multilingual",
                "pages": [
                    {
                        "page_index": 0,
                        "width": 800,
                        "height": 600,
                        "rec_texts": ["TOTAL $12.34", "08/25/2026"],
                        "rec_scores": [0.98, 0.94],
                        "rec_boxes": [[10, 20, 220, 55], [10, 70, 180, 100]],
                    }
                ],
            },
        )

    client = _client(tmp_path, handler)
    adapter = PaddleOCRParserAdapter(client, provider_version="3.7.0")
    assert adapter.ready()
    submission = adapter.submit(
        stream=io.BytesIO(b"image bytes"),
        filename="receipt.png",
        media_type="image/png",
    )
    assert adapter.status(submission.operation_ref).state == "success"
    artifact = adapter.result(
        operation_ref=submission.operation_ref,
        document_id="document-1",
        source_version_id="source-1",
        run_id="run-1",
    )
    assert artifact.schema_version == "2"
    assert artifact.pages[0].coordinate_space == "pixels"
    assert artifact.blocks[0].bbox == (10.0, 20.0, 220.0, 55.0)
    assert artifact.blocks[0].confidence == 0.98
    assert artifact.blocks[0].provider_ref == "#/pages/0/lines/0"
    evaluated = evaluate_conventional_ocr_artifact(artifact)
    assert evaluated.quality.processing_complete
    client.close()


def test_adapter_rejects_unscoped_ids_and_unsupported_media(tmp_path) -> None:
    client = _client(tmp_path, lambda _request: httpx.Response(500))
    adapter = PaddleOCRParserAdapter(client, provider_version="3.7.0")
    with pytest.raises(RuntimeError, match="media_type_unsupported"):
        adapter.submit(stream=io.BytesIO(b"x"), filename="x.gif", media_type="image/gif")
    with pytest.raises(ParserOperationUnavailable):
        adapter.status("ocr-missing")
    client.close()


def test_client_maps_allowlisted_http_error_without_response_content(tmp_path) -> None:
    client = _client(
        tmp_path,
        lambda _request: httpx.Response(
            400,
            json={"detail": "paddleocr_extension_mismatch", "untrusted": "must not leak"},
        ),
    )

    with pytest.raises(RuntimeError) as error:
        client.infer(
            stream=io.BytesIO(b"image"),
            filename="receipt.jpg",
            media_type="image/jpeg",
        )

    assert str(error.value) == "paddleocr_http_400_paddleocr_extension_mismatch"
    assert error.value.code == "paddleocr_http_400_paddleocr_extension_mismatch"
    assert "untrusted" not in str(error.value)
    client.close()


def test_low_confidence_ocr_fails_closed_to_review(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "success",
                "pages": [{
                    "page_index": 0,
                    "width": 10,
                    "height": 10,
                    "rec_texts": ["uncertain total 10.00"],
                    "rec_scores": [0.22],
                    "rec_boxes": [[0, 0, 10, 10]],
                }],
            },
        )

    client = _client(tmp_path, handler)
    adapter = PaddleOCRParserAdapter(client, provider_version="3.7.0")
    submission = adapter.submit(
        stream=io.BytesIO(b"image"), filename="receipt.jpg", media_type="image/jpeg"
    )
    artifact = adapter.result(
        operation_ref=submission.operation_ref,
        document_id="d",
        source_version_id="s",
        run_id="r",
    )
    evaluated = evaluate_conventional_ocr_artifact(artifact)
    assert not evaluated.quality.processing_complete
    assert "ocr_low_mean_confidence" in evaluated.quality.review_reasons
    client.close()
