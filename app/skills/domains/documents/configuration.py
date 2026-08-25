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
