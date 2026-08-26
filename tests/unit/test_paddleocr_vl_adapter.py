from __future__ import annotations

from io import BytesIO

from app.integrations.paddleocr_vl.adapter import PaddleOCRVLParserAdapter
from app.skills.domains.documents.quality import evaluate_vlm_fallback_artifact


class FakeVLMClient:
    def infer(self, **_kwargs):
        return {
            "status": "success",
            "pages": [
                {
                    "page_index": 0,
                    "width": 800,
                    "height": 600,
                    "blocks": [
                        {
                            "kind": "text",
                            "text": "Invoice total $123.45 due tomorrow",
                            "bbox": [10, 20, 500, 80],
                            "confidence": None,
                        }
                    ],
                }
            ],
        }

    def ready(self) -> bool:
        return True


def test_vlm_adapter_normalizes_full_pipeline_result_and_forces_review() -> None:
    adapter = PaddleOCRVLParserAdapter(FakeVLMClient(), provider_version="3.6.0")
    submission = adapter.submit(
        stream=BytesIO(b"image"),
        filename="invoice.jpg",
        media_type="image/jpeg",
    )
    assert adapter.status(submission.operation_ref).state == "success"
    artifact = adapter.result(
        operation_ref=submission.operation_ref,
        document_id="document-1",
        source_version_id="source-1",
        run_id="run-1",
    )

    evaluated = evaluate_vlm_fallback_artifact(
        artifact,
        reference_texts=("uncertain conventional result",),
    )
    assert evaluated.schema_version == "3"
    assert evaluated.blocks[0].text == "Invoice total $123.45 due tomorrow"
    assert evaluated.quality.processing_complete is False
    assert "vlm_human_review_required" in evaluated.quality.review_reasons
    assert "vlm_conventional_disagreement" in evaluated.quality.review_reasons
    assert "vlm_critical_field_present" in evaluated.quality.review_reasons
