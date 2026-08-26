from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from app.jobs.types import DurableJobStore, ResourceClass


DOCUMENT_DISCORD_COMPLETION_JOB = "document.discord_completion.v1"


def _numeric_id(value: str | int | None, *, label: str, optional: bool = False) -> str | None:
    normalized = str(value or "").strip()
    if optional and not normalized:
        return None
    if not normalized.isdigit() or len(normalized) > 32 or int(normalized) <= 0:
        raise ValueError(f"invalid {label}")
    return normalized


class DurableDocumentCompletionEnqueuer:
    """Registers content-free completion subscriptions in the shared durable ledger."""

    def __init__(
        self,
        repository: DurableJobStore,
        *,
        max_attempts: int = 8,
        deadline_seconds: float = 86400.0,
    ) -> None:
        self.repository = repository
        self.max_attempts = max(1, min(int(max_attempts), 20))
        self.deadline_seconds = max(300.0, min(float(deadline_seconds), 604800.0))

    def register_discord(
        self,
        *,
        document_id: str,
        guild_id: str | int | None,
        channel_id: str | int,
        user_id: str | int,
        message_id: str | int,
        attachment_id: str | int,
    ) -> dict[str, Any]:
        normalized_document_id = str(document_id or "").strip()
        if not normalized_document_id or len(normalized_document_id) > 128:
            raise ValueError("invalid document ID")
        target = {
            "sink": "discord",
            "guild_id": _numeric_id(guild_id, label="Discord guild ID", optional=True),
            "channel_id": _numeric_id(channel_id, label="Discord channel ID"),
            "user_id": _numeric_id(user_id, label="Discord user ID"),
            "message_id": _numeric_id(message_id, label="Discord message ID"),
            "attachment_id": _numeric_id(attachment_id, label="Discord attachment ID"),
        }
        identity = ":".join(
            (
                str(target["guild_id"] or "dm"),
                str(target["channel_id"]),
                str(target["user_id"]),
                str(target["message_id"]),
                str(target["attachment_id"]),
            )
        )
        digest = hashlib.sha256(identity.encode("ascii")).hexdigest()
        deadline = datetime.now(UTC) + timedelta(seconds=self.deadline_seconds)
        return self.repository.enqueue_job(
            job_type=DOCUMENT_DISCORD_COMPLETION_JOB,
            aggregate_id=normalized_document_id,
            idempotency_key=f"document-discord-completion:{digest}",
            payload={
                "schema_version": 1,
                "document_id": normalized_document_id,
                **target,
            },
            max_attempts=self.max_attempts,
            priority=40,
            resource_class=ResourceClass.CPU_SMALL.value,
            total_deadline_at=deadline.isoformat(),
        )

    def signal_terminal(self, *, document_id: str, state: str) -> int:
        normalized_document_id = str(document_id or "").strip()
        normalized_state = str(state or "").strip().casefold()
        if not normalized_document_id or not normalized_state:
            raise ValueError("document ID and terminal state are required")
        return self.repository.release_jobs(
            job_type=DOCUMENT_DISCORD_COMPLETION_JOB,
            aggregate_id=normalized_document_id,
            reconcile_state=f"terminal:{normalized_state}"[:80],
        )
