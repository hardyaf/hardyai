from __future__ import annotations

from app.skills.domains.calendar.receipts import build_operation_receipt
from app.tickets.types import ReviewVerdict
from app.tickets.verifiers.calendar import GoogleCalendarSourceVerifier


class FakeCalendarService:
    def source_event_by_id(self, *, calendar_id: str, event_id: str):
        return {
            "status": "ok",
            "event": {
                "google_event_id": event_id,
                "host_calendar_id": calendar_id,
                "google_event_etag": "etag-1",
                "title": "Dentist",
                "start_at": "2026-08-17T10:00:00-04:00",
                "end_at": "2026-08-17T11:00:00-04:00",
                "attendee_emails": ["parent@example.com"],
            },
        }


def test_google_calendar_receipt_is_verified_from_provider_readback():
    result = {
        "status": "ok",
        "source": "google_live",
        "event": {
            "event_title": "Dentist",
            "start_at": "2026-08-17T10:00:00-04:00",
            "end_at": "2026-08-17T11:00:00-04:00",
            "google_event_id": "event-1",
            "google_event_etag": "etag-1",
            "host_calendar_id": "house@example.com",
            "attendee_emails": ["parent@example.com"],
        },
    }
    receipt = build_operation_receipt(
        intent="calendar.add_event",
        entities={"event_title": "Dentist"},
        context={"request_id": "calendar-request"},
        result=result,
        services={},
    )
    assert receipt is not None
    observation = GoogleCalendarSourceVerifier(calendar_service=FakeCalendarService()).observe(
        resource_locator=receipt["resource_locator"],
        expected_state=receipt["expected_effect"],
        operation_receipt=receipt,
    )
    assert observation.deterministic_verdict is ReviewVerdict.CORRECT


def test_local_calendar_does_not_fabricate_a_verifiable_receipt():
    receipt = build_operation_receipt(
        intent="calendar.add_event",
        entities={},
        context={"request_id": "local"},
        result={"status": "ok", "source": "local_stub", "event": {"event_title": "Test"}},
        services={},
    )
    assert receipt is None


def test_google_calendar_delete_receipt_is_correct_when_provider_reports_not_found():
    class MissingCalendarService:
        def source_event_by_id(self, *, calendar_id: str, event_id: str):
            return {"status": "error", "error_code": "not_found"}

    result = {
        "status": "ok",
        "source": "google_live",
        "deleted": True,
        "event": {
            "event_title": "Temporary event",
            "google_event_id": "event-delete",
            "google_event_etag": "etag-before-delete",
            "host_calendar_id": "house@example.com",
        },
    }
    receipt = build_operation_receipt(
        intent="calendar.delete_event",
        entities={"event_reference": "Temporary event"},
        context={"request_id": "delete-request"},
        result=result,
        services={},
    )

    assert receipt is not None
    assert receipt["action"] == "delete_event"
    assert receipt["expected_effect"] == {"exists": False}
    observation = GoogleCalendarSourceVerifier(calendar_service=MissingCalendarService()).observe(
        resource_locator=receipt["resource_locator"],
        expected_state=receipt["expected_effect"],
        operation_receipt=receipt,
    )
    assert observation.deterministic_verdict is ReviewVerdict.CORRECT
