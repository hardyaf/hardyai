from __future__ import annotations

from typing import Any


def build_operation_receipt(
    *,
    intent: str,
    entities: dict[str, Any],
    context: dict[str, Any],
    result: dict[str, Any],
    services: dict[str, Any],
) -> dict[str, Any] | None:
    del entities, services
    if intent != "documents.reprocess" or result.get("status") not in {"queued", "processing"}:
        return None
    run_id = str(result.get("run_id") or "").strip()
    document_id = str(result.get("document_id") or "").strip()
    if not run_id or not document_id:
        return None
    return {
        "operation_id": run_id,
        "idempotency_key": f"document-reprocess:{context.get('request_id') or run_id}",
        "capability": "documents",
        "action": intent,
        "resource_key": document_id,
        "status": "queued",
        "expected_effect": {"processing_run_id": run_id},
        "validator_name": "document_processing_run",
        "validator_version": "v1",
        "resource_locator": {"document_id": document_id, "run_id": run_id},
    }
