from __future__ import annotations

import json
import sqlite3
import subprocess
import sys

import pytest

from app.db.connection import open_readonly_sqlite_connection
from app.jobs.repository import DurableJobRepository
from app.operations.document_health import document_operational_health
from scripts.generate_document_release_manifest import build_manifest
from tests.unit.test_document_phase7_proposals import _ready_run


@pytest.mark.parametrize(
    "script",
    (
        "scripts/check_document_operations.py",
        "scripts/benchmark_discord_conversation.py",
        "scripts/benchmark_accelerator_coexistence.py",
    ),
)
def test_phase11_operator_clis_can_be_invoked_by_file_path(script: str) -> None:
    result = subprocess.run(
        [sys.executable, script, "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_shared_readonly_connection_rejects_mutation(tmp_path) -> None:
    jobs = DurableJobRepository(str(tmp_path / "core.db"))
    jobs.close()
    _, connection = open_readonly_sqlite_connection(str(tmp_path / "core.db"))
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden(value TEXT)")
    finally:
        connection.close()


def test_operational_health_is_content_free_and_actionable(tmp_path) -> None:
    storage = tmp_path / "storage"
    (storage / "jarvis" / "spool").mkdir(parents=True)
    (storage / "backups" / "phase11").mkdir(parents=True)
    (storage / "backups" / "phase11" / "manifest.json").write_text("{}", encoding="utf-8")
    repository, _, _ = _ready_run(tmp_path)
    jobs = DurableJobRepository(str(tmp_path / "core.db"))
    jobs.record_worker_heartbeat(
        worker_type="documents",
        worker_id="phase11-worker",
        status="idle",
        metadata={"result_count": 0},
    )

    result = document_operational_health(
        core_database=tmp_path / "core.db",
        documents_database=tmp_path / "documents.db",
        storage_root=storage,
        spool_quota_bytes=1024,
        min_free_bytes=1,
    )

    assert result["status"] == "ok"
    assert result["alerts"] == []
    encoded = json.dumps(result, sort_keys=True)
    assert "notes.pdf" not in encoded
    assert "literal_text" not in encoded
    assert "payload_json" not in encoded
    jobs.close()
    repository.close()


def test_operational_health_alerts_on_document_dead_letter(tmp_path) -> None:
    storage = tmp_path / "storage"
    (storage / "jarvis" / "spool").mkdir(parents=True)
    (storage / "backups" / "phase11").mkdir(parents=True)
    (storage / "backups" / "phase11" / "manifest.json").write_text("{}", encoding="utf-8")
    repository, _, _ = _ready_run(tmp_path)
    jobs = DurableJobRepository(str(tmp_path / "core.db"))
    jobs.record_worker_heartbeat(
        worker_type="documents", worker_id="phase11-worker", status="idle"
    )
    job = jobs.enqueue_job(
        job_type="document.process.v1",
        aggregate_id="opaque-document-ref",
        idempotency_key="phase11-dead-letter",
        payload={"document_id": "opaque-document-ref"},
    )
    claimed = jobs.claim_jobs(
        job_type="document.process.v1", worker_id="test", limit=1, lease_seconds=60
    )[0]
    jobs.dead_letter_job(
        job_id=str(job["job_id"]),
        worker_id="test",
        fencing_token=int(claimed["lease_fencing_token"]),
        error_code="synthetic_failure",
    )

    result = document_operational_health(
        core_database=tmp_path / "core.db",
        documents_database=tmp_path / "documents.db",
        storage_root=storage,
        spool_quota_bytes=1024,
        min_free_bytes=1,
    )
    assert result["status"] == "critical"
    assert "document_jobs_dead_lettered" in {item["code"] for item in result["alerts"]}
    jobs.close()
    repository.close()


def test_release_manifest_pins_application_and_external_compose_images() -> None:
    digest = "sha256:" + "a" * 64
    paddle_digest = "sha256:" + "b" * 64
    vl_digest = "sha256:" + "c" * 64
    manifest = build_manifest(
        application_digest=digest,
        source_revision="phase11-test",
        local_image_digests={
            "hardyai-paddleocr:3.7.0": paddle_digest,
            "hardyai-paddleocr-vl:3.6.0-pipeline1.6": vl_digest,
        },
    )
    assert manifest["application_image_digest"] == digest
    assert manifest["compose_image_digests"]["paddleocr-serve"] == paddle_digest
    assert manifest["compose_image_digests"]["paddleocr-vl-serve"] == vl_digest
    assert manifest["unresolved_image_digests"] == []
    assert manifest["unpinned_external_images"] == []
    assert manifest["inputs"]["requirements.txt"]["sha256"]
    assert manifest["python_packages"]
