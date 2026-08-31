from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.services.event_log import EventLogService
from app.services.google.gmail_gateway import GmailHistoryExpiredError, GmailReadOnlyGateway
from app.services.google.gmail_mime import GmailMimeParser
from app.skills.domains.email_agent.classification import EmailClassifier
from app.skills.domains.email_agent.config import EmailAgentPermissions
from app.skills.domains.email_agent.query import EmailReadToolExecutor
from app.skills.domains.email_agent.storage import EmailAgentSQLiteStorage
from app.skills.domains.email_agent.summarization import (
    PROMPT_VERSION,
    EmailSummaryCompiler,
    deterministic_summary,
)


EMAIL_INTENTS = frozenset(
    {
        "email.list_recent",
        "email.search",
        "email.get_message",
        "email.get_thread",
        "email.summarize",
        "email.discuss",
        "email.status",
        "email.mark_reviewed",
        "email.snooze",
        "email.dismiss",
        "email.correct_category",
        "email.mark_needs_reply",
        "email.mark_complete",
        "email.mark_spam",
        "email.sync",
        "email.promote_to_list",
        "email.promote_to_calendar",
        "email.promote_to_task",
        "email.promote_to_wave",
    }
)
EMAIL_INTERACTIVE_INTENTS = EMAIL_INTENTS - {
    "email.sync",
    "email.promote_to_list",
    "email.promote_to_calendar",
    "email.promote_to_task",
    "email.promote_to_wave",
}
EMAIL_INTENT_CONTRACTS = (
    {
        "intent": "email.list_recent",
        "purpose": "List or summarize a collection of recent messages using time, unread, inbox, category, or sender scope.",
        "operation": "read",
        "entity_fields": ["query"],
    },
    {
        "intent": "email.search",
        "purpose": "Search for a collection of messages matching user-supplied criteria.",
        "operation": "read",
        "entity_fields": ["query"],
    },
    {
        "intent": "email.get_message",
        "purpose": "Show one previously identified email reference such as E1.",
        "operation": "read",
        "entity_fields": ["reference"],
    },
    {
        "intent": "email.get_thread",
        "purpose": "Show the thread containing one previously identified email reference.",
        "operation": "read",
        "entity_fields": ["reference"],
    },
    {
        "intent": "email.summarize",
        "purpose": "Summarize one previously identified email reference; do not use for a collection request.",
        "operation": "read",
        "entity_fields": ["reference"],
    },
    {
        "intent": "email.discuss",
        "purpose": "Discuss the topic or implications of one previously identified email reference.",
        "operation": "read",
        "entity_fields": ["reference", "query"],
    },
    {
        "intent": "email.status",
        "purpose": "Report content-free shared email agent synchronization and processing status.",
        "operation": "read",
        "entity_fields": [],
    },
    {
        "intent": "email.mark_reviewed",
        "purpose": "Mark one or more identified email references as reviewed in Jarvis state.",
        "operation": "write",
        "entity_fields": ["reference", "references"],
    },
    {
        "intent": "email.snooze",
        "purpose": "Snooze one or more identified email references until a user-supplied time.",
        "operation": "write",
        "entity_fields": ["reference", "references", "until"],
    },
    {
        "intent": "email.dismiss",
        "purpose": "Dismiss one or more identified email references from Jarvis active review state.",
        "operation": "write",
        "entity_fields": ["reference", "references"],
    },
    {
        "intent": "email.correct_category",
        "purpose": "Correct the shared category for one or more identified email references.",
        "operation": "write",
        "entity_fields": ["reference", "references", "category_key"],
    },
    {
        "intent": "email.mark_needs_reply",
        "purpose": "Mark one or more identified email references as needing a reply from the user.",
        "operation": "write",
        "entity_fields": ["reference", "references"],
    },
    {
        "intent": "email.mark_complete",
        "purpose": "Mark one or more identified email references complete and apply the configured Gmail label move.",
        "operation": "write",
        "entity_fields": ["reference", "references"],
    },
    {
        "intent": "email.mark_spam",
        "purpose": "Move one or more explicitly identified email references to Gmail spam when spam writes are enabled.",
        "operation": "write",
        "entity_fields": ["reference", "references"],
    },
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class EmailAgentRuntimeConfig:
    timezone_name: str = "America/New_York"
    sync_enabled: bool = False
    sync_interval_seconds: int = 600
    on_demand_stale_seconds: int = 120
    max_history_pages: int = 5
    max_messages_per_run: int = 100
    max_interactive_messages: int = 10
    reference_retention_hours: int = 72
    followup_context_minutes: int = 60
    allow_historical_backfill: bool = False
    label_writes_enabled: bool = False
    spam_writes_enabled: bool = False
    spam_max_operations_per_command: int = 5
    lease_minutes: int = 15
    max_provider_attempts: int = 3

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown email-agent timezone: {self.timezone_name}") from exc
        bounds = {
            "sync_interval_seconds": (60, 3600),
            "on_demand_stale_seconds": (30, 1800),
            "max_history_pages": (1, 10),
            "max_messages_per_run": (1, 200),
            "max_interactive_messages": (1, 20),
            "reference_retention_hours": (1, 720),
            "followup_context_minutes": (5, 240),
            "spam_max_operations_per_command": (1, 5),
            "lease_minutes": (1, 60),
            "max_provider_attempts": (1, 5),
        }
        for field_name, (minimum, maximum) in bounds.items():
            value = int(getattr(self, field_name))
            if not minimum <= value <= maximum:
                raise ValueError(f"{field_name} must be between {minimum} and {maximum}.")


class EmailAgentService:
    SKILL_ID = "skill.email.agent"

    def __init__(
        self,
        *,
        storage: EmailAgentSQLiteStorage,
        gateway: GmailReadOnlyGateway,
        permissions: EmailAgentPermissions,
        mime_parser: GmailMimeParser,
        classifier: EmailClassifier,
        summary_compiler: EmailSummaryCompiler | None,
        config: EmailAgentRuntimeConfig,
        event_log: EventLogService | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._storage = storage
        self._gateway = gateway
        self._permissions = permissions
        self._mime_parser = mime_parser
        self._classifier = classifier
        self._summary_compiler = summary_compiler
        self.config = config
        self._event_log = event_log
        self._worker_id = str(worker_id or f"email-agent-{uuid4()}")
        self._typed_reads = EmailReadToolExecutor(
            storage=storage,
            permissions=permissions,
            timezone_name=config.timezone_name,
            reference_retention_hours=config.reference_retention_hours,
            stale_seconds=config.on_demand_stale_seconds,
        )

    def run_due(self, *, now: datetime | None = None) -> dict[str, Any] | None:
        if not self.config.sync_enabled:
            return None
        current = self._normalize_now(now)
        bucket = int(current.timestamp()) // self.config.sync_interval_seconds
        result = self._run_sync(
            now=current,
            run_kind="scheduled",
            bucket_key=f"scheduled:{bucket}",
        )
        return self._with_label_reconciliation(result=result, now=current)

    def sync_if_stale(self, *, now: datetime | None = None) -> dict[str, Any]:
        if not self.config.sync_enabled:
            return {"status": "disabled", "reason": "sync_disabled"}
        current = self._normalize_now(now)
        state = self._storage.get_sync_state()
        if state is not None:
            last_success = self._parse_iso(state.get("last_success_at"))
            if last_success is not None:
                age = (current - last_success).total_seconds()
                if age < self.config.on_demand_stale_seconds:
                    return self._with_label_reconciliation(
                        result={"status": "fresh", "age_seconds": max(0, int(age))},
                        now=current,
                    )
        bucket = int(current.timestamp()) // self.config.on_demand_stale_seconds
        result = self._run_sync(
            now=current,
            run_kind="on_demand",
            bucket_key=f"on_demand:{bucket}",
        )
        return self._with_label_reconciliation(result=result, now=current)

    def _with_label_reconciliation(
        self,
        *,
        result: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        if not self.config.label_writes_enabled:
            return result
        candidates = self._storage.list_category_label_candidates(
            taxonomy_version=self._permissions.taxonomy_version,
            limit=self.config.max_messages_per_run,
        )
        managed_labels = self._permissions.managed_gmail_labels
        queued = 0
        for row in candidates:
            category_key = str(row.get("logical_category_key") or "").strip().casefold()
            label_name = managed_labels.get(category_key)
            message_id = str(row.get("gmail_message_id") or "").strip()
            classification_updated_at = str(row.get("classification_updated_at") or "").strip()
            if not label_name or not message_id or not classification_updated_at:
                continue
            self._storage.enqueue_label_operation(
                gmail_message_id=message_id,
                taxonomy_version=self._permissions.taxonomy_version,
                logical_category_key=category_key,
                gmail_label_name=label_name,
                operation_type="add",
                idempotency_key=(
                    f"email-label:add:v1:{self._permissions.taxonomy_version}:"
                    f"{message_id}:{category_key}:{classification_updated_at}"
                ),
                max_attempts=self.config.max_provider_attempts,
                now=_iso(now),
            )
            queued += 1
        return {**result, "managed_label_operations_queued": queued}

    def bootstrap_recent_canaries(
        self,
        *,
        lookback_minutes: int,
        expected_count: int | None = None,
        allow_recheck: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Explicit operator-only import for canaries sent just before first activation."""

        minutes = int(lookback_minutes)
        if not 1 <= minutes <= 1440:
            raise ValueError("Canary lookback must be between 1 and 1440 minutes.")
        expected = None if expected_count is None else int(expected_count)
        if expected is not None and not 1 <= expected <= self.config.max_messages_per_run:
            raise ValueError("Expected canary count is outside the configured message cap.")
        current = self._normalize_now(now)
        state = self._storage.get_sync_state()
        if state is not None and state.get("last_success_at") and not allow_recheck:
            return {
                "status": "already_activated",
                "message": "The email cursor already has a completed synchronization.",
            }
        if state is None:
            profile = self._gateway.profile()
            state = self._storage.activate(
                now=_iso(current - timedelta(minutes=minutes)),
                history_id=profile.history_id,
            )

        activation = self._parse_iso(state.get("activation_at"))
        if activation is None:
            raise RuntimeError("email_bootstrap_missing_activation_watermark")
        bucket_key = (
            f"bootstrap_recheck:{int(current.timestamp()) // 60}"
            if state.get("last_success_at")
            else f"bootstrap:{int(activation.timestamp())}"
        )
        claim = self._storage.claim_sync_run(
            bucket_key=bucket_key,
            run_kind="recovery",
            lease_owner=self._worker_id,
            now=_iso(current),
            lease_expires_at=_iso(current + timedelta(minutes=self.config.lease_minutes)),
            stale_before=_iso(current - timedelta(minutes=self.config.lease_minutes)),
            max_attempts=self.config.max_provider_attempts,
        )
        if not claim.get("claimed"):
            return {
                "status": "not_due",
                "reason": claim.get("reason"),
                "bucket_key": bucket_key,
            }
        run_id = str(claim.get("run_id") or "")
        counts = {
            "page_count": 0,
            "candidate_count": 0,
            "accepted_count": 0,
            "ignored_count": 0,
            "failed_count": 0,
            "summary_count": 0,
            "classification_count": 0,
        }
        alias_query = " OR ".join(
            f"deliveredto:{alias}" for alias in self._permissions.destination_aliases
        )
        query = f"after:{max(0, int(activation.timestamp()))} {{{alias_query}}}"
        page_token: str | None = None
        blocking_failure = False
        try:
            for _ in range(self.config.max_history_pages):
                remaining = self.config.max_messages_per_run - counts["candidate_count"]
                if remaining <= 0:
                    break
                page = self._gateway.search_messages(
                    query=query,
                    page_token=page_token,
                    limit=remaining,
                )
                counts["page_count"] += 1
                refs = list(page.messages)[:remaining]
                counts["candidate_count"] += len(refs)
                for ref in refs:
                    outcome = self._process_message_id(
                        message_id=ref.message_id,
                        now=current,
                        activation_at=str(state.get("activation_at") or ""),
                    )
                    for key in (
                        "accepted_count",
                        "ignored_count",
                        "failed_count",
                        "summary_count",
                        "classification_count",
                    ):
                        counts[key] += int(outcome.get(key) or 0)
                    if outcome.get("retry_required"):
                        blocking_failure = True
                self._storage.update_run_counts(
                    run_id=run_id,
                    counts=counts,
                    now=_iso(current),
                )
                page_token = page.next_page_token
                if blocking_failure or not page_token:
                    break
            if blocking_failure:
                raise RuntimeError("email_bootstrap_message_processing_incomplete")
            if page_token:
                raise RuntimeError("email_bootstrap_page_cap_reached")
            self._storage.complete_sync_run(
                run_id=run_id,
                counts=counts,
                now=_iso(current),
                history_id=str(state.get("history_id") or ""),
                continuation_token=None,
                recovered=True,
            )
        except Exception as exc:
            return self._fail_run(
                run_id=run_id,
                bucket_key=bucket_key,
                counts=counts,
                now=current,
                exc=exc,
            )
        status = "ok"
        if expected is not None and counts["accepted_count"] != expected:
            status = "count_mismatch"
        result = {
            "status": status,
            "run_id": run_id,
            "bucket_key": bucket_key,
            "expected_count": expected,
            "lookback_minutes": minutes,
            **counts,
        }
        self._record("email.sync.bootstrap_completed", result)
        return result

    def _run_sync(
        self,
        *,
        now: datetime,
        run_kind: str,
        bucket_key: str,
    ) -> dict[str, Any]:
        state = self._storage.get_sync_state()
        if state is None:
            profile = self._gateway.profile()
            state = self._storage.activate(now=_iso(now), history_id=profile.history_id)
            result = {
                "status": "activated",
                "activation_at": state.get("activation_at"),
                "gmail_profile": profile.email_address,
                "historical_backfill": False,
            }
            self._record("email.sync.activated", result)
            return result

        lease_expiry = now + timedelta(minutes=self.config.lease_minutes)
        claim = self._storage.claim_sync_run(
            bucket_key=bucket_key,
            run_kind=run_kind,
            lease_owner=self._worker_id,
            now=_iso(now),
            lease_expires_at=_iso(lease_expiry),
            stale_before=_iso(now - timedelta(minutes=self.config.lease_minutes)),
            max_attempts=self.config.max_provider_attempts,
        )
        if not claim.get("claimed"):
            return {
                "status": "not_due",
                "reason": claim.get("reason"),
                "bucket_key": bucket_key,
            }
        run_id = str(claim.get("run_id") or "")
        counts = {
            "page_count": 0,
            "candidate_count": 0,
            "accepted_count": 0,
            "ignored_count": 0,
            "failed_count": 0,
            "summary_count": 0,
            "classification_count": 0,
        }
        self._record(
            "email.sync.started",
            {"run_id": run_id, "bucket_key": bucket_key, "run_kind": run_kind},
        )
        try:
            return self._consume_history(
                state=state,
                run_id=run_id,
                bucket_key=bucket_key,
                now=now,
                counts=counts,
            )
        except GmailHistoryExpiredError:
            try:
                return self._recover_expired_cursor(
                    state=state,
                    run_id=run_id,
                    bucket_key=bucket_key,
                    now=now,
                    counts=counts,
                )
            except Exception as exc:
                return self._fail_run(run_id=run_id, bucket_key=bucket_key, counts=counts, now=now, exc=exc)
        except Exception as exc:
            return self._fail_run(run_id=run_id, bucket_key=bucket_key, counts=counts, now=now, exc=exc)

    def _consume_history(
        self,
        *,
        state: dict[str, Any],
        run_id: str,
        bucket_key: str,
        now: datetime,
        counts: dict[str, int],
    ) -> dict[str, Any]:
        committed_history_id = str(state.get("history_id") or "").strip()
        if not committed_history_id:
            raise RuntimeError("email_sync_state_missing_history_id")
        page_token = str(state.get("continuation_token") or "").strip() or None
        latest_history_id = committed_history_id
        blocking_failure = False
        for _ in range(self.config.max_history_pages):
            remaining = self.config.max_messages_per_run - counts["candidate_count"]
            if remaining <= 0:
                break
            page = self._gateway.list_history(
                start_history_id=committed_history_id,
                page_token=page_token,
                limit=remaining,
            )
            counts["page_count"] += 1
            latest_history_id = page.history_id or latest_history_id
            refs = list(page.messages)[:remaining]
            counts["candidate_count"] += len(refs)
            for ref in refs:
                outcome = self._process_message_id(
                    message_id=ref.message_id,
                    now=now,
                    activation_at=str(state.get("activation_at") or ""),
                )
                for key in ("accepted_count", "ignored_count", "failed_count", "summary_count", "classification_count"):
                    counts[key] += int(outcome.get(key) or 0)
                if outcome.get("retry_required"):
                    blocking_failure = True
            self._storage.update_run_counts(run_id=run_id, counts=counts, now=_iso(now))
            page_token = page.next_page_token
            if blocking_failure or not page_token:
                break

        if blocking_failure:
            raise RuntimeError("message_processing_incomplete")
        continuation = page_token
        committed_after = committed_history_id if continuation else latest_history_id
        self._storage.complete_sync_run(
            run_id=run_id,
            counts=counts,
            now=_iso(now),
            history_id=committed_after,
            continuation_token=continuation,
        )
        result = {
            "status": "partial" if continuation else "ok",
            "run_id": run_id,
            "bucket_key": bucket_key,
            "continuation_pending": bool(continuation),
            **counts,
        }
        self._record("email.sync.completed", result)
        return result

    def _recover_expired_cursor(
        self,
        *,
        state: dict[str, Any],
        run_id: str,
        bucket_key: str,
        now: datetime,
        counts: dict[str, int],
    ) -> dict[str, Any]:
        baseline = self._parse_iso(state.get("last_success_at")) or self._parse_iso(state.get("activation_at"))
        if baseline is None:
            raise RuntimeError("email_recovery_missing_watermark")
        alias_query = " OR ".join(
            f"deliveredto:{alias}" for alias in self._permissions.destination_aliases
        )
        query = f"after:{max(0, int(baseline.timestamp()))} {{{alias_query}}}"
        page_token: str | None = None
        blocking_failure = False
        for _ in range(self.config.max_history_pages):
            remaining = self.config.max_messages_per_run - counts["candidate_count"]
            if remaining <= 0:
                break
            page = self._gateway.search_messages(query=query, page_token=page_token, limit=remaining)
            counts["page_count"] += 1
            refs = list(page.messages)[:remaining]
            counts["candidate_count"] += len(refs)
            for ref in refs:
                outcome = self._process_message_id(
                    message_id=ref.message_id,
                    now=now,
                    activation_at=str(state.get("activation_at") or ""),
                )
                for key in ("accepted_count", "ignored_count", "failed_count", "summary_count", "classification_count"):
                    counts[key] += int(outcome.get(key) or 0)
                if outcome.get("retry_required"):
                    blocking_failure = True
            self._storage.update_run_counts(run_id=run_id, counts=counts, now=_iso(now))
            page_token = page.next_page_token
            if blocking_failure or not page_token:
                break
        if blocking_failure:
            raise RuntimeError("email_recovery_message_processing_incomplete")
        if page_token:
            raise RuntimeError("email_recovery_page_cap_reached")
        fresh_history_id = self._gateway.current_history_id()
        self._storage.complete_sync_run(
            run_id=run_id,
            counts=counts,
            now=_iso(now),
            history_id=fresh_history_id,
            continuation_token=None,
            recovered=True,
        )
        result = {
            "status": "ok",
            "run_id": run_id,
            "bucket_key": bucket_key,
            "recovered_history_cursor": True,
            **counts,
        }
        self._record("email.sync.cursor_recovered", result)
        return result

    def _process_message_id(
        self,
        *,
        message_id: str,
        now: datetime,
        activation_at: str,
    ) -> dict[str, Any]:
        try:
            raw = self._gateway.get_message(message_id=message_id, format="full")
            parsed = self._mime_parser.parse(
                raw,
                attachment_loader=lambda gmail_message_id, attachment_id: self._gateway.get_attachment_bytes(
                    message_id=gmail_message_id,
                    attachment_id=attachment_id,
                ),
            )
            route = self._permissions.route_for_delivery_addresses(parsed.trusted_delivery_addresses)
            if route is None:
                self._storage.clear_message_failure(gmail_message_id=message_id)
                return {"ignored_count": 1, "reason": "unrecognized_or_ambiguous_delivery_route"}
            activation = self._parse_iso(activation_at)
            if (
                not self.config.allow_historical_backfill
                and activation is not None
                and parsed.internal_date_ms < int(activation.timestamp() * 1000)
            ):
                self._storage.clear_message_failure(gmail_message_id=message_id)
                return {"ignored_count": 1, "reason": "before_activation_watermark"}

            upserted = self._storage.upsert_message(
                record=parsed.metadata_record(source_route_key=route.route_key),
                now=_iso(now),
            )
            current = self._storage.get_message(
                gmail_message_id=parsed.gmail_message_id,
                taxonomy_version=self._permissions.taxonomy_version,
            ) or {}
            needs_compile = bool(upserted.get("content_changed")) or not current.get("summary_text")
            needs_classify = bool(upserted.get("content_changed")) or not current.get("logical_category_key")
            summary_count = 0
            classification_count = 0
            if needs_compile:
                summary = (
                    self._summary_compiler.summarize(parsed)
                    if self._summary_compiler is not None
                    else None
                ) or deterministic_summary(parsed)
                self._storage.store_summary(
                    scope_type="message",
                    scope_id=parsed.gmail_message_id,
                    source_hash=parsed.canonical_body_hash,
                    summary_text=summary.summary,
                    structured_summary=summary.structured(
                        attachments=[item.to_dict() for item in parsed.attachment_metadata]
                    ),
                    model_provider=summary.model_provider,
                    model_name=summary.model_name,
                    prompt_version=PROMPT_VERSION,
                    taxonomy_version=self._permissions.taxonomy_version,
                    now=_iso(now),
                )
                summary_count = 1
            else:
                summary = deterministic_summary(parsed)
                structured = current.get("structured_summary")
                if isinstance(structured, dict):
                    summary = summary.__class__(
                        summary=str(current.get("summary_text") or summary.summary),
                        why_it_matters=str(structured.get("why_it_matters") or ""),
                        people_or_organizations=tuple(structured.get("people_or_organizations") or []),
                        explicit_dates=tuple(structured.get("explicit_dates") or []),
                        explicit_deadlines=tuple(structured.get("explicit_deadlines") or []),
                        questions=tuple(structured.get("questions") or []),
                        decisions=tuple(structured.get("decisions") or []),
                        action_candidates=tuple(structured.get("action_candidates") or []),
                        uncertainty=str(structured.get("uncertainty") or ""),
                        model_provider=str(current.get("model_provider") or "deterministic"),
                        model_name=str(current.get("model_name") or "none"),
                        semantic=bool(structured.get("semantic")),
                    )
            if needs_classify:
                decision = self._classifier.classify(message=parsed, route=route, summary=summary)
                self._storage.store_classification(
                    gmail_message_id=parsed.gmail_message_id,
                    taxonomy_version=self._permissions.taxonomy_version,
                    logical_category_key=decision.category_key,
                    confidence=decision.confidence,
                    decision_source=decision.decision_source,
                    evidence=decision.evidence,
                    review_required=decision.review_required,
                    corrected_by_user_id=None,
                    now=_iso(now),
                )
                classification_count = 1
            self._storage.clear_message_failure(gmail_message_id=message_id)
            return {
                "accepted_count": 1,
                "summary_count": summary_count,
                "classification_count": classification_count,
            }
        except Exception as exc:
            failure = self._storage.record_message_failure(
                gmail_message_id=message_id,
                error_code=type(exc).__name__,
                now=_iso(now),
                max_attempts=self.config.max_provider_attempts,
            )
            self._record(
                "email.sync.message_failed",
                {
                    "message_id_hash": self._opaque_id(message_id),
                    "error_type": type(exc).__name__,
                    "status": failure.get("status"),
                    "attempt_count": failure.get("attempt_count"),
                },
            )
            return {
                "failed_count": 1,
                "retry_required": failure.get("status") != "dead_letter",
            }

    def execute(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        intent_value = str(intent or "").strip().casefold()
        if intent_value not in EMAIL_INTENTS:
            return {"status": "error", "message": "Unsupported email-agent intent."}
        grant = self._permissions.authorize(context)
        if grant is None:
            return {
                "status": "policy_denied",
                "message": "The shared email agent is available only in an authorized private email channel.",
            }
        if intent_value == "email.sync":
            return {
                "status": "scheduler_owned",
                "message": "Email synchronization runs through its bounded scheduler.",
            }
        if intent_value == "email.status":
            return self._status()
        if intent_value in {"email.promote_to_task", "email.promote_to_wave"}:
            target = "task system" if intent_value.endswith("task") else "Wave ticket provider"
            return {
                "status": "capability_gate",
                "message": f"The {target} is not configured yet. I can still summarize the email or help put it on a list or calendar.",
            }
        if intent_value in {"email.promote_to_list", "email.promote_to_calendar"}:
            return {
                "status": "capability_gate",
                "message": "Email promotion is staged for the next approved build phase; no downstream action was taken.",
            }

        sync_status = self.sync_if_stale()
        if intent_value == "email.mark_spam":
            return self._mark_spam(entities=entities, context=context)
        if intent_value == "email.mark_complete":
            return self._mark_complete(entities=entities, context=context)
        if intent_value in {"email.list_recent", "email.search"}:
            return self._list_or_search(
                intent=intent_value,
                entities=entities,
                context=context,
                sync_status=sync_status,
            )
        if (
            intent_value == "email.summarize"
            and not any(
                str(entities.get(key) or "").strip()
                for key in ("reference", "email_reference", "message_id")
            )
            and self._looks_like_collection_query(str(entities.get("query") or ""))
        ):
            return self._list_or_search(
                intent="email.list_recent",
                entities=entities,
                context=context,
                sync_status=sync_status,
            )
        if intent_value in {"email.get_message", "email.summarize", "email.discuss", "email.get_thread"}:
            return self._show_reference(
                intent=intent_value,
                entities=entities,
                context=context,
                sync_status=sync_status,
            )
        if intent_value in {
            "email.mark_reviewed",
            "email.snooze",
            "email.dismiss",
            "email.mark_needs_reply",
        }:
            return self._update_local_state(intent=intent_value, entities=entities, context=context)
        if intent_value == "email.correct_category":
            return self._correct_category(entities=entities, context=context)
        return {"status": "error", "message": "Email intent is not implemented."}

    def canonicalize_tool_arguments(
        self,
        *,
        tool_id: str,
        validated_arguments: dict[str, Any],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        return self._typed_reads.canonicalize(
            tool_id=tool_id,
            validated_arguments=validated_arguments,
            request_context=request_context,
        )

    def execute_tool(
        self,
        *,
        envelope: Any,
        services: dict[str, Any],
    ) -> dict[str, Any]:
        del services
        return self._typed_reads.execute(envelope=envelope)

    def capability_access(self, *, context: dict[str, Any]) -> dict[str, Any]:
        """Return safe, content-free capability status for Main's runtime catalog."""
        authorized = self._permissions.authorize(context) is not None
        if authorized:
            return {
                "configured": True,
                "authorized_here": True,
                "availability": "available",
                "access_note": "The shared email agent is available in this private channel.",
                "main_intents": sorted(EMAIL_INTERACTIVE_INTENTS),
                "intent_contracts": [dict(item) for item in EMAIL_INTENT_CONTRACTS],
            }
        return {
            "configured": True,
            "authorized_here": False,
            "availability": "restricted",
            "access_note": "The shared email agent is available only in an authorized private email channel.",
            "main_intents": sorted(EMAIL_INTERACTIVE_INTENTS),
            "intent_contracts": [dict(item) for item in EMAIL_INTENT_CONTRACTS],
        }

    def working_context_hint(self, *, context: dict[str, Any]) -> dict[str, Any]:
        """Return a non-content email anchor for follow-ups across session rotation."""
        if self._permissions.authorize(context) is None:
            return {}
        user_id, channel_id = self._scope(context)
        current = self._storage.latest_reference_set(
            user_id=user_id,
            discord_channel_id=channel_id,
            now=_iso(_utc_now()),
        )
        if current is None:
            return {}
        created_at = self._parse_iso(current.get("created_at"))
        if created_at is None or created_at < _utc_now() - timedelta(minutes=self.config.followup_context_minutes):
            return {}
        message_ids = current.get("ordered_message_ids")
        return {
            "skill_id": "skill.email.agent",
            "context_kind": "email_reference_set",
            "last_email_reference_set_id": current.get("reference_set_id"),
            "last_email_result_count": len(message_ids) if isinstance(message_ids, list) else 0,
            "created_at": current.get("created_at"),
            "expires_at": current.get("expires_at"),
        }

    def _list_or_search(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        context: dict[str, Any],
        sync_status: dict[str, Any],
    ) -> dict[str, Any]:
        query_text = str(entities.get("query") or "").strip()
        filters = self._query_filters(query_text=query_text, entities=entities)
        user_id, channel_id = self._scope(context)
        created_at = _utc_now()
        rows = self._storage.list_messages(
            taxonomy_version=self._permissions.taxonomy_version,
            limit=self.config.max_interactive_messages,
            since_internal_date=filters.get("since_internal_date"),
            source_route_key=filters.get("source_route_key"),
            category_key=filters.get("category_key"),
            query_text=filters.get("search_text") if intent == "email.search" else None,
            user_id=user_id,
            discord_channel_id=channel_id,
            visibility=str(filters.get("visibility") or "active"),
            now=_iso(created_at),
        )
        if not rows:
            visibility = str(filters.get("visibility") or "active")
            empty_message = {
                "unseen": "You do not have any new unseen email matching that request.",
                "needs_reply": "You do not have any email marked Needs reply matching that request.",
                "completed": "I did not find completed email matching that request.",
                "spam": "I did not find Spam email matching that request.",
            }.get(visibility, "You do not have any unhandled email matching that request.")
            return {
                "status": "ok",
                "message": empty_message,
                "sync_status": sync_status.get("status"),
                "visibility": visibility,
                "email_context_entities": [],
            }
        reference_set = self._storage.create_reference_set(
            user_id=user_id,
            discord_channel_id=channel_id,
            query_text=query_text,
            message_ids=[str(row["gmail_message_id"]) for row in rows],
            thread_ids=[str(row["gmail_thread_id"]) for row in rows],
            focused_message_id=str(rows[0]["gmail_message_id"]),
            focused_thread_id=str(rows[0]["gmail_thread_id"]),
            created_at=_iso(created_at),
            expires_at=_iso(created_at + timedelta(hours=self.config.reference_retention_hours)),
        )
        for row in rows:
            self._storage.set_user_state(
                user_id=user_id,
                discord_channel_id=channel_id,
                gmail_message_id=str(row["gmail_message_id"]),
                review_state="presented",
                snoozed_until=None,
                presented=True,
                now=_iso(created_at),
            )
        message = self._format_digest(rows)
        return {
            "status": "ok",
            "message": message,
            "result_count": len(rows),
            "reference_set_id": reference_set["reference_set_id"],
            "sync_status": sync_status.get("status"),
            "visibility": filters.get("visibility"),
            "email_context_entities": self._context_entities(rows, reference_set),
        }

    def _show_reference(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        context: dict[str, Any],
        sync_status: dict[str, Any],
    ) -> dict[str, Any]:
        resolved = self._resolve_message(entities=entities, context=context)
        if resolved is None:
            return {
                "status": "needs_clarification",
                "message": "I could not identify that email from your current channel references.",
                "question": "Which email reference, such as E1 or E2, do you mean?",
                "missing_fields": ["email_reference"],
            }
        message_id = str(resolved.get("gmail_message_id") or "")
        row = self._storage.get_message(
            gmail_message_id=message_id,
            taxonomy_version=self._permissions.taxonomy_version,
        )
        if row is None:
            return {"status": "not_found", "message": "That email is no longer available in the indexed mailbox."}
        if intent == "email.get_thread":
            thread_rows = self._storage.get_thread(
                gmail_thread_id=str(row["gmail_thread_id"]),
                taxonomy_version=self._permissions.taxonomy_version,
                limit=self.config.max_interactive_messages,
            )
            message = "\n\n".join(
                self._format_row(item, index=index) for index, item in enumerate(thread_rows, start=1)
            )
            rows = thread_rows
        else:
            label = str(resolved.get("reference") or "E1")
            message = self._format_row(row, index=max(1, _reference_number(label)))
            rows = [row]
        user_id, channel_id = self._scope(context)
        created_at = _utc_now()
        reference_set = self._storage.create_reference_set(
            user_id=user_id,
            discord_channel_id=channel_id,
            query_text=str(entities.get("query") or entities.get("reference") or "reference"),
            message_ids=[str(item["gmail_message_id"]) for item in rows],
            thread_ids=[str(item["gmail_thread_id"]) for item in rows],
            focused_message_id=message_id,
            focused_thread_id=str(row["gmail_thread_id"]),
            created_at=_iso(created_at),
            expires_at=_iso(created_at + timedelta(hours=self.config.reference_retention_hours)),
        )
        return {
            "status": "ok",
            "message": message,
            "reference_set_id": reference_set["reference_set_id"],
            "sync_status": sync_status.get("status"),
            "email_context_entities": self._context_entities(rows, reference_set),
        }

    def _update_local_state(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        resolved_rows = self._resolve_action_targets(
            entities=entities,
            context=context,
            allow_all_current=True,
            max_count=self.config.spam_max_operations_per_command,
        )
        if not resolved_rows:
            return {
                "status": "needs_clarification",
                "message": "I could not identify the current email reference or references.",
                "question": "Please list the emails again, then name the E-reference or say all of those.",
                "missing_fields": ["email_reference"],
            }
        state = {
            "email.mark_reviewed": "reviewed",
            "email.snooze": "snoozed",
            "email.dismiss": "dismissed",
            "email.mark_needs_reply": "actioned",
        }[intent]
        disposition = {
            "email.mark_reviewed": "complete",
            "email.snooze": "snoozed",
            "email.dismiss": "dismissed",
            "email.mark_needs_reply": "needs_reply",
        }[intent]
        snoozed_until = None
        if state == "snoozed":
            snoozed_until = self._snooze_until(str(entities.get("until") or entities.get("when_hint") or ""))
            if snoozed_until is None:
                return {
                    "status": "needs_clarification",
                    "message": "I can snooze it locally, but I need a time.",
                    "question": "When should Jarvis bring this email back?",
                    "missing_fields": ["snooze_until"],
                }
        user_id, channel_id = self._scope(context)
        now = _iso(_utc_now())
        for _, resolved in resolved_rows:
            self._storage.set_user_state(
                user_id=user_id,
                discord_channel_id=channel_id,
                gmail_message_id=str(resolved["gmail_message_id"]),
                review_state=state,
                disposition=disposition,
                snoozed_until=snoozed_until,
                presented=False,
                now=now,
            )
        references = [str(reference or "that email") for reference, _ in resolved_rows]
        reference_text = ", ".join(references)
        verb = {
            "email.mark_reviewed": "marked complete in Jarvis",
            "email.dismiss": "dismissed",
            "email.snooze": "snoozed",
            "email.mark_needs_reply": "marked Needs reply",
        }[intent]
        suffix = f" until {snoozed_until}" if snoozed_until else ""
        visibility_note = (
            "It will remain in normal summaries until you complete or dismiss it."
            if disposition == "needs_reply"
            else "Handled email will stay out of normal inbox summaries."
        )
        return {
            "status": "ok",
            "message": (
                f"I {verb} {reference_text}{suffix}. "
                f"{visibility_note} Gmail read/archive state was not changed."
            ),
            "operation_id": str(uuid4()),
            "operation_ids": [],
            "local_state": state,
            "disposition": disposition,
            "gmail_message_id": (
                str(resolved_rows[0][1]["gmail_message_id"])
                if len(resolved_rows) == 1 else None
            ),
            "gmail_message_ids": [str(resolved["gmail_message_id"]) for _, resolved in resolved_rows],
        }

    def _correct_category(
        self,
        *,
        entities: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        resolved = self._resolve_message(entities=entities, context=context)
        category = self._resolve_category(str(entities.get("category_key") or entities.get("category") or ""))
        if resolved is None or category is None:
            missing = []
            if resolved is None:
                missing.append("email_reference")
            if category is None:
                missing.append("category_key")
            return {
                "status": "needs_clarification",
                "message": "I need both the email reference and one configured category.",
                "question": "Which email and shared category should I use?",
                "missing_fields": missing,
            }
        user_id, _ = self._scope(context)
        stored = self._storage.store_classification(
            gmail_message_id=str(resolved["gmail_message_id"]),
            taxonomy_version=self._permissions.taxonomy_version,
            logical_category_key=category,
            confidence=1.0,
            decision_source="correction",
            evidence={"explicit_discord_correction": True},
            review_required=False,
            corrected_by_user_id=user_id,
            now=_iso(_utc_now()),
        )
        label_queue = self._with_label_reconciliation(
            result={"status": "ok"},
            now=_utc_now(),
        )
        display = next(item.display_name for item in self._permissions.categories if item.key == category)
        return {
            "status": "ok",
            "message": (
                f"I corrected {resolved.get('reference') or 'that email'} to {display} in Jarvis. "
                + (
                    "The matching managed Gmail label is queued for verified synchronization."
                    if self.config.label_writes_enabled
                    else "No Gmail label was changed."
                )
            ),
            "operation_id": str(uuid4()),
            "classification": {
                "category_key": stored.get("logical_category_key"),
                "audience": "shared",
                "decision_source": stored.get("decision_source"),
            },
            "gmail_message_id": str(resolved["gmail_message_id"]),
            "managed_label_operations_queued": label_queue.get("managed_label_operations_queued", 0),
        }

    def _mark_spam(
        self,
        *,
        entities: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        query = str(entities.get("query") or "").strip()
        if not self._is_explicit_positive_spam_request(query):
            return {
                "status": "needs_clarification",
                "message": "I did not queue a Gmail change because spam moves require explicit positive wording.",
                "question": "Name the current reference, for example: E2 is spam.",
                "missing_fields": ["email_reference"],
            }
        if not self.config.spam_writes_enabled:
            return {
                "status": "capability_gate",
                "message": "Manual Gmail spam moves are disabled, so no mailbox change was queued.",
            }
        external_request_id = str(
            context.get("external_message_id") or context.get("request_id") or ""
        ).strip()
        if not external_request_id.startswith("discord:"):
            return {
                "status": "policy_denied",
                "message": "Spam moves require an immutable Discord message request ID.",
            }

        raw_references = entities.get("references")
        if isinstance(raw_references, list):
            references = [
                str(item).strip().upper()
                for item in raw_references
                if str(item).strip()
            ]
        else:
            reference = str(
                entities.get("reference") or entities.get("email_reference") or ""
            ).strip()
            references = [reference] if reference else []
        references = list(dict.fromkeys(references))
        if not references:
            return {
                "status": "needs_clarification",
                "message": "I need the current email reference before changing Gmail.",
                "question": "Which email is spam? Use a reference such as E1.",
                "missing_fields": ["email_reference"],
            }
        if len(references) > self.config.spam_max_operations_per_command:
            return {
                "status": "needs_clarification",
                "message": "That exceeds the cautious manual spam limit.",
                "question": (
                    f"Name at most {self.config.spam_max_operations_per_command} E-references "
                    "in one command."
                ),
                "missing_fields": ["email_reference"],
            }

        user_id, channel_id = self._scope(context)
        now = _iso(_utc_now())
        resolved_rows: list[tuple[str, dict[str, Any]]] = []
        for reference in references:
            normalized = str(reference or "").strip()
            allowed_deictic = normalized.casefold() in {
                "that", "this", "it", "that email", "this email"
            }
            if not allowed_deictic and not re.fullmatch(
                r"e\d{1,2}",
                normalized,
                flags=re.IGNORECASE,
            ):
                return {
                    "status": "needs_clarification",
                    "message": "Spam moves accept only current E-references or singular that email.",
                    "question": "Which current E-reference is spam?",
                    "missing_fields": ["email_reference"],
                }
            resolved = self._storage.resolve_reference(
                user_id=user_id,
                discord_channel_id=channel_id,
                reference=normalized,
                now=now,
            )
            if resolved is None or not str(resolved.get("gmail_message_id") or "").strip():
                return {
                    "status": "needs_clarification",
                    "message": "I could not resolve every spam reference in this channel.",
                    "question": "Please list the emails again, then name the E-reference that is spam.",
                    "missing_fields": ["email_reference"],
                }
            resolved_rows.append((str(resolved.get("reference") or normalized), resolved))

        operations: list[dict[str, Any]] = []
        for reference, resolved in resolved_rows:
            message_id = str(resolved["gmail_message_id"])
            operation = self._storage.enqueue_spam_operation(
                gmail_message_id=message_id,
                taxonomy_version=self._permissions.taxonomy_version,
                requested_by_user_id=user_id,
                discord_channel_id=channel_id,
                external_request_id=external_request_id,
                idempotency_key=f"email-spam:v1:{external_request_id}:{message_id}",
                max_attempts=self.config.max_provider_attempts,
                now=now,
            )
            operations.append(
                {
                    "reference": reference,
                    "operation_id": operation.get("operation_id"),
                    "gmail_message_id": message_id,
                    "status": operation.get("status"),
                }
            )
        verified = all(item.get("status") == "verified" for item in operations)
        labels = ", ".join(str(item["reference"]) for item in operations)
        return {
            "status": "ok" if verified else "queued",
            "message": (
                f"{labels} {'is' if len(operations) == 1 else 'are'} already verified in Gmail Spam."
                if verified
                else (
                    f"I queued {labels} for a verified move to Gmail Spam. "
                    "Only the isolated worker can apply it; no automatic spam rule was created."
                )
            ),
            "operation_id": operations[0].get("operation_id") if len(operations) == 1 else None,
            "operation_ids": [item.get("operation_id") for item in operations],
            "gmail_message_id": operations[0].get("gmail_message_id") if len(operations) == 1 else None,
            "spam_operations": operations,
            "provider_write": "verified" if verified else "queued",
        }

    def _mark_complete(
        self,
        *,
        entities: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        query = str(entities.get("query") or "").strip()
        if not self._is_explicit_complete_request(query):
            return {
                "status": "needs_clarification",
                "message": "I did not queue a Gmail change because completion requires explicit wording.",
                "question": "Name the current reference, for example: mark E2 as read and complete.",
                "missing_fields": ["email_reference"],
            }
        if not self.config.spam_writes_enabled:
            return {
                "status": "capability_gate",
                "message": "Manual Gmail read-state changes are disabled, so no mailbox change was queued.",
            }
        external_request_id = str(
            context.get("external_message_id") or context.get("request_id") or ""
        ).strip()
        if not external_request_id.startswith("discord:"):
            return {
                "status": "policy_denied",
                "message": "Read-and-complete changes require an immutable Discord message request ID.",
            }
        resolved_rows = self._resolve_action_targets(
            entities=entities,
            context=context,
            allow_all_current=True,
            max_count=self.config.spam_max_operations_per_command,
        )
        if not resolved_rows:
            return {
                "status": "needs_clarification",
                "message": "I could not resolve every completion reference in this channel.",
                "question": "Please list the emails again, then name up to five E-references or say all of those.",
                "missing_fields": ["email_reference"],
            }
        user_id, channel_id = self._scope(context)
        now = _iso(_utc_now())
        operations: list[dict[str, Any]] = []
        for reference, resolved in resolved_rows:
            message_id = str(resolved["gmail_message_id"])
            operation = self._storage.enqueue_mailbox_operation(
                operation_type="mark_read_complete",
                gmail_message_id=message_id,
                taxonomy_version=self._permissions.taxonomy_version,
                requested_by_user_id=user_id,
                discord_channel_id=channel_id,
                external_request_id=external_request_id,
                idempotency_key=f"email-complete:v1:{external_request_id}:{message_id}",
                max_attempts=self.config.max_provider_attempts,
                now=now,
            )
            operations.append(
                {
                    "reference": reference,
                    "operation_id": operation.get("operation_id"),
                    "gmail_message_id": message_id,
                    "operation_type": "mark_read_complete",
                    "status": operation.get("status"),
                }
            )
        verified = all(item.get("status") == "verified" for item in operations)
        labels = ", ".join(str(item["reference"]) for item in operations)
        return {
            "status": "ok" if verified else "queued",
            "message": (
                f"{labels} {'is' if len(operations) == 1 else 'are'} already verified read and complete."
                if verified
                else (
                    f"I queued {labels} to be marked read in Gmail and complete in Jarvis. "
                    "They will leave normal summaries only after provider read-back succeeds. "
                    "Their Jarvis categories stay intact; no Gmail topic label was changed."
                )
            ),
            "operation_id": operations[0].get("operation_id") if len(operations) == 1 else None,
            "operation_ids": [item.get("operation_id") for item in operations],
            "gmail_message_id": operations[0].get("gmail_message_id") if len(operations) == 1 else None,
            "gmail_message_ids": [item.get("gmail_message_id") for item in operations],
            "mailbox_operations": operations,
            "provider_write": "verified" if verified else "queued",
        }

    def _resolve_action_targets(
        self,
        *,
        entities: dict[str, Any],
        context: dict[str, Any],
        allow_all_current: bool,
        max_count: int,
    ) -> list[tuple[str, dict[str, Any]]]:
        user_id, channel_id = self._scope(context)
        now = _iso(_utc_now())
        query = str(entities.get("query") or "").casefold()
        all_current = str(entities.get("reference_scope") or "").casefold() == "all_current"
        if allow_all_current and re.search(r"\b(?:all of (?:those|them)|those all|them all)\b", query):
            all_current = True
        if all_current:
            current = self._storage.latest_reference_set(
                user_id=user_id,
                discord_channel_id=channel_id,
                now=now,
            )
            message_ids = list(current.get("ordered_message_ids") or []) if current else []
            thread_ids = list(current.get("ordered_thread_ids") or []) if current else []
            if not message_ids or len(message_ids) > max_count:
                return []
            return [
                (
                    f"E{index}",
                    {
                        "reference": f"E{index}",
                        "gmail_message_id": str(message_id),
                        "gmail_thread_id": str(thread_ids[index - 1]) if index <= len(thread_ids) else None,
                        "reference_set_id": current.get("reference_set_id"),
                    },
                )
                for index, message_id in enumerate(message_ids, start=1)
            ]

        raw_references = entities.get("references")
        if isinstance(raw_references, list):
            references = [str(item).strip() for item in raw_references if str(item).strip()]
        else:
            reference = str(
                entities.get("reference") or entities.get("email_reference") or ""
            ).strip()
            references = [reference] if reference else ["that"]
        references = list(dict.fromkeys(references))
        if len(references) > max_count:
            return []
        resolved_rows: list[tuple[str, dict[str, Any]]] = []
        for reference in references:
            normalized = str(reference or "").strip()
            if normalized.casefold() not in {"that", "this", "it", "that email", "this email"} and not re.fullmatch(
                r"e\d{1,2}", normalized, flags=re.IGNORECASE
            ):
                return []
            resolved = self._storage.resolve_reference(
                user_id=user_id,
                discord_channel_id=channel_id,
                reference=normalized,
                now=now,
            )
            if resolved is None or not str(resolved.get("gmail_message_id") or "").strip():
                return []
            resolved_rows.append((str(resolved.get("reference") or normalized), resolved))
        return resolved_rows

    @staticmethod
    def _is_explicit_positive_spam_request(value: str) -> bool:
        lowered = str(value or "").casefold()
        if re.search(r"\b(?:not|isn['’]?t|aren['’]?t)\s+(?:spam|junk)\b", lowered):
            return False
        return bool(
            re.search(
                r"(?:\b(?:mark|move|send|put|flag|call)\b.*\b(?:spam|junk)\b|"
                r"\b(?:is|are|looks? like|seems? like)\s+(?:spam|junk)\b)",
                lowered,
            )
        )

    @staticmethod
    def _is_explicit_complete_request(value: str) -> bool:
        lowered = str(value or "").casefold()
        if re.search(r"\b(?:do not|don't|not)\b.*\b(?:complete|done|handled|read)\b", lowered):
            return False
        return bool(
            re.search(
                r"(?:\b(?:mark|make|set)\b.*\bread\b.*\b(?:complete|done|handled)\b|"
                r"\b(?:mark|make|set)\b.*\bread\b.*\bmove\b.*\bfolders?\b|"
                r"\b(?:complete|finish)\b.*\b(?:e\d{1,2}|email|this|that|those|them)\b|"
                r"\b(?:e\d{1,2}|this|that)\b.*\b(?:is|as)\s+(?:complete|done|handled)\b)",
                lowered,
            )
        )

    def _status(self) -> dict[str, Any]:
        status = self._storage.status()
        return {
            "status": "ok",
            "message": (
                "Email agent status: "
                f"{status.get('message_count', 0)} indexed, "
                f"{status.get('needs_review_count', 0)} awaiting category review, "
                f"{status.get('mailbox_queued_count', 0)} mailbox change(s) queued, "
                f"last sync {status.get('last_success_at') or 'not run yet'}. "
                + (
                    " Managed Gmail category labels are enabled."
                    if self.config.label_writes_enabled
                    else " General Gmail label writes are disabled."
                )
            ),
            "email_status": {
                "activated": bool(status.get("activation_at")),
                "last_success_at": status.get("last_success_at"),
                "message_count": status.get("message_count", 0),
                "needs_review_count": status.get("needs_review_count", 0),
                "failed_run_count": status.get("failed_run_count", 0),
                "dead_letter_message_count": status.get("dead_letter_message_count", 0),
                "shadow_mode": True,
                "label_writes_enabled": self.config.label_writes_enabled,
                "manual_mailbox_writes_enabled": self.config.spam_writes_enabled,
                "manual_spam_writes_enabled": self.config.spam_writes_enabled,
                "spam_queued_count": status.get("spam_queued_count", 0),
                "spam_dead_letter_count": status.get("spam_dead_letter_count", 0),
                "mailbox_queued_count": status.get("mailbox_queued_count", 0),
                "mailbox_dead_letter_count": status.get("mailbox_dead_letter_count", 0),
                "label_queued_count": status.get("label_queued_count", 0),
                "label_dead_letter_count": status.get("label_dead_letter_count", 0),
                "configured_user_channel_count": sum(1 for item in self._permissions.access_grants if item.enabled),
                "configured_source_route_count": len(self._permissions.source_routes),
            },
        }

    def _resolve_message(
        self,
        *,
        entities: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        user_id, channel_id = self._scope(context)
        raw_reference = str(
            entities.get("reference")
            or entities.get("email_reference")
            or entities.get("message_id")
            or ""
        ).strip()
        resolved = self._storage.resolve_reference(
            user_id=user_id,
            discord_channel_id=channel_id,
            reference=raw_reference,
            now=_iso(_utc_now()),
        )
        if resolved is not None:
            return resolved
        if raw_reference and not re.fullmatch(r"e\d{1,2}", raw_reference, flags=re.IGNORECASE):
            row = self._storage.get_message(
                gmail_message_id=raw_reference,
                taxonomy_version=self._permissions.taxonomy_version,
            )
            if row is not None:
                return {
                    "reference": None,
                    "gmail_message_id": raw_reference,
                    "gmail_thread_id": row.get("gmail_thread_id"),
                }
        return None

    def _query_filters(self, *, query_text: str, entities: dict[str, Any]) -> dict[str, Any]:
        lowered = query_text.casefold()
        source_route = str(entities.get("source_route_key") or "").strip().casefold() or None
        if source_route not in {item.route_key for item in self._permissions.source_routes}:
            source_route = None
            for route in self._permissions.source_routes:
                aliases = {
                    route.route_key.replace("_", " "),
                    route.source_mailbox.casefold(),
                    route.source_mailbox.split("@", 1)[0].casefold(),
                }
                if any(alias and alias in lowered for alias in aliases):
                    source_route = route.route_key
                    break
        category = self._resolve_category(str(entities.get("category_key") or entities.get("category") or ""))
        if category is None:
            category = self._resolve_category(lowered)
        now = _utc_now()
        since_internal_date = None
        if re.search(r"\btoday\b", lowered):
            local_now = now.astimezone(ZoneInfo(self.config.timezone_name))
            local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            since_internal_date = int(local_start.astimezone(timezone.utc).timestamp() * 1000)
        elif re.search(r"\b(?:this|past|last)\s+week\b|\bweekly\b", lowered):
            since_internal_date = int((now - timedelta(days=7)).timestamp() * 1000)
        explicit_search = str(entities.get("search_text") or "").strip()
        if not explicit_search:
            match = re.search(r"\bfrom\s+([\w.+@'-]+(?:\s+[\w.+@'-]+){0,3})", query_text, re.I)
            if match:
                explicit_search = match.group(1).strip()
        visibility = str(entities.get("visibility") or "").strip().casefold()
        if visibility not in {"active", "unseen", "needs_reply", "completed", "spam", "all"}:
            if re.search(r"\b(?:needs?\s+(?:a\s+)?reply|reply needed|reply queue)\b", lowered):
                visibility = "needs_reply"
            elif re.search(r"\b(?:completed|handled|done)\s+(?:emails?|mail)\b", lowered):
                visibility = "completed"
            elif re.search(r"\b(?:spam|junk)\s+(?:emails?|mail)\b", lowered):
                visibility = "spam"
            elif re.search(r"\b(?:include|show)\b.*\b(?:handled|completed|dismissed)\b", lowered):
                visibility = "all"
            elif re.search(r"\b(?:new|unseen)\b", lowered):
                visibility = "unseen"
            else:
                visibility = "active"
        return {
            "source_route_key": source_route,
            "category_key": category,
            "since_internal_date": since_internal_date,
            "search_text": explicit_search or None,
            "visibility": visibility,
        }

    def _resolve_category(self, value: str) -> str | None:
        normalized = re.sub(r"[^a-z0-9\s_]+", " ", str(value or "").casefold())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        for category in self._permissions.categories:
            candidates = {category.key, category.key.replace("_", " "), category.display_name.casefold()}
            if normalized in candidates or any(re.search(rf"\b{re.escape(item)}\b", normalized) for item in candidates):
                return category.key
        return None

    @staticmethod
    def _looks_like_collection_query(value: str) -> bool:
        return bool(
            re.search(
                r"\b(?:emails?|anything|everything|all|today|recent|latest|new|unseen|ones?|"
                r"addresses?|accounts?|inboxes?)\b",
                str(value or "").casefold(),
            )
        )

    def _format_row(self, row: dict[str, Any], *, index: int) -> str:
        local_time = self._format_internal_date(row.get("internal_date"))
        sender = str(row.get("sender_name") or row.get("sender_email") or "unknown sender")
        summary = str(row.get("summary_text") or row.get("snippet") or "No preview available.").strip()
        summary = re.sub(r"\s+", " ", summary)[:1200]
        structured = row.get("structured_summary") if isinstance(row.get("structured_summary"), dict) else {}
        why = str(structured.get("why_it_matters") or "").strip()[:500]
        deadlines = structured.get("explicit_deadlines") if isinstance(structured, dict) else []
        deadline = str(deadlines[0]).strip()[:300] if isinstance(deadlines, list) and deadlines else "none found"
        actions = structured.get("action_candidates") if isinstance(structured, dict) else []
        action = str(actions[0]).strip()[:300] if isinstance(actions, list) and actions else "none identified"
        category = str(row.get("logical_category_key") or "needs_review").replace("_", " ").title()
        review_suffix = " (review suggested)" if row.get("review_required") else ""
        attachments = row.get("attachment_metadata")
        attachment_names = []
        if isinstance(attachments, list):
            attachment_names = [
                str(item.get("filename") or "attachment")[:100]
                for item in attachments[:5]
                if isinstance(item, dict)
            ]
        lines = [
            f"E{index} - {str(row.get('subject') or '(no subject)')[:500]}",
            f"From: {sender[:300]} | Received: {local_time} | Via: {row.get('source_route_key')}",
            f"Category proposal: {category}{review_suffix}",
            f"Summary: {summary}",
        ]
        if why:
            lines.append(f"Why it matters: {why}")
        lines.extend(
            [
                f"Explicit deadline: {deadline}",
                f"Possible next step: {action} (not executed)",
                f"Attachments: {', '.join(attachment_names) if attachment_names else 'none'}",
            ]
        )
        return "\n".join(lines)

    def _format_digest(self, rows: list[dict[str, Any]]) -> str:
        """Format a collection as an inbox/category outline while preserving E references."""

        numbered = list(enumerate(rows, start=1))
        grouped: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
        for index, row in numbered:
            route_key = str(row.get("source_route_key") or "unknown")
            category_key = str(row.get("logical_category_key") or "needs_review")
            grouped.setdefault((route_key, category_key), []).append((index, row))

        category_names = {item.key: item.display_name for item in self._permissions.categories}
        source_names = {item.route_key: item.source_mailbox for item in self._permissions.source_routes}
        lines = [f"Inbox summary ({len(rows)} email{'s' if len(rows) != 1 else ''}):"]
        for (route_key, category_key), items in grouped.items():
            inbox = source_names.get(route_key, route_key.replace("_", " ").title())
            category = category_names.get(category_key, category_key.replace("_", " ").title())
            review = " · review suggested" if any(row.get("review_required") for _, row in items) else ""
            lines.append(f"- {inbox} — {category} ({len(items)}){review}")
            for index, row in items:
                subject = re.sub(r"\s+", " ", str(row.get("subject") or "(no subject)")).strip()[:300]
                summary = re.sub(
                    r"\s+",
                    " ",
                    str(row.get("summary_text") or row.get("snippet") or "No preview available."),
                ).strip()[:700]
                structured = row.get("structured_summary")
                questions = structured.get("questions") if isinstance(structured, dict) else []
                question_signal = "?" in " ".join(
                    (
                        str(row.get("subject") or ""),
                        str(row.get("snippet") or ""),
                        summary,
                    )
                )
                actual_questions = [
                    re.sub(r"\s+", " ", str(item)).strip()[:300]
                    for item in questions[:2]
                    if str(item).strip()
                ] if question_signal and isinstance(questions, list) else []
                lines.append(f"  - E{index}: {subject} — {summary}")
                if str(row.get("user_disposition") or "").casefold() == "needs_reply":
                    lines.append("    Status: Needs reply")
                if actual_questions:
                    lines.append(f"    Question: {'; '.join(actual_questions)}")
        return "\n".join(lines)

    def _context_entities(
        self,
        rows: list[dict[str, Any]],
        reference_set: dict[str, Any],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for index, row in enumerate(rows[: self.config.max_interactive_messages], start=1):
            result.append(
                {
                    "domain": "email",
                    "entity_type": "message",
                    "entity_id": str(row.get("gmail_message_id") or ""),
                    "display_name": f"E{index}",
                    "aliases": [f"E{index}", "that email" if index == 1 else f"email {index}"],
                    "salience": max(0.5, 1.0 - ((index - 1) * 0.05)),
                    "resolution_hints": {
                        "gmail_message_id": str(row.get("gmail_message_id") or ""),
                        "gmail_thread_id": str(row.get("gmail_thread_id") or ""),
                        "reference_set_id": reference_set.get("reference_set_id"),
                        "source_route_key": row.get("source_route_key"),
                        "category_key": row.get("logical_category_key"),
                    },
                }
            )
        return result

    def _snooze_until(self, value: str) -> str | None:
        cleaned = str(value or "").strip().casefold()
        now = _utc_now()
        if cleaned in {"tomorrow", "until tomorrow"}:
            return _iso(now + timedelta(days=1))
        match = re.search(r"\b(\d{1,3})\s*(hour|hours|day|days)\b", cleaned)
        if match:
            count = max(1, min(int(match.group(1)), 365))
            delta = timedelta(hours=count) if match.group(2).startswith("hour") else timedelta(days=count)
            return _iso(now + delta)
        parsed = self._parse_iso(value)
        return _iso(parsed) if parsed is not None and parsed > now else None

    def _fail_run(
        self,
        *,
        run_id: str,
        bucket_key: str,
        counts: dict[str, int],
        now: datetime,
        exc: Exception,
    ) -> dict[str, Any]:
        failure = self._storage.fail_sync_run(
            run_id=run_id,
            error_code=type(exc).__name__,
            now=_iso(now),
            max_attempts=self.config.max_provider_attempts,
        )
        result = {
            "status": "error",
            "run_id": run_id,
            "bucket_key": bucket_key,
            "error_type": type(exc).__name__,
            "run_status": failure.get("status"),
            **counts,
        }
        self._record("email.sync.failed", result)
        return result

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_log is None:
            return
        self._event_log.record(event_type=event_type, session_id="system:email-agent", payload=payload)

    def _format_internal_date(self, value: Any) -> str:
        try:
            parsed = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return "unknown"
        return parsed.astimezone(ZoneInfo(self.config.timezone_name)).strftime("%Y-%m-%d %I:%M %p")

    @staticmethod
    def _scope(context: dict[str, Any]) -> tuple[str, str]:
        return (
            str(context.get("requested_by_user_id") or "").strip().casefold(),
            str(context.get("discord_channel_id") or "").strip(),
        )

    @staticmethod
    def _parse_iso(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _normalize_now(value: datetime | None) -> datetime:
        current = value or _utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    @staticmethod
    def _opaque_id(value: str) -> str:
        import hashlib

        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _reference_number(value: str) -> int:
    match = re.fullmatch(r"e(\d{1,2})", str(value or "").strip(), flags=re.IGNORECASE)
    return int(match.group(1)) if match else 1
