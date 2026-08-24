from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.services.event_log import EventLogService
from app.services.google.calendar_inbox import InboxMessageRef
from app.skills.domains.calendar_inbox.storage import CalendarInboxSQLiteStorage


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class InboxSession(Protocol):
    house_calendar_id: str

    def list_candidate_messages(self) -> list[InboxMessageRef]: ...

    def reconcile_message(self, message_id: str) -> dict[str, Any]: ...


class InboxProvider(Protocol):
    def open_session(
        self,
        *,
        allowed_sender_emails: list[str],
        activation_epoch: int,
        lookback_days: int,
        max_messages: int,
        default_timezone: str,
    ) -> InboxSession: ...


@dataclass(frozen=True)
class CalendarInboxConfig:
    timezone_name: str = "America/New_York"
    start_hour: int = 8
    end_hour: int = 20
    max_messages_per_run: int = 100
    lookback_days: int = 30
    allowed_sender_emails: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown calendar inbox timezone: {self.timezone_name}") from exc
        if not 0 <= int(self.start_hour) <= 23 or not 0 <= int(self.end_hour) <= 23:
            raise ValueError("Calendar inbox start/end hours must be between 0 and 23.")
        if int(self.start_hour) > int(self.end_hour):
            raise ValueError("Calendar inbox start hour must be before or equal to end hour.")
        if not 1 <= int(self.max_messages_per_run) <= 200:
            raise ValueError("Calendar inbox max_messages_per_run must be between 1 and 200.")
        if not 1 <= int(self.lookback_days) <= 90:
            raise ValueError("Calendar inbox lookback_days must be between 1 and 90.")


class CalendarInboxService:
    SKILL_ID = "skill.calendar.inbox"
    MAX_RUN_ATTEMPTS = 3
    MAX_MESSAGE_ATTEMPTS = 3
    LEASE_MINUTES = 15

    def __init__(
        self,
        *,
        storage: CalendarInboxSQLiteStorage,
        provider: InboxProvider,
        config: CalendarInboxConfig,
        event_log: EventLogService | None = None,
    ) -> None:
        self._storage = storage
        self._provider = provider
        self.config = config
        self._event_log = event_log
        activated_at = self._storage.get_or_create_activation_time(now=_iso(_utc_now()))
        try:
            self._activation_time = datetime.fromisoformat(activated_at)
        except ValueError:
            self._activation_time = _utc_now()
        if self._activation_time.tzinfo is None:
            self._activation_time = self._activation_time.replace(tzinfo=timezone.utc)

    def run_due(self, *, now: datetime | None = None) -> dict[str, Any] | None:
        current = now or _utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        local_now = current.astimezone(ZoneInfo(self.config.timezone_name))
        if not self.config.start_hour <= local_now.hour <= self.config.end_hour:
            return None

        slot_key = f"{local_now.date().isoformat()}T{local_now.hour:02d}@{self.config.timezone_name}"
        claimed = self._storage.claim_run(
            slot_key=slot_key,
            now=_iso(current),
            stale_before=_iso(current - timedelta(minutes=self.LEASE_MINUTES)),
            max_attempts=self.MAX_RUN_ATTEMPTS,
        )
        if not claimed.get("claimed"):
            return None
        run_id = str(claimed.get("run_id") or "")
        self._record(
            "calendar_inbox.run.started",
            {"run_id": run_id, "slot_key": slot_key, "attempt_count": claimed.get("attempt_count")},
        )

        try:
            session = self._provider.open_session(
                allowed_sender_emails=list(self.config.allowed_sender_emails),
                activation_epoch=max(0, int(self._activation_time.timestamp())),
                lookback_days=self.config.lookback_days,
                max_messages=self.config.max_messages_per_run,
                default_timezone=self.config.timezone_name,
            )
            refs = session.list_candidate_messages()[: self.config.max_messages_per_run]
            result: dict[str, Any] = {
                "status": "ok",
                "run_id": run_id,
                "slot_key": slot_key,
                "scanned_count": len(refs),
                "imported_count": 0,
                "updated_count": 0,
                "existing_count": 0,
                "ignored_count": 0,
                "failed_count": 0,
            }
            for ref in refs:
                self._process_message(
                    session=session,
                    ref=ref,
                    run_id=run_id,
                    current=current,
                    result=result,
                )
            self._storage.finish_run(run_id=run_id, result=result, now=_iso(current))
            self._record("calendar_inbox.run.completed", dict(result))
            return result
        except Exception as exc:
            failure = self._storage.fail_run(
                run_id=run_id,
                error=f"{type(exc).__name__}: {exc}",
                now=_iso(current),
                max_attempts=self.MAX_RUN_ATTEMPTS,
            )
            result = {
                "status": "error",
                "run_id": run_id,
                "slot_key": slot_key,
                "error_type": type(exc).__name__,
                "run_status": failure.get("status"),
                "attempt_count": failure.get("attempt_count"),
            }
            self._record("calendar_inbox.run.failed", result)
            return result

    def _process_message(
        self,
        *,
        session: InboxSession,
        ref: InboxMessageRef,
        run_id: str,
        current: datetime,
        result: dict[str, Any],
    ) -> None:
        claimed = self._storage.claim_message(
            gmail_message_id=ref.message_id,
            gmail_thread_id=ref.thread_id,
            gmail_internal_date=None,
            run_id=run_id,
            now=_iso(current),
            stale_before=_iso(current - timedelta(minutes=self.LEASE_MINUTES)),
            max_attempts=self.MAX_MESSAGE_ATTEMPTS,
        )
        if not claimed.get("claimed"):
            return
        try:
            outcome = session.reconcile_message(ref.message_id)
            ignored = str(outcome.get("status") or "").casefold() == "ignored"
            for event in outcome.get("events") or []:
                if not isinstance(event, dict):
                    continue
                action = str(event.get("action") or "ignored")
                self._storage.record_event(
                    source_key=str(event.get("source_key") or ""),
                    gmail_message_id=ref.message_id,
                    ical_uid=str(event.get("ical_uid") or ""),
                    recurrence_id=str(event.get("recurrence_id") or "").strip() or None,
                    house_calendar_id=str(event.get("house_calendar_id") or session.house_calendar_id),
                    google_event_id=str(event.get("google_event_id") or "").strip() or None,
                    action=action,
                    payload_hash=str(event.get("payload_hash") or "").strip() or None,
                    result=event,
                    now=_iso(current),
                )
                if action == "imported":
                    result["imported_count"] += 1
                elif action in {"updated", "cancelled"}:
                    result["updated_count"] += 1
                elif action == "existing_on_house":
                    result["existing_count"] += 1
                else:
                    result["ignored_count"] += 1
            if ignored:
                result["ignored_count"] += 1
            self._storage.finish_message(
                gmail_message_id=ref.message_id,
                outcome=outcome,
                ignored=ignored,
                now=_iso(current),
            )
        except Exception as exc:
            result["failed_count"] += 1
            failure = self._storage.fail_message(
                gmail_message_id=ref.message_id,
                error=f"{type(exc).__name__}: {exc}",
                now=_iso(current),
                max_attempts=self.MAX_MESSAGE_ATTEMPTS,
            )
            self._record(
                "calendar_inbox.message.failed",
                {
                    "run_id": run_id,
                    "gmail_message_id": ref.message_id,
                    "status": failure.get("status"),
                    "attempt_count": failure.get("attempt_count"),
                    "error_type": type(exc).__name__,
                },
            )

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_log is None:
            return
        self._event_log.record(
            event_type=event_type,
            session_id="system:calendar_inbox",
            payload={"skill_id": self.SKILL_ID, **payload},
        )
