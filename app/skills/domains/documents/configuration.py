from __future__ import annotations

import hashlib
import json
from typing import Any


def native_docling_configuration_sha256(settings: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "artifact_schema": "1",
                "docling_version": str(settings.docling_server_version),
                "image_digest": str(settings.docling_image_digest),
                "input": ["pdf"],
                "ocr": False,
                "outputs": ["json", "md"],
                "safe_enrichment": bool(settings.documents_safe_extraction_enabled),
                "classification_contract": "document-classification-v1",
                "extraction_contract": "document-extraction-v1",
                "note_proposals": bool(settings.documents_note_proposals_enabled),
                "contact_proposals": bool(settings.documents_contact_proposals_enabled),
                "document_intelligence": bool(settings.documents_intelligence_enabled),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()


def conventional_ocr_configuration_sha256(settings: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "artifact_schema": "2",
                "paddleocr_version": str(settings.paddleocr_server_version),
                "image_digest": str(settings.paddleocr_image_digest),
                "model_tier": str(settings.paddleocr_model_tier),
                "model_version": "PP-OCRv6",
                "device": "cpu",
                "inputs": ["pdf", "jpeg", "png"],
                "orientation": False,
                "unwarping": False,
                "textline_orientation": False,
                "safe_enrichment": bool(settings.documents_safe_extraction_enabled),
                "classification_contract": "document-classification-v1",
                "extraction_contract": "document-extraction-v1",
                "note_proposals": bool(settings.documents_note_proposals_enabled),
                "contact_proposals": bool(settings.documents_contact_proposals_enabled),
                "document_intelligence": bool(settings.documents_intelligence_enabled),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()


def vlm_fallback_configuration_sha256(settings: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "artifact_schema": "3",
                "framework_version": str(settings.paddleocr_vl_framework_version),
                "pipeline_version": str(settings.paddleocr_vl_pipeline_version),
                "image_digest": str(settings.paddleocr_vl_image_digest),
                "model": "PaddleOCR-VL-1.6-0.9B",
                "layout_model": "PP-DocLayoutV3",
                "device": "gpu",
                "inputs": ["jpeg", "png"],
                "orientation": False,
                "unwarping": False,
                "execution": "one_request_subprocess",
                "max_new_tokens": int(settings.paddleocr_vl_max_new_tokens),
                "requires_human_review": True,
                "safe_enrichment": bool(settings.documents_safe_extraction_enabled),
                "classification_contract": "document-classification-v1",
                "extraction_contract": "document-extraction-v1",
                "note_proposals": bool(settings.documents_note_proposals_enabled),
                "contact_proposals": bool(settings.documents_contact_proposals_enabled),
                "document_intelligence": bool(settings.documents_intelligence_enabled),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
