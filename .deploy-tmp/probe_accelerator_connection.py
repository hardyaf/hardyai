from __future__ import annotations

import os

import httpx

from app.accelerator.client import accelerator_request_headers


url = os.environ["LOCAL_MODEL_URL"].rstrip("/") + "/health"
try:
    response = httpx.get(
        url,
        headers=accelerator_request_headers("runtime_health"),
        timeout=5,
    )
    print({"url": url, "status_code": response.status_code})
except Exception as exc:
    print({"url": url, "error_type": type(exc).__name__, "error": str(exc)})
    raise SystemExit(1)
