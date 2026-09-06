from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.google.gmail_spam_writer import (  # noqa: E402
    GmailManagedLabelWriteResult,
    GmailSpamWriteResult,
)
from app.db.connection import open_sqlite_connection  # noqa: E402
from app.db.migrations import initialize_schema  # noqa: E402
from app.skills.domains.email_agent.config import EmailAgentPermissions  # noqa: E402
from app.skills.domains.email_agent.operations import EmailManagedLabelToolExecutor  # noqa: E402
from app.skills.domains.email_agent.query import EmailReadToolExecutor  # noqa: E402
from app.skills.domains.email_agent.storage import EmailAgentSQLiteStorage  # noqa: E402
from app.skills.tool_contracts import (  # noqa: E402
    ToolArgumentCanonicalizationError,
    ToolCallEnvelope,
    compile_tool_descriptors,
)
from app.workers.email_operations_worker import (  # noqa: E402
    EmailOperationsWorker,
    EmailOperationsWorkerConfig,
)


class FakeManagedLabelWriter:
    """Stateful, provider-free label writer used only by the copied-database canary."""

    def __init__(self, *, fail_message_id: str | None = None) -> None:
        self.fail_message_id = fail_message_id
        self.calls = 0
        self._states: dict[str, set[str]] = {}

    def verify_profile(self) -> None:
        return None

    @staticmethod
    def _label_id(label_name: str) -> str:
        digest = hashlib.sha256(str(label_name).encode("utf-8")).hexdigest()[:20]
        return f"FakeLabel_{digest}"

    def mutate_managed_labels(
        self,
        *,
        message_id: str,
        operation_id: str,
        action: str,
        label_names: tuple[str, ...],
        managed_label_names: tuple[str, ...],
    ) -> GmailManagedLabelWriteResult:
        del operation_id
        self.calls += 1
        if message_id == self.fail_message_id:
            raise TimeoutError("copied_database_fake_provider_timeout")
        managed_ids = {
            label_name: self._label_id(label_name) for label_name in managed_label_names
        }
        before = set(
            self._states.setdefault(message_id, {"INBOX", "UNREAD", "UnrelatedLabel"})
        )
        target_ids = {managed_ids[label_name] for label_name in label_names}
        after = set(before)
        if action == "apply":
            after.update(target_ids)
        elif action == "remove":
            after.difference_update(target_ids)
        else:
            raise ValueError("copied_database_fake_action_invalid")
        if not {"INBOX", "UNREAD", "UnrelatedLabel"}.issubset(after):
            raise RuntimeError("copied_database_fake_unrelated_label_changed")
        self._states[message_id] = after
        return GmailManagedLabelWriteResult(
            message_id=message_id,
            labels_before=tuple(sorted(before)),
            labels_after=tuple(sorted(after)),
            provider_modified=before != after,
            verified=True,
            managed_label_ids=tuple(sorted(managed_ids.items())),
        )

    def mutate_system_label(
        self,
        *,
        message_id: str,
        operation_id: str,
        action: str,
        system_label: str,
    ) -> GmailSpamWriteResult:
        del operation_id
        self.calls += 1
        if message_id == self.fail_message_id:
            raise TimeoutError("copied_database_fake_provider_timeout")
        label = str(system_label or "").strip().upper()
        if label not in {"INBOX", "UNREAD"} or action not in {"apply", "remove"}:
            raise ValueError("copied_database_fake_system_transition_invalid")
        before = set(
            self._states.setdefault(message_id, {"INBOX", "UNREAD", "UnrelatedLabel"})
        )
        if label == "INBOX" and action == "apply" and ({"SPAM", "TRASH"} & before):
            raise RuntimeError("email_restore_source_state_forbidden")
        after = set(before)
        if action == "apply":
            after.add(label)
        else:
            after.discard(label)
        if "UnrelatedLabel" not in after:
            raise RuntimeError("copied_database_fake_unrelated_label_changed")
        self._states[message_id] = after
        return GmailSpamWriteResult(
            message_id=message_id,
            labels_before=tuple(sorted(before)),
            labels_after=tuple(sorted(after)),
            provider_modified=before != after,
            verified=(label in after) == (action == "apply") and (before ^ after) <= {label},
        )


def _descriptors() -> dict[str, Any]:
    path = Path("app/prompts/skills/email_agent_skill.md")
    frontmatter = yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])
    descriptors, diagnostics = compile_tool_descriptors(
        skill_id="skill.email.agent",
        contract_version=frontmatter["main_tools_contract_version"],
        declarations=frontmatter["main_tools"],
    )
    if diagnostics:
        raise RuntimeError("copied_database_descriptor_diagnostics")
    return {descriptor.tool_id: descriptor for descriptor in descriptors}


def _context(permissions: EmailAgentPermissions) -> dict[str, Any]:
    grant = next((item for item in permissions.access_grants if item.enabled), None)
    if grant is None:
        raise RuntimeError("copied_database_authorized_grant_missing")
    return {
        "source_interface": "discord",
        "identity_bound": True,
        "requested_by_user_id": grant.user_id,
        "discord_channel_id": grant.discord_channel_id,
        "external_user_id": grant.external_user_id or "copied-db-canary",
        "agent_id": grant.agent_ids[0] if grant.agent_ids else "jarvis",
    }


def _envelope(
    *,
    executor: Any,
    descriptors: dict[str, Any],
    context: dict[str, Any],
    tool_id: str,
    arguments: dict[str, Any],
    root_request_id: str | None = None,
) -> ToolCallEnvelope:
    descriptor = descriptors[tool_id]
    validated = descriptor.validate_arguments(arguments)
    canonical = executor.canonicalize(
        tool_id=tool_id,
        validated_arguments=validated,
        request_context=context,
    )
    return ToolCallEnvelope.create(
        root_request_id=root_request_id or f"copied-db-{uuid4()}",
        call_ordinal=1,
        session_id="copied-db-canary",
        principal_kind="discord_adapter",
        principal_subject=str(context["external_user_id"]),
        user_id=str(context["requested_by_user_id"]),
        agent_id=str(context["agent_id"]),
        source_interface="discord",
        channel_scope=str(context["discord_channel_id"]),
        skill_id="skill.email.agent",
        descriptor=descriptor,
        authorization_snapshot_ref="copied-db-canary",
        validated_arguments=canonical,
    )


def _run_read(
    *,
    executor: EmailReadToolExecutor,
    descriptors: dict[str, Any],
    context: dict[str, Any],
    tool_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return executor.execute(
        envelope=_envelope(
            executor=executor,
            descriptors=descriptors,
            context=context,
            tool_id=tool_id,
            arguments=arguments,
        )
    )


def _operation_status(
    *,
    storage: EmailAgentSQLiteStorage,
    envelope: ToolCallEnvelope,
    context: dict[str, Any],
) -> dict[str, Any]:
    value = storage.get_managed_label_operation(
        operation_id=envelope.operation_id,
        owner_user_id=str(context["requested_by_user_id"]),
        discord_channel_id=str(context["discord_channel_id"]),
    )
    return value or {}


def run_canary(*, database_path: str, permissions_path: str) -> dict[str, Any]:
    database = Path(database_path).expanduser().resolve()
    if database.is_symlink() or not database.name.endswith(".copied-canary.db"):
        raise RuntimeError("copied_database_path_guard_failed")
    if not database.is_file():
        raise RuntimeError("copied_database_missing")

    permissions = EmailAgentPermissions.load(permissions_path)
    if not permissions.additive_label_writes_ready:
        raise RuntimeError("copied_database_managed_labels_not_ready")
    context = _context(permissions)
    descriptors = _descriptors()
    now = datetime.now(UTC).replace(microsecond=0)
    _, migration_connection = open_sqlite_connection(str(database))
    try:
        initialize_schema(migration_connection)
    finally:
        migration_connection.close()
    storage = EmailAgentSQLiteStorage(str(database))
    try:
        legacy_before = {
            str(row[0]): int(row[1])
            for row in storage._conn.execute(
                "SELECT status, COUNT(*) FROM email_label_operations GROUP BY status"
            ).fetchall()
        }
        reads = EmailReadToolExecutor(
            storage=storage,
            permissions=permissions,
            timezone_name="America/New_York",
            reference_retention_hours=24,
            stale_seconds=1800,
            utc_clock=lambda: now,
        )
        operations = EmailManagedLabelToolExecutor(
            storage=storage,
            permissions=permissions,
            max_attempts=4,
            utc_clock=lambda: now,
        )

        mailbox_catalog = _run_read(
            executor=reads,
            descriptors=descriptors,
            context=context,
            tool_id="email.list_mailboxes",
            arguments={},
        )
        label_catalog = _run_read(
            executor=reads,
            descriptors=descriptors,
            context=context,
            tool_id="email.list_labels",
            arguments={},
        )
        mailboxes = mailbox_catalog.get("payload", {}).get("mailboxes") or []
        labels = label_catalog.get("payload", {}).get("labels") or []
        if mailbox_catalog.get("status") != "ok" or not mailboxes:
            raise RuntimeError("copied_database_mailbox_catalog_failed")
        if label_catalog.get("status") != "ok" or not labels:
            raise RuntimeError("copied_database_label_catalog_failed")
        label_ref = str(labels[0]["label_ref"])

        arbitrary_date = _run_read(
            executor=reads,
            descriptors=descriptors,
            context=context,
            tool_id="email.query_messages",
            arguments={
                "start": "2000-01-01T00:00:00-05:00",
                "end": "2100-01-01T00:00:00-05:00",
                "visibility": "all",
                "limit": 2,
            },
        )
        if arbitrary_date.get("status") != "ok":
            raise RuntimeError("copied_database_arbitrary_date_failed")

        first_page = _run_read(
            executor=reads,
            descriptors=descriptors,
            context=context,
            tool_id="email.query_messages",
            arguments={"visibility": "all", "order": "newest", "limit": 1},
        )
        cursor = str(first_page.get("payload", {}).get("next_cursor") or "")
        if first_page.get("status") != "ok" or not cursor:
            raise RuntimeError("copied_database_pagination_first_page_failed")
        second_page = _run_read(
            executor=reads,
            descriptors=descriptors,
            context=context,
            tool_id="email.query_messages",
            arguments={"cursor": cursor},
        )
        if second_page.get("status") != "ok":
            raise RuntimeError("copied_database_pagination_second_page_failed")

        operation_query = _run_read(
            executor=reads,
            descriptors=descriptors,
            context=context,
            tool_id="email.query_messages",
            arguments={"visibility": "all", "order": "newest", "limit": 2},
        )
        if len(operation_query.get("payload", {}).get("messages") or []) < 2:
            raise RuntimeError("copied_database_operation_targets_missing")

        unauthorized = {**context, "discord_channel_id": "0"}
        try:
            reads.canonicalize(
                tool_id="email.list_mailboxes",
                validated_arguments={},
                request_context=unauthorized,
            )
        except ToolArgumentCanonicalizationError as exc:
            if exc.code != "email_tool_unauthorized":
                raise
        else:
            raise RuntimeError("copied_database_authorization_denial_failed")

        stale_envelope = _envelope(
            executor=operations,
            descriptors=descriptors,
            context=context,
            tool_id="email.apply_labels",
            arguments={"message_refs": ["E1"], "label_refs": ["label_v1_" + "f" * 24]},
        )
        stale_result = operations.execute(envelope=stale_envelope)
        if stale_result.get("status") != "needs_input":
            raise RuntimeError("copied_database_missing_label_recovery_failed")

        apply_envelope = _envelope(
            executor=operations,
            descriptors=descriptors,
            context=context,
            tool_id="email.apply_labels",
            arguments={"message_refs": ["E1"], "label_refs": [label_ref]},
            root_request_id="copied-db-apply",
        )
        queued_apply = operations.execute(envelope=apply_envelope)
        replay_apply = operations.execute(envelope=apply_envelope)
        if queued_apply.get("status") != "queued" or not replay_apply.get("payload", {}).get(
            "idempotent_replay"
        ):
            raise RuntimeError("copied_database_apply_replay_failed")
        writer = FakeManagedLabelWriter()
        worker = EmailOperationsWorker(
            storage=storage,
            writer=writer,
            permissions=permissions,
            config=EmailOperationsWorkerConfig(
                enabled=True,
                batch_size=25,
                max_writes_per_hour=500,
                max_writes_per_day=2000,
            ),
            worker_id="copied-db-apply",
        )
        applied = worker.run_once(now=now)
        if applied.get("verified_count") != 1 or _operation_status(
            storage=storage,
            envelope=apply_envelope,
            context=context,
        ).get("status") != "completed":
            raise RuntimeError("copied_database_apply_failed")

        remove_envelope = _envelope(
            executor=operations,
            descriptors=descriptors,
            context=context,
            tool_id="email.remove_labels",
            arguments={"message_refs": ["E1"], "label_refs": [label_ref]},
            root_request_id="copied-db-remove",
        )
        operations.execute(envelope=remove_envelope)
        removed = worker.run_once(now=now)
        if removed.get("verified_count") != 1 or _operation_status(
            storage=storage,
            envelope=remove_envelope,
            context=context,
        ).get("status") != "completed":
            raise RuntimeError("copied_database_remove_failed")

        reversible_steps = (
            ("email.set_read_state", {"message_refs": ["E1"], "state": "read"}),
            ("email.archive_messages", {"message_refs": ["E1"]}),
            ("email.restore_to_inbox", {"message_refs": ["E1"]}),
            ("email.set_read_state", {"message_refs": ["E1"], "state": "unread"}),
        )
        reversible_writer = FakeManagedLabelWriter()
        reversible_worker = EmailOperationsWorker(
            storage=storage,
            writer=reversible_writer,
            permissions=permissions,
            config=EmailOperationsWorkerConfig(
                enabled=True,
                batch_size=25,
                max_writes_per_hour=500,
                max_writes_per_day=2000,
            ),
            worker_id="copied-db-reversible",
        )
        for index, (tool_id, arguments) in enumerate(reversible_steps, start=1):
            reversible_envelope = _envelope(
                executor=operations,
                descriptors=descriptors,
                context=context,
                tool_id=tool_id,
                arguments=arguments,
                root_request_id=f"copied-db-reversible-{index}",
            )
            if operations.execute(envelope=reversible_envelope).get("status") != "queued":
                raise RuntimeError("copied_database_reversible_queue_failed")
            if reversible_worker.run_once(now=now).get("verified_count") != 1:
                raise RuntimeError("copied_database_reversible_worker_failed")
            if _operation_status(
                storage=storage,
                envelope=reversible_envelope,
                context=context,
            ).get("status") != "completed":
                raise RuntimeError("copied_database_reversible_completion_failed")
        reversible_states = list(reversible_writer._states.values())
        if len(reversible_states) != 1 or reversible_states[0] != {
            "INBOX",
            "UNREAD",
            "UnrelatedLabel",
        }:
            raise RuntimeError("copied_database_reversible_restore_failed")

        partial_executor = EmailManagedLabelToolExecutor(
            storage=storage,
            permissions=permissions,
            max_attempts=1,
            utc_clock=lambda: now,
        )
        partial_envelope = _envelope(
            executor=partial_executor,
            descriptors=descriptors,
            context=context,
            tool_id="email.apply_labels",
            arguments={"message_refs": ["E1", "E2"], "label_refs": [label_ref]},
            root_request_id="copied-db-partial",
        )
        partial_executor.execute(envelope=partial_envelope)
        failed_target = storage.resolve_reference(
            user_id=str(context["requested_by_user_id"]),
            discord_channel_id=str(context["discord_channel_id"]),
            reference="E2",
            now=now.isoformat().replace("+00:00", "Z"),
        )
        partial_writer = FakeManagedLabelWriter(
            fail_message_id=str((failed_target or {}).get("gmail_message_id") or "")
        )
        partial_worker = EmailOperationsWorker(
            storage=storage,
            writer=partial_writer,
            permissions=permissions,
            config=EmailOperationsWorkerConfig(
                enabled=True,
                batch_size=25,
                max_writes_per_hour=500,
                max_writes_per_day=2000,
            ),
            worker_id="copied-db-partial",
        )
        partial_worker.run_once(now=now)
        if _operation_status(
            storage=storage,
            envelope=partial_envelope,
            context=context,
        ).get("status") != "partial":
            raise RuntimeError("copied_database_partial_failure_failed")

        restart_envelope = _envelope(
            executor=operations,
            descriptors=descriptors,
            context=context,
            tool_id="email.remove_labels",
            arguments={"message_refs": ["E1", "E2"], "label_refs": [label_ref]},
            root_request_id="copied-db-restart",
        )
        operations.execute(envelope=restart_envelope)
        with storage._lock:
            storage._conn.execute(
                "UPDATE email_tool_operations SET status='reserved' WHERE operation_id=?",
                (restart_envelope.operation_id,),
            )
            storage._conn.execute(
                "DELETE FROM email_managed_label_operations "
                "WHERE parent_operation_id=? AND child_index=2",
                (restart_envelope.operation_id,),
            )
            storage._conn.commit()
        restart_writer = FakeManagedLabelWriter()
        restarted_worker = EmailOperationsWorker(
            storage=storage,
            writer=restart_writer,
            permissions=permissions,
            config=EmailOperationsWorkerConfig(
                enabled=True,
                batch_size=25,
                max_writes_per_hour=500,
                max_writes_per_day=2000,
            ),
            worker_id="copied-db-restarted",
        )
        restarted_worker.run_once(now=now)
        if _operation_status(
            storage=storage,
            envelope=restart_envelope,
            context=context,
        ).get("status") != "completed":
            raise RuntimeError("copied_database_restart_recovery_failed")

        legacy_after = {
            str(row[0]): int(row[1])
            for row in storage._conn.execute(
                "SELECT status, COUNT(*) FROM email_label_operations GROUP BY status"
            ).fetchall()
        }
        if legacy_after != legacy_before:
            raise RuntimeError("copied_database_legacy_rows_changed")
        integrity = str(storage._conn.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity.casefold() != "ok":
            raise RuntimeError("copied_database_integrity_failed")
        return {
            "status": "passed",
            "provider": "fake_only",
            "catalog_discovery": True,
            "arbitrary_dates": True,
            "pagination": True,
            "query_apply_remove": True,
            "reversible_mailbox_state": True,
            "authorization_denial": True,
            "missing_label_recovery": True,
            "idempotent_replay": True,
            "partial_failure": True,
            "restart_recovery": True,
            "legacy_rows_unchanged": True,
            "integrity_check": "ok",
        }
    finally:
        storage.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the provider-free P5F canary against a guarded copied database."
    )
    parser.add_argument("--database-copy", required=True)
    parser.add_argument("--permissions", required=True)
    parser.add_argument("--fake-writer-only", action="store_true")
    args = parser.parse_args()
    if not args.fake_writer_only:
        parser.error("--fake-writer-only is required")
    result = run_canary(
        database_path=args.database_copy,
        permissions_path=args.permissions,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
