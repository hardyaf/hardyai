from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.jobs.repository import DurableJobRepository
from app.services.google.gmail_spam_writer import GmailSpamWriter, GoogleGmailSpamWriter
from app.services.google.gmail_spam_writer import GMAIL_MODIFY_SCOPE
from app.skills.domains.email_agent.catalog import EmailCatalogService
from app.skills.domains.email_agent.config import EmailAgentPermissions
from app.skills.domains.email_agent.operations import (
    SYSTEM_LABEL_INBOX_REF,
    SYSTEM_LABEL_REFS,
    SYSTEM_LABEL_UNREAD_REF,
)
from app.skills.domains.email_agent.storage import (
    EmailAgentSQLiteStorage,
    inspect_email_operations_worker_database,
)
from app.tickets.async_receipts import AsyncChildOutcomeSink
from app.tickets.repository import TicketRepository


@dataclass(frozen=True, slots=True)
class EmailOperationsWorkerConfig:
    enabled: bool = False
    poll_seconds: float = 2.0
    batch_size: int = 5
    lease_seconds: int = 90
    max_writes_per_hour: int = 40
    max_writes_per_day: int = 200

    def __post_init__(self) -> None:
        if not 1.0 <= float(self.poll_seconds) <= 60.0:
            raise ValueError("poll_seconds must be between 1 and 60.")
        if not 1 <= int(self.batch_size) <= 25:
            raise ValueError("batch_size must be between 1 and 25.")
        if not 15 <= int(self.lease_seconds) <= 600:
            raise ValueError("lease_seconds must be between 15 and 600.")
        if not 1 <= int(self.max_writes_per_hour) <= 500:
            raise ValueError("max_writes_per_hour must be between 1 and 500.")
        if not 1 <= int(self.max_writes_per_day) <= 2000:
            raise ValueError("max_writes_per_day must be between 1 and 2000.")


class EmailOperationsWorker:
    """Claims only P5F/P8D parent-bound, allowlisted Gmail state operations."""

    WORKER_TYPE = "email_operations"

    def __init__(
        self,
        *,
        storage: EmailAgentSQLiteStorage,
        writer: GmailSpamWriter,
        permissions: EmailAgentPermissions,
        config: EmailOperationsWorkerConfig,
        heartbeat_repository: DurableJobRepository | None = None,
        worker_id: str | None = None,
        outcome_sink: AsyncChildOutcomeSink | None = None,
    ) -> None:
        if not permissions.additive_label_writes_ready:
            raise ValueError("Email managed-label permissions version 2 is required.")
        self._storage = storage
        self._writer = writer
        self._permissions = permissions
        self.config = config
        self._heartbeats = heartbeat_repository
        self.worker_id = str(worker_id or f"email-operations-{uuid4()}")
        self._stop = Event()
        self._outcome_sink = outcome_sink
        self._catalog = EmailCatalogService(permissions=permissions, storage=storage)
        self._labels_by_ref = {
            self._catalog.label_ref(label.key): label
            for label in permissions.managed_labels
            if label.enabled
        }

    def request_stop(self) -> None:
        self._stop.set()

    def readiness(self) -> dict[str, Any]:
        """Validate local configuration/schema only; never claim or call Gmail."""

        labels = self._storage.enabled_managed_labels()
        storage = self._storage.operations_worker_readiness(now=self._iso(self._now()))
        ready = (
            self.config.enabled
            and bool(labels)
            and set(self._labels_by_ref) == {str(item.get("label_ref") or "") for item in labels}
            and bool(storage["schema_ready"])
            and bool(storage["supported_row_kinds"])
            and bool(storage["single_worker_ownership"])
        )
        return {
            "status": "ready" if ready else "not_ready",
            "worker_enabled": self.config.enabled,
            "managed_label_count": len(labels),
            "legacy_claim_eligible_count": 0,
            **storage,
        }

    def run_once(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = (now or self._now()).astimezone(UTC).replace(microsecond=0)
        if not self.config.enabled:
            return {"status": "disabled", "claimed_count": 0}
        # Reconstruct interrupted atomic reservations on the same clock edge as
        # the claim that follows. Using constructor wall time here could make a
        # recovered child appear to be scheduled in the future when run_once
        # is driven by an injected/simulated clock.
        self._storage.recover_reserved_managed_label_operations(now=self._iso(current))
        self._storage.recover_mailbox_tool_operations(now=self._iso(current))
        self._reconcile_terminal_mailbox_outcomes(now=current)
        self._heartbeat(status="polling", metadata={})
        started_hour = (
            self._storage.managed_label_started_count_since(
                since=self._iso(current - timedelta(hours=1))
            )
            + self._storage.mailbox_started_count_since(
                since=self._iso(current - timedelta(hours=1))
            )
        )
        started_day = (
            self._storage.managed_label_started_count_since(
                since=self._iso(current - timedelta(days=1))
            )
            + self._storage.mailbox_started_count_since(
                since=self._iso(current - timedelta(days=1))
            )
        )
        remaining = min(
            int(self.config.batch_size),
            max(0, int(self.config.max_writes_per_hour) - started_hour),
            max(0, int(self.config.max_writes_per_day) - started_day),
        )
        if remaining <= 0:
            self._heartbeat(
                status="rate_limited",
                metadata={"started_last_hour": started_hour, "started_last_day": started_day},
            )
            return {
                "status": "rate_limited",
                "claimed_count": 0,
                "started_last_hour": started_hour,
                "started_last_day": started_day,
            }
        claimed = self._storage.claim_managed_label_operations(
            lease_owner=self.worker_id,
            now=self._iso(current),
            lease_expires_at=self._iso(current + timedelta(seconds=int(self.config.lease_seconds))),
            limit=remaining,
        )
        verified_count = 0
        retry_count = 0
        dead_letter_count = 0
        allowed_names = tuple(label.gmail_label_name for label in self._labels_by_ref.values())
        for child in claimed:
            try:
                refs = child.get("managed_label_refs")
                if not isinstance(refs, list) or not refs:
                    raise RuntimeError("email_managed_label_refs_invalid")
                normalized_refs = [str(ref) for ref in refs]
                system_refs = set(normalized_refs) & SYSTEM_LABEL_REFS
                if system_refs:
                    if len(normalized_refs) != 1 or len(system_refs) != 1:
                        raise RuntimeError("email_system_label_refs_invalid")
                    system_ref = next(iter(system_refs))
                    system_label = {
                        SYSTEM_LABEL_INBOX_REF: "INBOX",
                        SYSTEM_LABEL_UNREAD_REF: "UNREAD",
                    }[system_ref]
                    result = self._writer.mutate_system_label(
                        message_id=str(child.get("gmail_message_id") or ""),
                        operation_id=str(child.get("child_operation_id") or ""),
                        action=str(child.get("action") or ""),
                        system_label=system_label,
                    )
                    managed_state: list[dict[str, Any]] = []
                else:
                    target_labels = [self._labels_by_ref.get(ref) for ref in normalized_refs]
                    if any(label is None for label in target_labels):
                        raise RuntimeError("email_managed_label_disabled")
                    result = self._writer.mutate_managed_labels(
                        message_id=str(child.get("gmail_message_id") or ""),
                        operation_id=str(child.get("child_operation_id") or ""),
                        action=str(child.get("action") or ""),
                        label_names=tuple(label.gmail_label_name for label in target_labels if label),
                        managed_label_names=allowed_names,
                    )
                    ids_by_name = dict(result.managed_label_ids)
                    managed_state = [
                        {
                            "label_ref": label_ref,
                            "provider_label_id": ids_by_name.get(label.gmail_label_name),
                            "present": bool(
                                ids_by_name.get(label.gmail_label_name)
                                and ids_by_name[label.gmail_label_name] in result.labels_after
                            ),
                        }
                        for label_ref, label in self._labels_by_ref.items()
                    ]
                if not result.verified:
                    raise RuntimeError("gmail_email_operation_readback_not_verified")
                self._storage.complete_managed_label_operation(
                    child_operation_id=str(child.get("child_operation_id") or ""),
                    lease_owner=self.worker_id,
                    lease_fencing_token=int(child.get("lease_fencing_token") or 0),
                    provider_labels_before=list(result.labels_before),
                    provider_labels_after=list(result.labels_after),
                    gmail_label_ids=list(result.labels_after),
                    managed_label_state=managed_state,
                    now=self._iso(current),
                )
                verified_count += 1
            except Exception as exc:
                attempt = int(child.get("attempt_count") or 1)
                retry_at = current + timedelta(seconds=min(900, 15 * (2 ** max(0, attempt - 1))))
                failed = self._storage.fail_managed_label_operation(
                    child_operation_id=str(child.get("child_operation_id") or ""),
                    lease_owner=self.worker_id,
                    lease_fencing_token=int(child.get("lease_fencing_token") or 0),
                    error_code=type(exc).__name__,
                    next_attempt_at=self._iso(retry_at),
                    now=self._iso(current),
                )
                if failed.get("status") == "dead_letter":
                    dead_letter_count += 1
                else:
                    retry_count += 1
        mailbox_remaining = max(0, remaining - len(claimed))
        mailbox_claimed = self._storage.claim_mailbox_operations(
            lease_owner=self.worker_id,
            now=self._iso(current),
            lease_expires_at=self._iso(current + timedelta(seconds=int(self.config.lease_seconds))),
            limit=max(1, mailbox_remaining),
            parent_bound=True,
        ) if mailbox_remaining else []
        for child in mailbox_claimed:
            if not self._mailbox_child_authorized(child):
                cancelled = self._storage.cancel_claimed_mailbox_operation(
                    operation_id=str(child.get("operation_id") or ""),
                    lease_owner=self.worker_id,
                    reason_code="policy_denied",
                    now=self._iso(current),
                )
                reconciled = self._report_mailbox_outcome(cancelled, now=current)
                self._storage.reduce_mailbox_tool_parent(
                    parent_operation_id=str(cancelled.get("parent_operation_id") or ""),
                    outcomes_reconciled=reconciled,
                    now=self._iso(current),
                )
                dead_letter_count += 1
                continue
            try:
                if str(child.get("operation_type") or "") != "move_to_spam":
                    raise RuntimeError("email_mailbox_operation_type_invalid")
                result = self._writer.move_to_spam(
                    message_id=str(child.get("gmail_message_id") or ""),
                    operation_id=str(child.get("operation_id") or ""),
                )
                if not result.verified:
                    raise RuntimeError("gmail_spam_readback_not_verified")
                completed = self._storage.complete_mailbox_operation(
                    operation_id=str(child.get("operation_id") or ""),
                    lease_owner=self.worker_id,
                    labels_before=list(result.labels_before),
                    labels_after=list(result.labels_after),
                    now=self._iso(current),
                )
                verified_count += 1
            except Exception as exc:
                attempt = int(child.get("attempt_count") or 1)
                retry_at = current + timedelta(seconds=min(900, 15 * (2 ** max(0, attempt - 1))))
                failed = self._storage.fail_mailbox_operation(
                    operation_id=str(child.get("operation_id") or ""),
                    lease_owner=self.worker_id,
                    error_code=type(exc).__name__,
                    next_attempt_at=self._iso(retry_at),
                    now=self._iso(current),
                )
                if failed.get("status") == "dead_letter":
                    reconciled = self._report_mailbox_outcome(failed, now=current)
                    self._storage.reduce_mailbox_tool_parent(
                        parent_operation_id=str(failed.get("parent_operation_id") or ""),
                        outcomes_reconciled=reconciled,
                        now=self._iso(current),
                    )
                    dead_letter_count += 1
                else:
                    retry_count += 1
                continue
            # Provider success is durable at this point. A receipt-sink or parent
            # reduction failure must never turn the verified child back into a
            # retryable Gmail write; startup reconciliation completes this path.
            try:
                reconciled = self._report_mailbox_outcome(completed, now=current)
                self._storage.reduce_mailbox_tool_parent(
                    parent_operation_id=str(completed.get("parent_operation_id") or ""),
                    outcomes_reconciled=reconciled,
                    now=self._iso(current),
                )
            except Exception:
                retry_count += 1
        status = "degraded" if retry_count or dead_letter_count else "idle"
        self._heartbeat(
            status=status,
            last_error_code=("email_operation_failed" if status == "degraded" else None),
            metadata={
                "claimed_count": len(claimed) + len(mailbox_claimed),
                "verified_count": verified_count,
                "retry_count": retry_count,
                "dead_letter_count": dead_letter_count,
            },
        )
        return {
            "status": "ok" if status == "idle" else "degraded",
            "claimed_count": len(claimed) + len(mailbox_claimed),
            "verified_count": verified_count,
            "retry_count": retry_count,
            "dead_letter_count": dead_letter_count,
            "started_last_hour": started_hour,
            "started_last_day": started_day,
        }

    def _mailbox_child_authorized(self, child: dict[str, Any]) -> bool:
        parent = self._storage.get_email_tool_operation(
            operation_id=str(child.get("parent_operation_id") or ""),
        )
        recovery = parent.get("recovery_manifest") if isinstance(parent, dict) else None
        if not isinstance(recovery, dict):
            return False
        return self._permissions.authorize(
            {
                "source_interface": str(recovery.get("source_interface") or ""),
                "identity_bound": True,
                "requested_by_user_id": str(child.get("requested_by_user_id") or ""),
                "discord_channel_id": str(child.get("discord_channel_id") or ""),
                "external_user_id": str(recovery.get("external_user_id") or ""),
                "agent_id": str(recovery.get("agent_id") or ""),
            }
        ) is not None

    def _reconcile_terminal_mailbox_outcomes(self, *, now: datetime) -> None:
        parents: dict[str, bool] = {}
        for child in self._storage.terminal_parent_mailbox_children(limit=100):
            parent_id = str(child.get("parent_operation_id") or "")
            if not parent_id:
                continue
            parents[parent_id] = self._report_mailbox_outcome(child, now=now)
        for parent_id, reconciled in parents.items():
            self._storage.reduce_mailbox_tool_parent(
                parent_operation_id=parent_id,
                outcomes_reconciled=reconciled,
                now=self._iso(now),
            )

    def _report_mailbox_outcome(self, child: dict[str, Any], *, now: datetime) -> bool:
        if self._outcome_sink is None:
            return False
        status = str(child.get("status") or "").strip().casefold()
        error = str(child.get("last_error_code") or "").strip().casefold()
        state = (
            "verified"
            if status == "verified"
            else "denied"
            if status == "cancelled" and error == "policy_denied"
            else "cancelled"
            if status == "cancelled"
            else "dead_letter"
            if status == "dead_letter"
            else ""
        )
        if not state:
            return False
        child_id = str(child.get("operation_id") or "")
        parent_id = str(child.get("parent_operation_id") or "")
        parent_hash = str(child.get("parent_manifest_hash") or child.get("p7_manifest_hash") or "")
        receipt = None
        if state == "verified":
            resource_hash = hashlib.sha256(
                str(child.get("gmail_message_id") or "").encode("utf-8")
            ).hexdigest()
            receipt = {
                "operation_id": child_id,
                "idempotency_key": f"ticket-effect-receipt:v1:{child_id}",
                "capability": "email.move_to_spam",
                "action": "move_to_spam",
                "resource_key": "email_message:" + resource_hash[:24],
                "status": "committed",
                "committed_at": self._iso(now),
                "expected_effect": {"spam": True, "inbox": False},
                "validator_name": "gmail_mailbox_label_readback",
                "validator_version": "v1",
                "resource_locator": {"resource_hash": resource_hash},
            }
        try:
            persisted = self._outcome_sink.record_terminal_child(
                parent_operation_id=parent_id,
                parent_manifest_hash=parent_hash,
                child_operation_id=child_id,
                effect_state=state,
                receipt=receipt,
                reason_code=(error or None) if state != "verified" else None,
            )
            aggregate = persisted.get("aggregate") if isinstance(persisted, dict) else None
            return isinstance(aggregate, dict) and not bool(
                aggregate.get("missing_child_operation_ids")
            )
        except (KeyError, LookupError, TypeError, ValueError):
            return False

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(float(self.config.poll_seconds))

    def _heartbeat(
        self,
        *,
        status: str,
        last_error_code: str | None = None,
        metadata: dict[str, Any],
    ) -> None:
        if self._heartbeats is None:
            return
        self._heartbeats.record_worker_heartbeat(
            worker_type=self.WORKER_TYPE,
            worker_id=self.worker_id,
            status=status,
            last_error_code=last_error_code,
            metadata=metadata,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _protected_file(path_value: str, *, label: str) -> Path:
    path = Path(str(path_value or "").strip()).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.is_symlink() or not path.exists() or not path.is_file():
        raise RuntimeError(f"{label} is unavailable")
    path = path.resolve()
    if path.stat().st_size > 128 * 1024:
        raise RuntimeError(f"{label} is invalid")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise RuntimeError(f"{label} permissions are too broad")
    return path


def _validate_readiness_files(*, permissions_path: str, token_path: str) -> None:
    _protected_file(permissions_path, label="Email permissions file")
    token = _protected_file(token_path, label="Email operations token")
    try:
        loaded = json.loads(token.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Email operations token is invalid") from exc
    scopes = {
        str(value).strip()
        for value in (loaded.get("scopes") if isinstance(loaded, dict) else []) or []
        if str(value).strip()
    }
    if scopes != {GMAIL_MODIFY_SCOPE}:
        raise RuntimeError("Email operations token scope is invalid")


def _build_worker() -> tuple[
    EmailOperationsWorker,
    EmailAgentSQLiteStorage,
    DurableJobRepository,
    TicketRepository,
]:
    from app.config import settings

    permissions = EmailAgentPermissions.load(settings.email_agent_permissions_path)
    storage = EmailAgentSQLiteStorage(settings.database_path)
    heartbeats = DurableJobRepository(settings.database_path)
    tickets = TicketRepository(settings.database_path)
    writer = GoogleGmailSpamWriter.from_token_file(
        expected_profile_email=permissions.gmail_profile,
        token_path=settings.email_agent_label_token_path,
    )
    writer.verify_profile()
    worker = EmailOperationsWorker(
        storage=storage,
        writer=writer,
        permissions=permissions,
        heartbeat_repository=heartbeats,
        config=EmailOperationsWorkerConfig(
            enabled=settings.email_agent_operations_worker_enabled,
            poll_seconds=settings.email_agent_operations_worker_poll_seconds,
            batch_size=settings.email_agent_operations_worker_batch_size,
            lease_seconds=settings.email_agent_operations_worker_lease_seconds,
            max_writes_per_hour=settings.email_agent_operations_max_writes_per_hour,
            max_writes_per_day=settings.email_agent_operations_max_writes_per_day,
        ),
        outcome_sink=AsyncChildOutcomeSink(repository=tickets),
    )
    return worker, storage, heartbeats, tickets


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded P5F/P8D Email operations worker.")
    parser.add_argument("--readiness-only", action="store_true")
    args = parser.parse_args()
    from app.config import settings
    from app.services.offline_runtime_policy import validate_offline_runtime

    validate_offline_runtime(settings, entrypoint="email-operations-worker")
    if not settings.email_agent_operations_worker_enabled:
        print('{"status":"disabled","legacy_claim_eligible_count":0}')
        return 0
    if args.readiness_only:
        _validate_readiness_files(
            permissions_path=settings.email_agent_permissions_path,
            token_path=settings.email_agent_label_token_path,
        )
        permissions = EmailAgentPermissions.load(settings.email_agent_permissions_path)
        inspection, stored_label_refs = inspect_email_operations_worker_database(
            settings.database_path,
            now=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        )
        configured_label_refs = {
            EmailCatalogService.label_ref(label.key)
            for label in permissions.managed_labels
            if label.enabled
        }
        ready = (
            permissions.additive_label_writes_ready
            and bool(configured_label_refs)
            and configured_label_refs == stored_label_refs
            and bool(inspection["schema_ready"])
            and bool(inspection["supported_row_kinds"])
            and bool(inspection["single_worker_ownership"])
        )
        print(json.dumps({
            "status": "ready" if ready else "not_ready",
            "protected_config_ready": True,
            "token_mount_ready": True,
            "managed_label_count": len(stored_label_refs),
            "legacy_claim_eligible_count": 0,
            **inspection,
        }, separators=(",", ":"), sort_keys=True))
        return 0 if ready else 1
    worker, storage, heartbeats, tickets = _build_worker()
    signal.signal(signal.SIGTERM, lambda *_: worker.request_stop())
    signal.signal(signal.SIGINT, lambda *_: worker.request_stop())
    try:
        worker.run_forever()
        return 0
    finally:
        storage.close()
        heartbeats.close()
        tickets.close()


if __name__ == "__main__":
    raise SystemExit(main())
