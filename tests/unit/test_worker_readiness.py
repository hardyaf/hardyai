from __future__ import annotations

import json

from app.jobs.repository import DurableJobRepository
from scripts.check_worker_readiness import main


def test_readiness_checks_heartbeat_and_writes_content_free_baselines(tmp_path, capsys) -> None:
    database = tmp_path / "core.db"
    repository = DurableJobRepository(str(database))
    repository.record_worker_heartbeat(
        worker_type="action_approval",
        worker_id="worker-1",
        status="idle",
        metadata={"claimed_count": 0},
    )
    repository.close()
    dead_letters = tmp_path / "dead-letters.json"
    email_dead_letters = tmp_path / "email-dead-letters.json"
    requirements = tmp_path / "requirements.json"

    assert main(
        [
            "--database",
            str(database),
            "--require-worker",
            "action_approval=120",
            "--write-dead-letter-baseline",
            str(dead_letters),
            "--write-email-dead-letter-baseline",
            str(email_dead_letters),
            "--write-runtime-requirements",
            str(requirements),
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ready"
    assert output["worker_requirements"][0]["type"] == "action_approval"
    assert json.loads(dead_letters.read_text(encoding="utf-8"))["counts"] == {}
    assert json.loads(email_dead_letters.read_text(encoding="utf-8"))["counts"] == {
        "email_label_operations": 0,
        "email_mailbox_operations": 0,
    }
    manifest = json.loads(requirements.read_text(encoding="utf-8"))
    assert set(manifest) == {
        "schema_version",
        "required_services",
        "required_workers",
        "email_consumer_required",
        "reasons",
    }


def test_readiness_fails_closed_for_unknown_prospective_operation(tmp_path, capsys) -> None:
    database = tmp_path / "core.db"
    repository = DurableJobRepository(str(database))
    repository.close()

    assert main(
        [
            "--database",
            str(database),
            "--prospective-operation",
            "unknown.operation",
        ]
    ) == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "not_ready",
        "reason": "operation_unknown:unknown.operation",
    }
