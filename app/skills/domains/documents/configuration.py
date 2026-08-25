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
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
