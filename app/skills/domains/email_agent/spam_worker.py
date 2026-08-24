from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.services.google.gmail_spam_writer import GmailSpamWriter
from app.skills.domains.email_agent.storage import EmailAgentSQLiteStorage


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class EmailSpamWorkerConfig:
    enabled: bool = False
    label_writes_enabled: bool = False
    batch_size: int = 5
    lease_seconds: int = 60
    max_writes_per_hour: int = 10
    max_writes_per_day: int = 20
    label_batch_size: int = 10
    label_max_writes_per_hour: int = 20
    label_max_writes_per_day: int = 50

    def __post_init__(self) -> None:
        bounds = {
            "batch_size": (1, 10),
            "lease_seconds": (15, 300),
            "max_writes_per_hour": (1, 50),
            "max_writes_per_day": (1, 200),
            "label_batch_size": (1, 25),
            "label_max_writes_per_hour": (1, 100),
            "label_max_writes_per_day": (1, 500),
        }
        for field_name, (minimum, maximum) in bounds.items():
            value = int(getattr(self, field_name))
            if not minimum <= value <= maximum:
                raise ValueError(f"{field_name} must be between {minimum} and {maximum}.")


class EmailSpamWorker:
    """Bounded worker for explicit, durable mailbox disposition operations."""

    def __init__(
        self,
        *,
        storage: EmailAgentSQLiteStorage,
        writer: GmailSpamWriter,
        config: EmailSpamWorkerConfig,
        managed_label_names: dict[str, str] | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._storage = storage
        self._writer = writer
        self.config = config
        self._managed_label_names = {
            str(key).strip().casefold(): str(value).strip()
            for key, value in (managed_label_names or {}).items()
            if str(key).strip() and str(value).strip()
        }
        self._worker_id = str(worker_id or f"email-spam-worker-{uuid4()}")
        self._profile_verified = False

    def run_once(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        if not self.config.enabled and not self.config.label_writes_enabled:
            return {"status": "disabled", "claimed_count": 0, "verified_count": 0, "failed_count": 0}

        if not self._profile_verified:
            self._writer.verify_profile()
            self._profile_verified = True

        hour_count = self._storage.mailbox_started_count_since(since=_iso(current - timedelta(hours=1)))
        day_count = self._storage.mailbox_started_count_since(since=_iso(current - timedelta(days=1)))
        remaining = (
            min(
                self.config.max_writes_per_hour - hour_count,
                self.config.max_writes_per_day - day_count,
                self.config.batch_size,
            )
            if self.config.enabled
            else 0
        )
        mailbox_rate_limited = self.config.enabled and remaining <= 0
        claimed = (
            self._storage.claim_mailbox_operations(
                lease_owner=self._worker_id,
                now=_iso(current),
                lease_expires_at=_iso(current + timedelta(seconds=self.config.lease_seconds)),
                limit=remaining,
            )
            if remaining > 0
            else []
        )
        verified_count = 0
        failed_count = 0
        dead_letter_count = 0
        for operation in claimed:
            try:
                operation_type = str(operation.get("operation_type") or "").strip().casefold()
                operation_kwargs = {
                    "message_id": str(operation.get("gmail_message_id") or ""),
                    "operation_id": str(operation.get("operation_id") or ""),
                }
                if operation_type == "move_to_spam":
                    result = self._writer.move_to_spam(**operation_kwargs)
                elif operation_type == "mark_read_complete":
                    result = self._writer.mark_read_complete(**operation_kwargs)
                else:
                    raise RuntimeError("unsupported_mailbox_operation")
                if not result.verified:
                    raise RuntimeError("gmail_mailbox_readback_not_verified")
                self._storage.update_message_labels(
                    gmail_message_id=result.message_id,
                    label_ids=list(result.labels_after),
                    now=_iso(current),
                )
                if operation_type == "move_to_spam":
                    self._storage.store_classification(
                        gmail_message_id=result.message_id,
                        taxonomy_version=str(operation.get("taxonomy_version") or ""),
                        logical_category_key="spam",
                        confidence=1.0,
                        decision_source="correction",
                        evidence={
                            "explicit_discord_spam_instruction": True,
                            "provider_readback_verified": True,
                        },
                        review_required=False,
                        corrected_by_user_id=str(operation.get("requested_by_user_id") or ""),
                        now=_iso(current),
                    )
                self._storage.set_user_state(
                    user_id=str(operation.get("requested_by_user_id") or ""),
                    discord_channel_id=str(operation.get("discord_channel_id") or ""),
                    gmail_message_id=result.message_id,
                    review_state="actioned",
                    disposition="spam" if operation_type == "move_to_spam" else "complete",
                    snoozed_until=None,
                    presented=False,
                    now=_iso(current),
                )
                self._storage.complete_mailbox_operation(
                    operation_id=str(operation.get("operation_id") or ""),
                    lease_owner=self._worker_id,
                    labels_before=list(result.labels_before),
                    labels_after=list(result.labels_after),
                    now=_iso(current),
                )
                verified_count += 1
            except Exception as exc:
                attempt = int(operation.get("attempt_count") or 1)
                retry_at = current + timedelta(seconds=min(900, 30 * (2 ** max(0, attempt - 1))))
                failed = self._storage.fail_mailbox_operation(
                    operation_id=str(operation.get("operation_id") or ""),
                    lease_owner=self._worker_id,
                    error_code=type(exc).__name__,
                    next_attempt_at=_iso(retry_at),
                    now=_iso(current),
                )
                failed_count += 1
                if failed.get("status") == "dead_letter":
                    dead_letter_count += 1
        label_hour_count = self._storage.label_started_count_since(
            since=_iso(current - timedelta(hours=1))
        )
        label_day_count = self._storage.label_started_count_since(
            since=_iso(current - timedelta(days=1))
        )
        label_remaining = (
            min(
                self.config.label_max_writes_per_hour - label_hour_count,
                self.config.label_max_writes_per_day - label_day_count,
                self.config.label_batch_size,
            )
            if self.config.label_writes_enabled
            else 0
        )
        label_rate_limited = self.config.label_writes_enabled and label_remaining <= 0
        label_claimed = (
            self._storage.claim_label_operations(
                lease_owner=self._worker_id,
                now=_iso(current),
                lease_expires_at=_iso(current + timedelta(seconds=self.config.lease_seconds)),
                limit=label_remaining,
            )
            if label_remaining > 0
            else []
        )
        label_verified_count = 0
        label_failed_count = 0
        label_dead_letter_count = 0
        allowed_label_names = tuple(self._managed_label_names.values())
        for operation in label_claimed:
            try:
                category_key = str(operation.get("logical_category_key") or "").strip().casefold()
                label_name = str(operation.get("gmail_label_name") or "").strip()
                expected_label_name = self._managed_label_names.get(category_key)
                if not expected_label_name or label_name != expected_label_name:
                    raise RuntimeError("managed_label_not_allowlisted")
                if str(operation.get("operation_type") or "").strip().casefold() != "add":
                    raise RuntimeError("unsupported_managed_label_operation")
                result = self._writer.apply_managed_category(
                    message_id=str(operation.get("gmail_message_id") or ""),
                    operation_id=str(operation.get("operation_id") or ""),
                    label_name=expected_label_name,
                    managed_label_names=allowed_label_names,
                )
                if not result.verified or not result.gmail_label_id:
                    raise RuntimeError("gmail_managed_label_readback_not_verified")
                self._storage.update_message_labels(
                    gmail_message_id=result.message_id,
                    label_ids=list(result.labels_after),
                    now=_iso(current),
                )
                self._storage.complete_label_operation(
                    operation_id=str(operation.get("operation_id") or ""),
                    lease_owner=self._worker_id,
                    gmail_label_id=result.gmail_label_id,
                    labels_before=list(result.labels_before),
                    labels_after=list(result.labels_after),
                    now=_iso(current),
                )
                label_verified_count += 1
            except Exception as exc:
                attempt = int(operation.get("attempt_count") or 1)
                retry_at = current + timedelta(seconds=min(900, 30 * (2 ** max(0, attempt - 1))))
                failed = self._storage.fail_label_operation(
                    operation_id=str(operation.get("operation_id") or ""),
                    lease_owner=self._worker_id,
                    error_code=type(exc).__name__,
                    next_attempt_at=_iso(retry_at),
                    now=_iso(current),
                )
                label_failed_count += 1
                if failed.get("status") == "dead_letter":
                    label_dead_letter_count += 1

        total_claimed = len(claimed) + len(label_claimed)
        return {
            "status": "rate_limited" if total_claimed == 0 and (mailbox_rate_limited or label_rate_limited) else "ok",
            "claimed_count": total_claimed,
            "verified_count": verified_count + label_verified_count,
            "failed_count": failed_count + label_failed_count,
            "dead_letter_count": dead_letter_count + label_dead_letter_count,
            "mailbox_claimed_count": len(claimed),
            "label_claimed_count": len(label_claimed),
            "label_verified_count": label_verified_count,
            "label_failed_count": label_failed_count,
            "started_last_hour": hour_count,
            "started_last_day": day_count,
            "label_started_last_hour": label_hour_count,
            "label_started_last_day": label_day_count,
        }
