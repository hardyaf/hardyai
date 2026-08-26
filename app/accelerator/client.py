from __future__ import annotations

import os
from pathlib import Path

from app.accelerator.service import LANE_PRIORITIES


def accelerator_request_headers(lane: str) -> dict[str, str]:
    normalized = str(lane or "").strip().casefold()
    if normalized not in LANE_PRIORITIES:
        raise RuntimeError("accelerator_lane_not_allowed")
    headers = {"X-HardyAI-Accelerator-Lane": normalized}
    key_path_value = str(os.getenv("ACCELERATOR_ADMISSION_API_KEY_PATH", "") or "").strip()
    required = str(os.getenv("ACCELERATOR_ADMISSION_REQUIRED", "false")).strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not key_path_value:
        if required:
            raise RuntimeError("accelerator_admission_key_path_missing")
        return headers
    key_path = Path(key_path_value).expanduser()
    if key_path.is_symlink():
        raise RuntimeError("accelerator_admission_key_path_symlink")
    try:
        key = key_path.resolve().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("accelerator_admission_key_unavailable") from exc
    if not key or len(key) > 512 or any(character.isspace() for character in key):
        raise RuntimeError("accelerator_admission_key_invalid")
    headers["X-HardyAI-Accelerator-Key"] = key
    return headers
