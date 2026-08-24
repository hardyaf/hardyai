from __future__ import annotations

import base64
from email.message import EmailMessage

from app.services.google.calendar_inbox import GoogleCalendarInboxSession


def _encoded(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _ics(*, method: str = "REQUEST", status: str = "CONFIRMED", recurrence_id: str = "") -> bytes:
    recurrence_line = f"RECURRENCE-ID:20260821T220000Z\r\n" if recurrence_id else ""
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        f"METHOD:{method}\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:party-123@example.com\r\n"
        "DTSTAMP:20260816T120000Z\r\n"
        f"{recurrence_line}"
        "DTSTART:20260821T220000Z\r\n"
        "DTEND:20260821T230000Z\r\n"
        "SUMMARY:ICDP Party\r\n"
        "DESCRIPTION:Bring the layout notes.\r\n"
        "ORGANIZER:mailto:personal.sender@example.com\r\n"
        f"STATUS:{status}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    ).encode("utf-8")


class Execute:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value() if callable(self.value) else self.value


class FakeGmail:
    def __init__(self, messages: dict[str, dict]):
        self._messages = messages
        self.list_queries: list[str] = []

    def users(self):
        return self

    def messages(self):
        return self

    def attachments(self):
        return self

    def list(self, **kwargs):
        self.list_queries.append(str(kwargs.get("q") or ""))
        rows = [{"id": key, "threadId": f"thread-{key}"} for key in self._messages]
        return Execute({"messages": rows[: int(kwargs.get("maxResults") or 100)]})

    def get(self, **kwargs):
        if "messageId" in kwargs:
            raise AssertionError("attachment fetch was not expected")
        return Execute(self._messages[str(kwargs["id"])])


class FakeCalendarEvents:
    def __init__(self):
        self.rows: list[dict] = []
        self.import_calls: list[dict] = []
        self.patch_calls: list[dict] = []
        self.delete_calls: list[dict] = []

    def list(self, **kwargs):
        uid = str(kwargs.get("iCalUID") or "")
        return Execute({"items": [row for row in self.rows if row.get("iCalUID") == uid]})

    def import_(self, **kwargs):
        body = dict(kwargs["body"])

        def execute():
            row = {**body, "id": f"imported-{len(self.import_calls) + 1}", "status": "confirmed"}
            self.rows.append(row)
            self.import_calls.append(kwargs)
            return row

        return Execute(execute)

    def patch(self, **kwargs):
        def execute():
            target = next(row for row in self.rows if row.get("id") == kwargs["eventId"])
            target.update(dict(kwargs["body"]))
            self.patch_calls.append(kwargs)
            return target

        return Execute(execute)

    def delete(self, **kwargs):
        def execute():
            target = next(row for row in self.rows if row.get("id") == kwargs["eventId"])
            target["status"] = "cancelled"
            self.delete_calls.append(kwargs)
            return {}

        return Execute(execute)


class FakeCalendar:
    def __init__(self):
        self.event_api = FakeCalendarEvents()

    def events(self):
        return self.event_api


def _message(raw_ics: bytes, *, sender: str = "Jordan <personal.sender@example.com>") -> dict:
    return {
        "internalDate": "1786896000000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "From", "value": sender}],
            "parts": [
                {
                    "mimeType": "text/calendar",
                    "filename": "invite.ics",
                    "body": {"data": _encoded(raw_ics)},
                }
            ],
        },
    }


def _session(gmail: FakeGmail, calendar: FakeCalendar) -> GoogleCalendarInboxSession:
    return GoogleCalendarInboxSession(
        gmail_service=gmail,
        calendar_service=calendar,
        house_calendar_id="jarvis.house@example.com",
        allowed_sender_emails={"personal.sender@example.com", "second.person@example.com"},
        gmail_query="after:1786896000 newer_than:30d",
        max_messages=100,
        default_timezone="America/New_York",
    )


def test_forwarded_ics_imports_once_then_retry_updates_managed_copy_without_notifications():
    gmail = FakeGmail({"message-1": _message(_ics())})
    calendar = FakeCalendar()
    session = _session(gmail, calendar)

    refs = session.list_candidate_messages()
    first = session.reconcile_message(refs[0].message_id)
    second = session.reconcile_message(refs[0].message_id)

    assert gmail.list_queries == ["after:1786896000 newer_than:30d"]
    assert first["status"] == "ok"
    assert first["events"][0]["action"] == "imported"
    assert second["events"][0]["action"] == "updated"
    assert len(calendar.event_api.import_calls) == 1
    assert len(calendar.event_api.patch_calls) == 1
    imported_body = calendar.event_api.import_calls[0]["body"]
    assert imported_body["iCalUID"] == "party-123@example.com"
    assert imported_body["extendedProperties"]["private"]["jarvis_calendar_inbox"] == "true"
    assert calendar.event_api.patch_calls[0]["sendUpdates"] == "none"


def test_existing_unmanaged_invitation_is_left_untouched_and_unallowed_sender_is_ignored():
    gmail = FakeGmail(
        {
            "allowed": _message(_ics()),
            "unallowed": _message(_ics(), sender="Stranger <stranger@example.com>"),
        }
    )
    calendar = FakeCalendar()
    calendar.event_api.rows.append(
        {
            "id": "google-native-invite",
            "iCalUID": "party-123@example.com",
            "status": "confirmed",
        }
    )
    session = _session(gmail, calendar)

    existing = session.reconcile_message("allowed")
    ignored = session.reconcile_message("unallowed")

    assert existing["events"][0]["action"] == "existing_on_house"
    assert ignored == {
        "status": "ignored",
        "reason": "sender_not_allowed",
        "internal_date": "1786896000000",
        "events": [],
    }
    assert calendar.event_api.import_calls == []
    assert calendar.event_api.patch_calls == []
    assert calendar.event_api.delete_calls == []


def test_google_calendar_notification_may_use_allowlisted_ics_organizer():
    gmail = FakeGmail(
        {
            "notification": _message(
                _ics(),
                sender="Google Calendar <calendar-notification@google.com>",
            )
        }
    )
    calendar = FakeCalendar()
    session = _session(gmail, calendar)

    result = session.reconcile_message("notification")

    assert result["status"] == "ok"
    assert result["events"][0]["action"] == "imported"


def test_forwarded_eml_attachment_finds_nested_calendar_payload():
    forwarded = EmailMessage()
    forwarded["From"] = "Original Organizer <organizer@example.com>"
    forwarded["To"] = "Jordan <personal.sender@example.com>"
    forwarded.set_content("Calendar details are attached.")
    forwarded.add_attachment(
        _ics(),
        maintype="text",
        subtype="calendar",
        filename="event.ics",
    )
    gmail = FakeGmail(
        {
            "forwarded": {
                "internalDate": "1786896000000",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "headers": [{"name": "From", "value": "Jordan <personal.sender@example.com>"}],
                    "parts": [
                        {
                            "mimeType": "message/rfc822",
                            "filename": "forwarded.eml",
                            "body": {"data": _encoded(forwarded.as_bytes())},
                        }
                    ],
                },
            }
        }
    )
    calendar = FakeCalendar()
    session = _session(gmail, calendar)

    result = session.reconcile_message("forwarded")

    assert result["status"] == "ok"
    assert result["events"][0]["action"] == "imported"


def test_cancel_deletes_only_managed_copy_and_recurrence_exception_is_ignored():
    gmail = FakeGmail(
        {
            "create": _message(_ics()),
            "cancel": _message(_ics(method="CANCEL", status="CANCELLED")),
            "exception": _message(_ics(recurrence_id="yes")),
        }
    )
    calendar = FakeCalendar()
    session = _session(gmail, calendar)

    created = session.reconcile_message("create")
    cancelled = session.reconcile_message("cancel")
    exception = session.reconcile_message("exception")

    assert created["events"][0]["action"] == "imported"
    assert cancelled["events"][0]["action"] == "cancelled"
    assert len(calendar.event_api.delete_calls) == 1
    assert calendar.event_api.delete_calls[0]["sendUpdates"] == "none"
    assert exception["events"][0]["action"] == "ignored_recurrence_exception"
