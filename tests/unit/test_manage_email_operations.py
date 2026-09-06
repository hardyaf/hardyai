from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.skills.domains.email_agent.storage import EmailAgentSQLiteStorage
from scripts.manage_email_operations import _legacy_audit, _legacy_quarantine
from tests.unit.test_email_agent_storage import NOW, message_record


def test_operator_cli_bootstraps_repository_imports_outside_checkout(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts" / "manage_email_operations.py"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Audit and manage durable Email operations" in completed.stdout


def _legacy_row(storage: EmailAgentSQLiteStorage) -> dict:
    storage.upsert_message(record=message_record(), now=NOW)
    return storage.enqueue_label_operation(
        gmail_message_id="m1",
        taxonomy_version="shared-v1",
        logical_category_key="work_mail",
        gmail_label_name="Jarvis/Work",
        operation_type="add",
        idempotency_key="legacy-fixture",
        max_attempts=3,
        now=NOW,
    )


def test_legacy_audit_and_quarantine_cancel_only_queued_rows_without_provider(tmp_path):
    database = tmp_path / "core.db"
    storage = EmailAgentSQLiteStorage(str(database))
    legacy = _legacy_row(storage)
    storage.close()
    backup = tmp_path / "core.backup.db"
    shutil.copyfile(database, backup)

    audit = _legacy_audit(str(database))
    dry_run = _legacy_quarantine(str(database), apply=False, verified_backup=None)
    applied = _legacy_quarantine(
        str(database),
        apply=True,
        verified_backup=str(backup),
    )
    reopened = EmailAgentSQLiteStorage(str(database))
    row = reopened.get_label_operation(operation_id=legacy["operation_id"])

    assert audit["integrity_check"] == "ok"
    assert audit["counts"] == {"queued": 1}
    assert dry_run == {
        "mode": "dry_run",
        "eligible_count": 1,
        "active_claim_count": 0,
        "would_call_provider": False,
    }
    assert applied["cancelled_count"] == 1
    assert applied["remaining_queued_count"] == 0
    assert applied["would_call_provider"] is False
    assert row["status"] == "cancelled"
    assert row["last_error_code"] == "legacy_automatic_reconciliation_quarantined"
    reopened.close()


def test_legacy_quarantine_fails_closed_while_a_claim_is_active(tmp_path):
    database = tmp_path / "core.db"
    storage = EmailAgentSQLiteStorage(str(database))
    _legacy_row(storage)
    storage.claim_label_operations(
        lease_owner="legacy-worker",
        now=NOW,
        lease_expires_at="2026-08-16T16:00:00+00:00",
        limit=1,
    )
    storage.close()
    backup = tmp_path / "core.backup.db"
    shutil.copyfile(database, backup)

    with pytest.raises(RuntimeError, match="zero active label claims"):
        _legacy_quarantine(
            str(database),
            apply=True,
            verified_backup=str(backup),
        )

    assert _legacy_audit(str(database))["counts"] == {"claimed": 1}
