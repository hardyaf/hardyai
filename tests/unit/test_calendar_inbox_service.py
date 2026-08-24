from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.services.clock_scheduler import BoundedClockScheduler, ClockJob
from app.services.google.calendar_inbox import InboxMessageRef
from app.skills.domains.calendar_inbox.service import CalendarInboxConfig, CalendarInboxService
from app.skills.domains.calendar_inbox.storage import CalendarInboxSQLiteStorage


class FakeSession:
    house_calendar_id = "jarvis.house@example.com"

    def __init__(self) -> None:
        self.message_calls: list[str] = []

    def list_candidate_messages(self):
        return [
            InboxMessageRef(message_id="gmail-1", thread_id="thread-1"),
            InboxMessageRef(message_id="gmail-2", thread_id="thread-2"),
        ]

    def reconcile_message(self, message_id: str):
        self.message_calls.append(message_id)
        if message_id == "gmail-2":
            return {"status": "ignored", "reason": "no_calendar_payload", "events": []}
        return {
            "status": "ok",
            "events": [
                {
                    "source_key": "source-1",
                    "ical_uid": "uid-1@example.com",
                    "recurrence_id": None,
                    "house_calendar_id": self.house_calendar_id,
                    "google_event_id": "google-1",
                    "action": "imported",
                    "payload_hash": "hash-1",
                }
            ],
        }


class FakeProvider:
    def __init__(self, session: FakeSession | None = None, error: Exception | None = None) -> None:
        self.session = session or FakeSession()
        self.error = error
        self.open_calls: list[dict[str, object]] = []

    def open_session(self, **kwargs):
        self.open_calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return self.session


def _config() -> CalendarInboxConfig:
    return CalendarInboxConfig(
        timezone_name="America/New_York",
        start_hour=8,
        end_hour=20,
        max_messages_per_run=100,
        lookback_days=30,
        allowed_sender_emails=("personal.sender@example.com", "second.person@example.com"),
    )


def test_hourly_calendar_inbox_runs_once_per_inclusive_local_slot(tmp_path):
    storage = CalendarInboxSQLiteStorage(str(tmp_path / "calendar-inbox.db"))
    provider = FakeProvider()
    service = CalendarInboxService(storage=storage, provider=provider, config=_config())
    try:
        assert service.run_due(
            now=datetime(2026, 8, 16, 7, 59, tzinfo=timezone.utc)
        ) is None  # 03:59 Eastern

        first = service.run_due(
            now=datetime(2026, 8, 16, 12, 13, tzinfo=timezone.utc)
        )  # 08:13 Eastern
        assert first is not None
        assert first["status"] == "ok"
        assert first["scanned_count"] == 2
        assert first["imported_count"] == 1
        assert first["ignored_count"] == 1
        assert provider.session.message_calls == ["gmail-1", "gmail-2"]
        assert provider.open_calls[0]["allowed_sender_emails"] == [
            "personal.sender@example.com",
            "second.person@example.com",
        ]

        assert service.run_due(
            now=datetime(2026, 8, 16, 12, 59, tzinfo=timezone.utc)
        ) is None
        assert service.run_due(
            now=datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc)
        ) is None  # 21:00 Eastern

        run = storage.get_run(slot_key="2026-08-16T08@America/New_York")
        assert run is not None
        assert run["status"] == "completed"
        assert run["attempt_count"] == 1
        assert storage.get_message(gmail_message_id="gmail-1")["status"] == "completed"
        assert storage.get_message(gmail_message_id="gmail-2")["status"] == "ignored"
    finally:
        storage.close()


def test_calendar_inbox_provider_failures_are_bounded_per_slot(tmp_path):
    storage = CalendarInboxSQLiteStorage(str(tmp_path / "calendar-inbox-failure.db"))
    provider = FakeProvider(error=RuntimeError("OAuth scope unavailable"))
    service = CalendarInboxService(storage=storage, provider=provider, config=_config())
    due = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    try:
        results = [service.run_due(now=due) for _ in range(service.MAX_RUN_ATTEMPTS + 1)]
        assert [item["status"] for item in results[:3]] == ["error", "error", "error"]
        assert results[2]["run_status"] == "dead_letter"
        assert results[3] is None
        run = storage.get_run(slot_key="2026-08-16T08@America/New_York")
        assert run is not None
        assert run["status"] == "dead_letter"
        assert run["attempt_count"] == 3
    finally:
        storage.close()


def test_bounded_clock_scheduler_passes_one_shared_timestamp():
    observed: list[datetime] = []

    def callback(*, now: datetime):
        observed.append(now)
        return {"status": "ok"}

    scheduler = BoundedClockScheduler(
        jobs=[ClockJob(name="calendar_inbox.reconcile", callback=callback)],
        poll_seconds=60,
    )
    current = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    results = asyncio.run(scheduler.run_once(now=current))

    assert observed == [current]
    assert results == [
        {
            "job_name": "calendar_inbox.reconcile",
            "status": "ok",
            "result": {"status": "ok"},
        }
    ]


def test_calendar_inbox_config_rejects_overnight_or_unbounded_values():
    for kwargs in (
        {"start_hour": 21, "end_hour": 8},
        {"max_messages_per_run": 201},
        {"lookback_days": 0},
        {"timezone_name": "Not/AZone"},
    ):
        try:
            CalendarInboxConfig(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid config to fail: {kwargs!r}")
