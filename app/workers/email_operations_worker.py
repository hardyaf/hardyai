from __future__ import annotations

import argparse
import signal
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any
from uuid import uuid4

from app.jobs.repository import DurableJobRepository
from app.services.google.gmail_spam_writer import GmailSpamWriter, GoogleGmailSpamWriter
from app.skills.domains.email_agent.catalog import EmailCatalogService
from app.skills.domains.email_agent.config import EmailAgentPermissions
from app.skills.domains.email_agent.operations import (
    SYSTEM_LABEL_INBOX_REF,
    SYSTEM_LABEL_REFS,
    SYSTEM_LABEL_UNREAD_REF,
)
from app.skills.domains.email_agent.storage import EmailAgentSQLiteStorage


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
    """Claims only P5F parent-bound, allowlisted Gmail label-state operations."""

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
        ready = (
            self.config.enabled
            and bool(labels)
            and set(self._labels_by_ref) == {str(item.get("label_ref") or "") for item in labels}
        )
        return {
            "status": "ready" if ready else "not_ready",
            "worker_enabled": self.config.enabled,
            "managed_label_count": len(labels),
            "legacy_claim_eligible_count": 0,
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
        self._heartbeat(status="polling", metadata={})
        started_hour = self._storage.managed_label_started_count_since(
            since=self._iso(current - timedelta(hours=1))
        )
        started_day = self._storage.managed_label_started_count_since(
            since=self._iso(current - timedelta(days=1))
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
        status = "degraded" if retry_count or dead_letter_count else "idle"
        self._heartbeat(
            status=status,
            last_error_code=("email_operation_failed" if status == "degraded" else None),
            metadata={
                "claimed_count": len(claimed),
                "verified_count": verified_count,
                "retry_count": retry_count,
                "dead_letter_count": dead_letter_count,
            },
        )
        return {
            "status": "ok" if status == "idle" else "degraded",
            "claimed_count": len(claimed),
            "verified_count": verified_count,
            "retry_count": retry_count,
            "dead_letter_count": dead_letter_count,
            "started_last_hour": started_hour,
            "started_last_day": started_day,
        }

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


def _build_worker() -> tuple[EmailOperationsWorker, EmailAgentSQLiteStorage, DurableJobRepository]:
    from app.config import settings

    permissions = EmailAgentPermissions.load(settings.email_agent_permissions_path)
    storage = EmailAgentSQLiteStorage(settings.database_path)
    heartbeats = DurableJobRepository(settings.database_path)
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
    )
    return worker, storage, heartbeats


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded P5F Email operations worker.")
    parser.add_argument("--readiness-only", action="store_true")
    args = parser.parse_args()
    from app.config import settings
    from app.services.offline_runtime_policy import validate_offline_runtime

    validate_offline_runtime(settings, entrypoint="email-operations-worker")
    if not settings.email_agent_operations_worker_enabled:
        print('{"status":"disabled","legacy_claim_eligible_count":0}')
        return 0
    if args.readiness_only:
        permissions = EmailAgentPermissions.load(settings.email_agent_permissions_path)
        storage = EmailAgentSQLiteStorage(settings.database_path)
        try:
            catalog = EmailCatalogService(permissions=permissions, storage=storage)
            storage.sync_managed_label_catalog(
                labels=[
                    {
                        "label_ref": catalog.label_ref(label.key),
                        "policy_key": label.key,
                        "display_name": label.display_name,
                        "gmail_label_name": label.gmail_label_name,
                        "enabled": label.enabled,
                    }
                    for label in permissions.managed_labels
                ],
                now=EmailOperationsWorker._iso(datetime.now(UTC)),
            )
            ready = permissions.additive_label_writes_ready and bool(
                storage.enabled_managed_labels()
            )
            print(
                '{"status":"%s","legacy_claim_eligible_count":0}'
                % ("ready" if ready else "not_ready")
            )
            return 0 if ready else 1
        finally:
            storage.close()
    worker, storage, heartbeats = _build_worker()
    signal.signal(signal.SIGTERM, lambda *_: worker.request_stop())
    signal.signal(signal.SIGINT, lambda *_: worker.request_stop())
    try:
        worker.run_forever()
        return 0
    finally:
        storage.close()
        heartbeats.close()


if __name__ == "__main__":
    raise SystemExit(main())
