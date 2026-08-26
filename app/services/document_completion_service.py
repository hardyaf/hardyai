from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from app.jobs.document_completion import (
    DOCUMENT_DISCORD_COMPLETION_JOB,
    DurableDocumentCompletionEnqueuer,
)
from app.jobs.repository import DurableJobRepository
from app.skills.domains.documents.query_service import DocumentQueryService


_TERMINAL_PROCESSING_STATES = frozenset(
    {"complete", "needs_review", "processing_incomplete", "failed", "cancelled", "protected_pending"}
)


@dataclass(frozen=True, slots=True)
class PreparedDocumentCompletion:
    disposition: Literal["waiting", "ready", "already_delivered", "rejected"]
    message: str | None = None
    error_code: str | None = None


class DocumentCompletionNotificationService:
    """Core-side coordinator for bounded terminal document notifications."""

    def __init__(
        self,
        *,
        repository: DurableJobRepository,
        documents: DocumentQueryService,
        poll_delay_seconds: float = 5.0,
        batch_size: int = 10,
        lease_seconds: float = 30.0,
        worker_id: str | None = None,
    ) -> None:
        self.repository = repository
        self.documents = documents
        self.enqueuer = DurableDocumentCompletionEnqueuer(repository)
        self.poll_delay_seconds = max(1.0, min(float(poll_delay_seconds), 60.0))
        self.batch_size = max(1, min(int(batch_size), 50))
        self.lease_seconds = max(5.0, min(float(lease_seconds), 120.0))
        self.worker_id = str(worker_id or f"document-discord-notify-{uuid4()}")

    def register_discord(self, **kwargs: Any) -> dict[str, Any]:
        return self.enqueuer.register_discord(**kwargs)

    def claim(self) -> list[dict[str, Any]]:
        return self.repository.claim_jobs(
            job_type=DOCUMENT_DISCORD_COMPLETION_JOB,
            worker_id=self.worker_id,
            limit=self.batch_size,
            lease_seconds=self.lease_seconds,
        )

    def prepare(self, job: dict[str, Any]) -> PreparedDocumentCompletion:
        if str(job.get("provider_operation_ref") or "").strip():
            return PreparedDocumentCompletion(disposition="already_delivered")
        payload = self._validated_payload(job)
        document_id = str(payload["document_id"])
        result = self.documents.execute(
            intent="documents.get",
            entities={"document_id": document_id},
            context={
                "principal_kind": "discord_adapter",
                "source": "discord",
                "request_source": "discord",
                "request_id": str(job.get("idempotency_key") or job.get("job_id") or ""),
                "discord_guild_id": str(payload.get("guild_id") or "dm"),
                "discord_channel_id": str(payload["channel_id"]),
                "external_user_id": str(payload["user_id"]),
                "document_attachment_ids": [document_id],
                "current_document_attachment_ids": [document_id],
            },
        )
        status = str(result.get("status") or "").strip().casefold()
        if status == "denied":
            return PreparedDocumentCompletion(
                disposition="rejected",
                error_code="document_presentation_denied",
            )
        if status != "ok":
            raise RuntimeError("document_presentation_unavailable")
        document = result.get("document")
        if not isinstance(document, dict):
            raise RuntimeError("document_presentation_invalid")
        processing_state = str(document.get("processing_state") or "").strip().casefold()
        document_state = str(document.get("state") or "").strip().casefold()
        if processing_state not in _TERMINAL_PROCESSING_STATES and document_state != "failed":
            return PreparedDocumentCompletion(disposition="waiting")
        message = str(result.get("message") or "").strip()
        if not message:
            raise RuntimeError("document_presentation_empty")
        return PreparedDocumentCompletion(disposition="ready", message=message[:1900])

    def defer(self, job: dict[str, Any]) -> bool:
        return self.repository.defer_job(
            job_id=str(job["job_id"]),
            worker_id=self.worker_id,
            fencing_token=int(job.get("lease_fencing_token") or 0),
            delay_seconds=self.poll_delay_seconds,
            reconcile_state="waiting_for_document_terminal_state",
        )

    def complete(self, job: dict[str, Any]) -> bool:
        return self.repository.complete_job(
            job_id=str(job["job_id"]),
            worker_id=self.worker_id,
            fencing_token=int(job.get("lease_fencing_token") or 0),
        )

    def record_delivery(self, job: dict[str, Any], *, message_id: str) -> bool:
        normalized_message_id = str(message_id or "").strip()
        if not normalized_message_id.isdigit() or len(normalized_message_id) > 32:
            raise ValueError("invalid Discord delivery message ID")
        return self.repository.set_provider_operation(
            job_id=str(job["job_id"]),
            worker_id=self.worker_id,
            fencing_token=int(job.get("lease_fencing_token") or 0),
            operation_ref=f"discord:{normalized_message_id}",
            reconcile_state="delivered",
        )

    def retry(self, job: dict[str, Any], *, error_code: str) -> bool:
        attempt = max(1, int(job.get("attempt_count") or 1))
        return self.repository.retry_job(
            job_id=str(job["job_id"]),
            worker_id=self.worker_id,
            fencing_token=int(job.get("lease_fencing_token") or 0),
            error_code=str(error_code or "document_notification_failed")[:120],
            delay_seconds=min(300.0, float(2 ** min(attempt, 8))),
        )

    def reject(self, job: dict[str, Any], *, error_code: str) -> bool:
        return self.repository.dead_letter_job(
            job_id=str(job["job_id"]),
            worker_id=self.worker_id,
            fencing_token=int(job.get("lease_fencing_token") or 0),
            error_code=error_code,
        )

    def heartbeat(
        self,
        *,
        status: str,
        claimed: int,
        delivered: int,
        error_code: str | None = None,
    ) -> None:
        self.repository.record_worker_heartbeat(
            worker_type="document_discord_notifications",
            worker_id=self.worker_id,
            status=status,
            last_error_code=error_code,
            metadata={"claimed": int(claimed), "delivered": int(delivered)},
        )

    @staticmethod
    def delivery_nonce(job: dict[str, Any]) -> int:
        material = str(job.get("idempotency_key") or job.get("job_id") or "").encode("utf-8")
        return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") & ((1 << 63) - 1)

    @staticmethod
    def target(job: dict[str, Any]) -> dict[str, str | None]:
        payload = DocumentCompletionNotificationService._validated_payload(job)
        return {
            "guild_id": str(payload["guild_id"]) if payload.get("guild_id") else None,
            "channel_id": str(payload["channel_id"]),
            "user_id": str(payload["user_id"]),
            "message_id": str(payload["message_id"]),
            "document_id": str(payload["document_id"]),
        }

    @staticmethod
    def _validated_payload(job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload")
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("unsupported_document_completion_payload")
        if payload.get("sink") != "discord":
            raise ValueError("unsupported_document_completion_sink")
        document_id = str(payload.get("document_id") or "").strip()
        if not document_id or document_id != str(job.get("aggregate_id") or "").strip():
            raise ValueError("document_completion_aggregate_mismatch")
        for name in ("channel_id", "user_id", "message_id", "attachment_id"):
            value = str(payload.get(name) or "").strip()
            if not value.isdigit() or len(value) > 32 or int(value) <= 0:
                raise ValueError(f"invalid_document_completion_{name}")
        guild_id = payload.get("guild_id")
        if guild_id is not None:
            normalized_guild = str(guild_id).strip()
            if not normalized_guild.isdigit() or len(normalized_guild) > 32 or int(normalized_guild) <= 0:
                raise ValueError("invalid_document_completion_guild_id")
        return payload
