from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


_SYMBOL = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_WORKERS = {
    "action_approval": ("action-approval-worker", 120),
    "ticket_review": ("ticket-review", 60),
    "documents": ("document-worker", 120),
    "email_operations": ("email-operations-worker", 120),
    "plane_sync": ("plane-sync", 60),
}
_DEPENDENCIES = {
    "action_approval",
    "ticket_review",
    "document_processing",
    "email_operations",
}
_REASON_CODES = {
    "protected_flag_enabled",
    "active_operation_dependency",
    "prospective_operation_dependency",
    "nonterminal_action_proposal",
    "unfinished_durable_job",
    "unfinished_domain_operation",
}
_TERMINAL_PROPOSAL_STATES = {
    "executed",
    "rejected",
    "expired",
    "superseded",
    "canceled",
    "denied",
    "failed_terminal",
}
_KNOWN_NO_EXTRA_SERVICE_JOBS = {
    "write.memory_interaction.v1",
    "document.discord_completion.v1",
    "model.compute_budget_notice.v1",
}


class ReadinessError(ValueError):
    pass


def _open_readonly(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve(strict=True)
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _parse_instant(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReadinessError("worker_heartbeat_time_invalid") from exc
    if parsed.tzinfo is None:
        raise ReadinessError("worker_heartbeat_time_invalid")
    return parsed.astimezone(UTC)


def _parse_worker_requirement(value: str) -> tuple[str, int]:
    worker_type, separator, age = str(value).partition("=")
    if not separator or worker_type not in _WORKERS:
        raise ReadinessError("worker_requirement_invalid")
    try:
        max_age = int(age)
    except ValueError as exc:
        raise ReadinessError("worker_requirement_invalid") from exc
    if not 1 <= max_age <= 86_400:
        raise ReadinessError("worker_requirement_invalid")
    return worker_type, max_age


def _heartbeat_status(
    connection: sqlite3.Connection,
    requirements: list[tuple[str, int]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    if requirements and "worker_heartbeats" not in _tables(connection):
        raise ReadinessError("worker_heartbeat_table_missing")
    output: list[dict[str, Any]] = []
    for worker_type, max_age in requirements:
        row = connection.execute(
            """
            SELECT status, last_seen_at, last_error_code
            FROM worker_heartbeats WHERE worker_type=?
            ORDER BY last_seen_at DESC LIMIT 1
            """,
            (worker_type,),
        ).fetchone()
        if row is None:
            raise ReadinessError(f"worker_missing:{worker_type}")
        age = max(0, int((now - _parse_instant(row["last_seen_at"])).total_seconds()))
        status = str(row["status"] or "").strip().casefold()
        if age > max_age:
            raise ReadinessError(f"worker_stale:{worker_type}")
        if status in {"degraded", "failed", "dead_letter", "disabled"} or row[
            "last_error_code"
        ]:
            raise ReadinessError(f"worker_degraded:{worker_type}")
        output.append(
            {
                "type": worker_type,
                "status": status,
                "age_seconds": age,
                "max_age_seconds": max_age,
            }
        )
    return output


def _dead_letter_counts(connection: sqlite3.Connection) -> dict[str, int]:
    if "durable_jobs" not in _tables(connection):
        raise ReadinessError("durable_jobs_table_missing")
    return {
        str(row["job_type"]): int(row["count"])
        for row in connection.execute(
            """
            SELECT job_type, COUNT(*) AS count FROM durable_jobs
            WHERE status='dead_letter' GROUP BY job_type ORDER BY job_type
            """
        ).fetchall()
    }


def _email_dead_letter_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = _tables(connection)
    result: dict[str, int] = {}
    for table in ("email_label_operations", "email_mailbox_operations"):
        if table not in tables:
            result[table] = 0
            continue
        columns = {
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "status" not in columns:
            raise ReadinessError(f"email_operation_table_invalid:{table}")
        result[table] = int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE status IN ('dead_letter','failed_terminal')"
            ).fetchone()[0]
        )
    return result


def _baseline(path: Path, *, kind: str, counts: dict[str, int], write: bool) -> None:
    payload = {"schema_version": 1, "kind": kind, "counts": dict(sorted(counts.items()))}
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        return
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadinessError(f"{kind}_baseline_invalid") from exc
    if not isinstance(expected, dict) or set(expected) != {"schema_version", "kind", "counts"}:
        raise ReadinessError(f"{kind}_baseline_invalid")
    if expected.get("schema_version") != 1 or expected.get("kind") != kind or not isinstance(
        expected.get("counts"), dict
    ):
        raise ReadinessError(f"{kind}_baseline_invalid")
    for key, value in counts.items():
        baseline_value = expected["counts"].get(key, 0)
        if not isinstance(baseline_value, int) or isinstance(baseline_value, bool):
            raise ReadinessError(f"{kind}_baseline_invalid")
        if value > baseline_value:
            raise ReadinessError(f"{kind}_baseline_increased:{key}")


def _catalog(connection: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
    tables = _tables(connection)
    if "skills" not in tables:
        raise ReadinessError("skill_catalog_missing")
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(skills)").fetchall()
    }
    if not {"active", "main_tools_json"}.issubset(columns):
        raise ReadinessError("skill_catalog_invalid")
    result: dict[str, tuple[str, ...]] = {}
    for row in connection.execute(
        "SELECT main_tools_json FROM skills WHERE active=1 AND main_tools_json IS NOT NULL"
    ).fetchall():
        try:
            descriptors = json.loads(str(row["main_tools_json"]))
        except json.JSONDecodeError as exc:
            raise ReadinessError("skill_catalog_descriptor_invalid") from exc
        if not isinstance(descriptors, list):
            raise ReadinessError("skill_catalog_descriptor_invalid")
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                raise ReadinessError("skill_catalog_descriptor_invalid")
            tool_id = str(descriptor.get("tool_id") or "").strip().casefold()
            dependencies = descriptor.get("runtime_dependencies")
            if not _SYMBOL.fullmatch(tool_id) or not isinstance(dependencies, list):
                raise ReadinessError("skill_catalog_descriptor_invalid")
            normalized = tuple(str(item).strip().casefold() for item in dependencies)
            if any(item not in _DEPENDENCIES for item in normalized):
                raise ReadinessError(f"runtime_dependency_unknown:{tool_id}")
            if tool_id in result and result[tool_id] != normalized:
                raise ReadinessError(f"skill_catalog_conflict:{tool_id}")
            result[tool_id] = normalized
    return result


def _requirements(
    connection: sqlite3.Connection,
    *,
    prospective_operations: list[str],
) -> dict[str, Any]:
    from app.config import settings

    catalog = _catalog(connection)
    active = sorted(set(settings.main_tool_enabled_operations))
    prospective = sorted(set(str(item).strip().casefold() for item in prospective_operations))
    for tool_id in active + prospective:
        if tool_id not in catalog:
            raise ReadinessError(f"operation_unknown:{tool_id}")
    reasons: set[tuple[str, str]] = set()
    dependencies: set[str] = set()

    def add_dependency(dependency: str, code: str, subject: str) -> None:
        if dependency not in _DEPENDENCIES or code not in _REASON_CODES or not _SYMBOL.fullmatch(subject):
            raise ReadinessError("runtime_requirement_mapping_invalid")
        dependencies.add(dependency)
        reasons.add((code, subject))

    for tool_id in active:
        for dependency in catalog[tool_id]:
            add_dependency(dependency, "active_operation_dependency", tool_id)
    for tool_id in prospective:
        for dependency in catalog[tool_id]:
            add_dependency(dependency, "prospective_operation_dependency", tool_id)
    flag_dependencies = (
        (settings.action_approval_worker_enabled, "action_approval", "action_approval"),
        (settings.action_tickets_enabled, "ticket_review", "action_tickets"),
        (settings.documents_processing_enabled, "document_processing", "documents_processing"),
        (settings.email_agent_operations_worker_enabled, "email_operations", "email_operations"),
    )
    for enabled, dependency, subject in flag_dependencies:
        if enabled:
            add_dependency(dependency, "protected_flag_enabled", subject)

    tables = _tables(connection)
    if "action_proposals" in tables:
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM action_proposals WHERE state NOT IN ("
                + ",".join("?" for _ in _TERMINAL_PROPOSAL_STATES)
                + ")",
                sorted(_TERMINAL_PROPOSAL_STATES),
            ).fetchone()[0]
        )
        if count:
            add_dependency(
                "action_approval", "nonterminal_action_proposal", "action_proposals"
            )
    if "durable_jobs" in tables:
        unfinished_jobs = connection.execute(
            "SELECT DISTINCT job_type FROM durable_jobs WHERE status NOT IN ('completed','dead_letter','cancelled')"
        ).fetchall()
        for row in unfinished_jobs:
            job_type = str(row["job_type"] or "").strip().casefold()
            if job_type in {
                "review.notification.discord.v1",
                "review.action_execution.v1",
                "review.outcome.discord.v1",
            }:
                add_dependency("action_approval", "unfinished_durable_job", job_type)
            elif job_type in {"ticket_review", "ticket_watchdog"}:
                add_dependency("ticket_review", "unfinished_durable_job", job_type)
            elif job_type in {"document.archive.v1", "document.process.v1"}:
                add_dependency("document_processing", "unfinished_durable_job", job_type)
            elif job_type == "plane_sync":
                reasons.add(("unfinished_durable_job", job_type))
            elif job_type not in _KNOWN_NO_EXTRA_SERVICE_JOBS:
                raise ReadinessError(f"durable_job_type_unknown:{job_type}")
    if "work_tickets" in tables:
        unfinished_tickets = int(
            connection.execute(
                "SELECT COUNT(*) FROM work_tickets WHERE status NOT IN "
                "('verified','superseded','unverifiable','escalated','cancelled')"
            ).fetchone()[0]
        )
        if unfinished_tickets:
            add_dependency("ticket_review", "unfinished_domain_operation", "work_tickets")
    for table in (
        "email_label_operations",
        "email_mailbox_operations",
        "email_managed_label_operations",
    ):
        if table not in tables:
            continue
        columns = {
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "status" not in columns:
            raise ReadinessError(f"domain_operation_table_invalid:{table}")
        unfinished = int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE status NOT IN "
                "('completed','dead_letter','cancelled','committed','denied','failed_terminal')"
            ).fetchone()[0]
        )
        if unfinished:
            add_dependency("email_operations", "unfinished_domain_operation", table)

    required_services: set[str] = set()
    required_workers: dict[str, int] = {}
    for dependency in sorted(dependencies):
        worker_type = {
            "action_approval": "action_approval",
            "ticket_review": "ticket_review",
            "document_processing": "documents",
            "email_operations": "email_operations",
        }[dependency]
        service, age = _WORKERS[worker_type]
        required_services.add(service)
        required_workers[worker_type] = age
    if settings.plane_enabled or any(subject == "plane_sync" for _, subject in reasons):
        required_services.add("plane-sync")
        required_workers["plane_sync"] = 60
        if settings.plane_enabled:
            reasons.add(("protected_flag_enabled", "plane"))
    if settings.documents_enabled:
        required_services.add("document-gateway")
        reasons.add(("protected_flag_enabled", "documents"))
    if settings.discord_attachment_ingress_enabled:
        required_services.add("discord-attachment-ingress")
        reasons.add(("protected_flag_enabled", "discord_attachment_ingress"))
    return {
        "schema_version": 1,
        "required_services": sorted(required_services),
        "required_workers": [
            {"max_age_seconds": age, "type": worker_type}
            for worker_type, age in sorted(required_workers.items())
        ],
        "email_consumer_required": "email_operations" in required_workers,
        "reasons": [
            {"code": code, "subject": subject} for code, subject in sorted(reasons)
        ],
    }


def _validate_requirements_file(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "required_services",
        "required_workers",
        "email_consumer_required",
        "reasons",
    }:
        raise ReadinessError("runtime_requirements_shape_invalid")
    if value.get("schema_version") != 1 or not isinstance(value.get("email_consumer_required"), bool):
        raise ReadinessError("runtime_requirements_version_invalid")
    allowed_services = {item[0] for item in _WORKERS.values()} | {
        "document-gateway",
        "discord-attachment-ingress",
    }
    services = value.get("required_services")
    workers = value.get("required_workers")
    reasons = value.get("reasons")
    if (
        not isinstance(services, list)
        or services != sorted(set(services))
        or any(item not in allowed_services for item in services)
        or not isinstance(workers, list)
        or not isinstance(reasons, list)
    ):
        raise ReadinessError("runtime_requirements_value_invalid")
    parsed_workers: list[dict[str, Any]] = []
    for row in workers:
        if not isinstance(row, dict) or set(row) != {"max_age_seconds", "type"}:
            raise ReadinessError("runtime_requirements_worker_invalid")
        worker_type = str(row.get("type") or "")
        if worker_type not in _WORKERS or row.get("max_age_seconds") != _WORKERS[worker_type][1]:
            raise ReadinessError("runtime_requirements_worker_invalid")
        parsed_workers.append(dict(row))
    if parsed_workers != sorted(parsed_workers, key=lambda item: item["type"]):
        raise ReadinessError("runtime_requirements_worker_invalid")
    parsed_reasons: list[dict[str, str]] = []
    for row in reasons:
        if not isinstance(row, dict) or set(row) != {"code", "subject"}:
            raise ReadinessError("runtime_requirements_reason_invalid")
        code = str(row.get("code") or "")
        subject = str(row.get("subject") or "")
        if code not in _REASON_CODES or not _SYMBOL.fullmatch(subject):
            raise ReadinessError("runtime_requirements_reason_invalid")
        parsed_reasons.append({"code": code, "subject": subject})
    if parsed_reasons != sorted(parsed_reasons, key=lambda item: (item["code"], item["subject"])):
        raise ReadinessError("runtime_requirements_reason_invalid")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check content-free durable worker readiness.")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--require-worker", action="append", default=[])
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--write-dead-letter-baseline", type=Path)
    parser.add_argument("--dead-letter-baseline", type=Path)
    parser.add_argument("--write-email-dead-letter-baseline", type=Path)
    parser.add_argument("--email-dead-letter-baseline", type=Path)
    parser.add_argument("--prospective-operation", action="append", default=[])
    parser.add_argument("--write-runtime-requirements", type=Path)
    parser.add_argument("--runtime-requirements", type=Path)
    args = parser.parse_args(argv)
    try:
        if not 0 <= args.wait_seconds <= 600:
            raise ReadinessError("wait_seconds_invalid")
        requirements = [_parse_worker_requirement(item) for item in args.require_worker]
        loaded_requirements = None
        if args.runtime_requirements is not None:
            loaded_requirements = _validate_requirements_file(
                json.loads(args.runtime_requirements.read_text(encoding="utf-8"))
            )
            requirements.extend(
                (str(row["type"]), int(row["max_age_seconds"]))
                for row in loaded_requirements["required_workers"]
            )
        deadline = time.monotonic() + args.wait_seconds
        while True:
            try:
                with closing(_open_readonly(args.database)) as connection:
                    heartbeats = _heartbeat_status(
                        connection,
                        sorted(set(requirements)),
                        now=datetime.now(UTC),
                    )
                    durable_counts = _dead_letter_counts(connection)
                    email_counts = _email_dead_letter_counts(connection)
                    generated = _requirements(
                        connection,
                        prospective_operations=args.prospective_operation,
                    )
                if loaded_requirements is not None and loaded_requirements != generated:
                    raise ReadinessError("runtime_requirements_changed")
                break
            except ReadinessError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        if args.write_dead_letter_baseline:
            _baseline(
                args.write_dead_letter_baseline,
                kind="durable_job_dead_letters",
                counts=durable_counts,
                write=True,
            )
        if args.dead_letter_baseline:
            _baseline(
                args.dead_letter_baseline,
                kind="durable_job_dead_letters",
                counts=durable_counts,
                write=False,
            )
        if args.write_email_dead_letter_baseline:
            _baseline(
                args.write_email_dead_letter_baseline,
                kind="email_operation_dead_letters",
                counts=email_counts,
                write=True,
            )
        if args.email_dead_letter_baseline:
            _baseline(
                args.email_dead_letter_baseline,
                kind="email_operation_dead_letters",
                counts=email_counts,
                write=False,
            )
        if args.write_runtime_requirements:
            args.write_runtime_requirements.parent.mkdir(parents=True, exist_ok=True)
            args.write_runtime_requirements.write_text(
                json.dumps(generated, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        output = {
            "status": "ready",
            "worker_requirements": heartbeats,
            "durable_dead_letter_count": sum(durable_counts.values()),
            "email_dead_letter_count": sum(email_counts.values()),
            "runtime_requirement_service_count": len(generated["required_services"]),
            "runtime_requirement_worker_count": len(generated["required_workers"]),
        }
        print(json.dumps(output, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, sqlite3.Error, json.JSONDecodeError, ReadinessError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "not_ready", "reason": str(exc)[:160]},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
