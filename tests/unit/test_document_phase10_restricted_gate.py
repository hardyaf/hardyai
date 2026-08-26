from __future__ import annotations

import json
import sqlite3

from app.restricted_documents.readiness import evaluate_restricted_workflow
from tests.unit.test_document_phase7_proposals import _ready_run


def test_restricted_workflow_is_disabled_by_default() -> None:
    readiness = evaluate_restricted_workflow(
        enabled=False,
        cipher_configured=False,
        isolated_store_configured=False,
        security_review_id="",
        recovery_attestation_path="",
    )
    assert not readiness.ready
    assert readiness.public_view() == {
        "enabled": False,
        "ready": False,
        "status": "disabled",
        "reasons": ["feature_disabled"],
    }


def test_enabling_without_every_security_gate_fails_closed(tmp_path) -> None:
    attestation = tmp_path / "restore-attestation.json"
    attestation.write_text("{}", encoding="utf-8")
    readiness = evaluate_restricted_workflow(
        enabled=True,
        cipher_configured=False,
        isolated_store_configured=False,
        security_review_id="review-2026-08",
        recovery_attestation_path=str(attestation),
    )
    assert not readiness.ready
    assert set(readiness.reasons) == {
        "authenticated_cipher_adapter_unavailable",
        "isolated_restricted_store_unavailable",
    }


def test_symlink_restore_attestation_is_rejected(tmp_path) -> None:
    target = tmp_path / "real.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        return
    readiness = evaluate_restricted_workflow(
        enabled=True,
        cipher_configured=True,
        isolated_store_configured=True,
        security_review_id="review-2026-08",
        recovery_attestation_path=str(link),
    )
    assert not readiness.ready
    assert readiness.reasons == ("clean_restore_attestation_missing",)


def test_denied_restricted_access_audit_is_content_free_and_idempotent(tmp_path) -> None:
    repository, record, _ = _ready_run(tmp_path)
    first = repository.record_restricted_access(
        document_id=record.document_id,
        actor_principal="operator",
        purpose_code="human_review",
        operation="restricted.read",
        outcome="denied",
        reason_code="restricted_workflow_not_ready",
        request_id="phase10-audit-1",
    )
    second = repository.record_restricted_access(
        document_id=record.document_id,
        actor_principal="operator",
        purpose_code="human_review",
        operation="restricted.read",
        outcome="denied",
        reason_code="restricted_workflow_not_ready",
        request_id="phase10-audit-1",
    )
    assert first["audit_id"] == second["audit_id"]
    rows = repository.list_restricted_access_audit(document_id=record.document_id)
    assert len(rows) == 1 and rows[0]["outcome"] == "denied"
    serialized = json.dumps(rows)
    assert "X12345678" not in serialized
    with sqlite3.connect(tmp_path / "documents.db") as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(document_restricted_access_audit)")
        }
    assert not ({"value", "plaintext", "ciphertext", "field_name"} & columns)
    repository.close()
