from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from typing import Any
from zoneinfo import ZoneInfo

from app.services.google.calendar_live import GoogleCalendarLiveService
from app.services.google.gmail_gateway import (
    GMAIL_READONLY_SCOPE,
    build_gmail_service,
    load_google_credentials,
)


MANAGED_MARKER_KEY = "jarvis_calendar_inbox"
MANAGED_MARKER_VALUE = "true"


@dataclass(frozen=True)
class InboxMessageRef:
    message_id: str
    thread_id: str | None


@dataclass(frozen=True)
class ParsedCalendarEvent:
    uid: str
    recurrence_id: str | None
    method: str
    status: str
    organizer_email: str | None
    resource: dict[str, Any]
    payload_hash: str

    @property
    def source_key(self) -> str:
        raw = f"{self.uid}\n{self.recurrence_id or ''}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


class GoogleCalendarInboxProvider:
    """Open a bounded Gmail + Calendar reconciliation session for the house account."""

    def __init__(self, calendar_live: GoogleCalendarLiveService) -> None:
        self._calendar_live = calendar_live

    def open_session(
        self,
        *,
        allowed_sender_emails: list[str],
        activation_epoch: int,
        lookback_days: int,
        max_messages: int,
        default_timezone: str,
    ) -> "GoogleCalendarInboxSession":
        config = self._calendar_live._load_permissions()
        calendar_config = config.get("calendar") or {}
        oauth_config = config.get("oauth") or {}
        bindings = self._calendar_live._calendar_bindings(calendar_config)
        host = self._calendar_live._select_host_binding(bindings, calendar_config)
        if host is None:
            raise RuntimeError("No house/default Google Calendar binding is configured.")

        derived_senders = [
            binding.calendar_id.strip().casefold()
            for binding in bindings
            if binding.calendar_id.strip()
            and binding.calendar_id.strip().casefold() != host.calendar_id.strip().casefold()
            and "@" in binding.calendar_id
        ]
        senders = {
            str(item).strip().casefold()
            for item in (allowed_sender_emails or derived_senders)
            if str(item).strip() and "@" in str(item)
        }
        if not senders:
            raise RuntimeError("Calendar inbox requires at least one allowed sender email.")

        scopes = self._calendar_live._oauth_scopes(oauth_cfg=oauth_config, include_write=True)
        if GMAIL_READONLY_SCOPE not in scopes:
            scopes.append(GMAIL_READONLY_SCOPE)
        credentials = load_google_credentials(
            calendar_live=self._calendar_live,
            account_key=self._calendar_live._resolve_account_key(host, config),
            scopes=scopes,
            allow_interactive=False,
        )
        gmail_service = build_gmail_service(credentials)
        calendar_service = self._calendar_live._build_calendar_service(credentials)
        return GoogleCalendarInboxSession(
            gmail_service=gmail_service,
            calendar_service=calendar_service,
            house_calendar_id=host.calendar_id,
            allowed_sender_emails=senders,
            gmail_query=(
                f"after:{max(0, int(activation_epoch))} "
                f"newer_than:{max(1, min(int(lookback_days), 90))}d"
            ),
            max_messages=max(1, min(int(max_messages), 200)),
            default_timezone=default_timezone,
        )


class GoogleCalendarInboxSession:
    MAX_GMAIL_PAGES = 5
    MAX_CALENDAR_PAYLOADS = 10
    MAX_CALENDAR_PAYLOAD_BYTES = 1_000_000
    MAX_EVENTS_PER_MESSAGE = 20

    def __init__(
        self,
        *,
        gmail_service: Any,
        calendar_service: Any,
        house_calendar_id: str,
        allowed_sender_emails: set[str],
        gmail_query: str,
        max_messages: int,
        default_timezone: str,
    ) -> None:
        self._gmail = gmail_service
        self._calendar = calendar_service
        self.house_calendar_id = str(house_calendar_id)
        self._allowed_sender_emails = {item.casefold() for item in allowed_sender_emails}
        self._gmail_query = str(gmail_query)
        self._max_messages = max(1, min(int(max_messages), 200))
        self._default_timezone = str(default_timezone or "America/New_York")
        ZoneInfo(self._default_timezone)

    def list_candidate_messages(self) -> list[InboxMessageRef]:
        refs: list[InboxMessageRef] = []
        page_token: str | None = None
        for _ in range(self.MAX_GMAIL_PAGES):
            remaining = self._max_messages - len(refs)
            if remaining <= 0:
                break
            response = (
                self._gmail.users()
                .messages()
                .list(
                    userId="me",
                    q=self._gmail_query,
                    maxResults=min(100, remaining),
                    pageToken=page_token,
                )
                .execute()
            )
            for row in response.get("messages") or []:
                if not isinstance(row, dict):
                    continue
                message_id = str(row.get("id") or "").strip()
                if message_id:
                    refs.append(
                        InboxMessageRef(
                            message_id=message_id,
                            thread_id=str(row.get("threadId") or "").strip() or None,
                        )
                    )
                if len(refs) >= self._max_messages:
                    break
            page_token = str(response.get("nextPageToken") or "").strip() or None
            if not page_token:
                break
        return refs

    def reconcile_message(self, message_id: str) -> dict[str, Any]:
        message = (
            self._gmail.users()
            .messages()
            .get(userId="me", id=str(message_id), format="full")
            .execute()
        )
        payload = message.get("payload") if isinstance(message, dict) else None
        if not isinstance(payload, dict):
            return {"status": "ignored", "reason": "missing_mime_payload", "events": []}

        calendar_payloads = self._extract_calendar_payloads(
            message_id=str(message_id),
            root_payload=payload,
        )
        if not calendar_payloads:
            return {
                "status": "ignored",
                "reason": "no_calendar_payload",
                "internal_date": str(message.get("internalDate") or "") or None,
                "events": [],
            }

        parsed_events: list[ParsedCalendarEvent] = []
        for raw in calendar_payloads[: self.MAX_CALENDAR_PAYLOADS]:
            parsed_events.extend(self._parse_calendar_payload(raw)[: self.MAX_EVENTS_PER_MESSAGE])
            if len(parsed_events) >= self.MAX_EVENTS_PER_MESSAGE:
                parsed_events = parsed_events[: self.MAX_EVENTS_PER_MESSAGE]
                break
        if not parsed_events:
            return {
                "status": "ignored",
                "reason": "no_valid_vevent",
                "internal_date": str(message.get("internalDate") or "") or None,
                "events": [],
            }

        header_emails = self._message_header_emails(payload)
        organizer_emails = {
            str(event.organizer_email or "").casefold()
            for event in parsed_events
            if event.organizer_email
        }
        header_allowed = bool(header_emails & self._allowed_sender_emails)
        google_calendar_notification = "calendar-notification@google.com" in header_emails
        organizer_allowed = google_calendar_notification and bool(
            organizer_emails & self._allowed_sender_emails
        )
        if not header_allowed and not organizer_allowed:
            return {
                "status": "ignored",
                "reason": "sender_not_allowed",
                "internal_date": str(message.get("internalDate") or "") or None,
                "events": [],
            }

        results = [
            self._reconcile_event(message_id=str(message_id), event=event)
            for event in parsed_events
        ]
        return {
            "status": "ok",
            "reason": None,
            "internal_date": str(message.get("internalDate") or "") or None,
            "events": results,
        }

    def _reconcile_event(self, *, message_id: str, event: ParsedCalendarEvent) -> dict[str, Any]:
        if event.recurrence_id:
            return self._event_result(event, action="ignored_recurrence_exception", google_event_id=None)

        existing_rows = (
            self._calendar.events()
            .list(
                calendarId=self.house_calendar_id,
                iCalUID=event.uid,
                showDeleted=True,
                maxResults=10,
            )
            .execute()
            .get("items", [])
        )
        existing = next(
            (
                row
                for row in existing_rows
                if isinstance(row, dict) and str(row.get("status") or "confirmed").casefold() != "cancelled"
            ),
            None,
        )
        managed = bool(existing) and (
            str(
                (((existing or {}).get("extendedProperties") or {}).get("private") or {}).get(
                    MANAGED_MARKER_KEY
                )
                or ""
            ).casefold()
            == MANAGED_MARKER_VALUE
        )

        if existing is not None and not managed:
            return self._event_result(
                event,
                action="existing_on_house",
                google_event_id=str(existing.get("id") or "") or None,
            )

        cancelled = event.method == "CANCEL" or event.status == "CANCELLED"
        if cancelled:
            if existing is None:
                return self._event_result(event, action="ignored_cancel_missing", google_event_id=None)
            event_id = str(existing.get("id") or "").strip()
            if event_id:
                self._calendar.events().delete(
                    calendarId=self.house_calendar_id,
                    eventId=event_id,
                    sendUpdates="none",
                ).execute()
            return self._event_result(event, action="cancelled", google_event_id=event_id or None)

        resource = dict(event.resource)
        private_props = dict(((resource.get("extendedProperties") or {}).get("private") or {}))
        private_props.update(
            {
                MANAGED_MARKER_KEY: MANAGED_MARKER_VALUE,
                "jarvis_gmail_message_id": str(message_id)[:256],
                "jarvis_ical_uid_hash": hashlib.sha256(event.uid.encode("utf-8")).hexdigest(),
            }
        )
        resource["extendedProperties"] = {"private": private_props}

        if existing is not None:
            event_id = str(existing.get("id") or "").strip()
            resource.pop("iCalUID", None)
            updated = (
                self._calendar.events()
                .patch(
                    calendarId=self.house_calendar_id,
                    eventId=event_id,
                    body=resource,
                    sendUpdates="none",
                )
                .execute()
            )
            return self._event_result(
                event,
                action="updated",
                google_event_id=str(updated.get("id") or event_id) or None,
            )

        imported = (
            self._calendar.events()
            .import_(
                calendarId=self.house_calendar_id,
                body=resource,
            )
            .execute()
        )
        return self._event_result(
            event,
            action="imported",
            google_event_id=str(imported.get("id") or "") or None,
        )

    def _extract_calendar_payloads(self, *, message_id: str, root_payload: dict[str, Any]) -> list[bytes]:
        values: list[bytes] = []

        def add(raw: bytes) -> None:
            if not raw or len(raw) > self.MAX_CALENDAR_PAYLOAD_BYTES:
                return
            digest = hashlib.sha256(raw).digest()
            if all(hashlib.sha256(existing).digest() != digest for existing in values):
                values.append(raw)

        def walk(part: dict[str, Any]) -> None:
            if len(values) >= self.MAX_CALENDAR_PAYLOADS:
                return
            mime_type = str(part.get("mimeType") or "").casefold()
            filename = str(part.get("filename") or "").casefold()
            body = part.get("body") if isinstance(part.get("body"), dict) else {}
            raw = self._gmail_part_bytes(message_id=message_id, body=body)
            if raw and (mime_type == "text/calendar" or filename.endswith(".ics")):
                add(raw)
            elif raw and (mime_type == "message/rfc822" or filename.endswith(".eml")):
                try:
                    email_message = BytesParser(policy=policy.default).parsebytes(raw)
                    for nested in email_message.walk():
                        nested_filename = str(nested.get_filename() or "").casefold()
                        if nested.get_content_type().casefold() == "text/calendar" or nested_filename.endswith(".ics"):
                            nested_raw = nested.get_payload(decode=True) or b""
                            add(nested_raw)
                except Exception:
                    pass
            for child in part.get("parts") or []:
                if isinstance(child, dict):
                    walk(child)

        walk(root_payload)
        return values

    def _gmail_part_bytes(self, *, message_id: str, body: dict[str, Any]) -> bytes:
        raw_data = str(body.get("data") or "").strip()
        if raw_data:
            return self._decode_base64url(raw_data)
        attachment_id = str(body.get("attachmentId") or "").strip()
        if not attachment_id:
            return b""
        attachment = (
            self._gmail.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
        return self._decode_base64url(str(attachment.get("data") or ""))

    def _parse_calendar_payload(self, raw: bytes) -> list[ParsedCalendarEvent]:
        try:
            from icalendar import Calendar
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("icalendar is required for forwarded calendar ingestion.") from exc

        calendar = Calendar.from_ical(raw)
        method = str(calendar.get("METHOD") or "REQUEST").strip().upper() or "REQUEST"
        parsed: list[ParsedCalendarEvent] = []
        for component in calendar.walk("VEVENT"):
            uid = str(component.get("UID") or "").strip()
            if not uid:
                continue
            recurrence_value = component.get("RECURRENCE-ID")
            recurrence_id = None
            if recurrence_value is not None:
                try:
                    recurrence_id = str(component.decoded("RECURRENCE-ID"))
                except Exception:
                    recurrence_id = str(recurrence_value)
            organizer_email = self._calendar_email(component.get("ORGANIZER"))
            resource = self._google_event_resource(component=component, uid=uid)
            if resource is None:
                continue
            parsed.append(
                ParsedCalendarEvent(
                    uid=uid,
                    recurrence_id=recurrence_id,
                    method=method,
                    status=str(component.get("STATUS") or "CONFIRMED").strip().upper(),
                    organizer_email=organizer_email,
                    resource=resource,
                    payload_hash=hashlib.sha256(component.to_ical()).hexdigest(),
                )
            )
            if len(parsed) >= self.MAX_EVENTS_PER_MESSAGE:
                break
        return parsed

    def _google_event_resource(self, *, component: Any, uid: str) -> dict[str, Any] | None:
        try:
            start_value = component.decoded("DTSTART")
        except Exception:
            return None
        try:
            end_value = component.decoded("DTEND")
        except Exception:
            end_value = None
        if end_value is None:
            try:
                duration = component.decoded("DURATION")
            except Exception:
                duration = None
            if isinstance(duration, timedelta):
                end_value = start_value + duration
        timezone_value = ZoneInfo(self._default_timezone)

        if isinstance(start_value, datetime):
            start_dt = start_value if start_value.tzinfo is not None else start_value.replace(tzinfo=timezone_value)
            if not isinstance(end_value, datetime):
                end_value = start_dt + timedelta(hours=1)
            end_dt = end_value if end_value.tzinfo is not None else end_value.replace(tzinfo=timezone_value)
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(hours=1)
            start_resource = {"dateTime": start_dt.isoformat()}
            end_resource = {"dateTime": end_dt.isoformat()}
        elif isinstance(start_value, date):
            start_date = start_value
            end_date = end_value if isinstance(end_value, date) and not isinstance(end_value, datetime) else None
            if end_date is None or end_date <= start_date:
                end_date = start_date + timedelta(days=1)
            start_resource = {"date": start_date.isoformat()}
            end_resource = {"date": end_date.isoformat()}
        else:
            return None

        resource: dict[str, Any] = {
            "iCalUID": uid,
            "summary": self._clean_text(component.get("SUMMARY") or "Calendar event", 1024),
            "start": start_resource,
            "end": end_resource,
        }
        description = self._clean_text(component.get("DESCRIPTION") or "", 8000)
        location = self._clean_text(component.get("LOCATION") or "", 1024)
        if description:
            resource["description"] = description
        if location:
            resource["location"] = location
        recurrence = component.get("RRULE")
        if recurrence is not None:
            recurrence_text = recurrence.to_ical().decode("utf-8", errors="replace").strip()
            if recurrence_text:
                resource["recurrence"] = [f"RRULE:{recurrence_text}"]
        return resource

    @staticmethod
    def _message_header_emails(payload: dict[str, Any]) -> set[str]:
        relevant_values: list[str] = []
        for header in payload.get("headers") or []:
            if not isinstance(header, dict):
                continue
            if str(header.get("name") or "").casefold() in {"from", "reply-to", "sender"}:
                relevant_values.append(str(header.get("value") or ""))
        return {
            email.casefold()
            for _, email in getaddresses(relevant_values)
            if email and "@" in email
        }

    @staticmethod
    def _calendar_email(value: Any) -> str | None:
        if value is None:
            return None
        raw = value.to_ical().decode("utf-8", errors="replace") if hasattr(value, "to_ical") else str(value)
        candidate = raw.strip().casefold().removeprefix("mailto:")
        return candidate if "@" in candidate else None

    @staticmethod
    def _decode_base64url(value: str) -> bytes:
        raw = str(value or "").encode("ascii", errors="ignore")
        raw += b"=" * (-len(raw) % 4)
        try:
            return base64.urlsafe_b64decode(raw)
        except Exception:
            return b""

    @staticmethod
    def _clean_text(value: Any, max_chars: int) -> str:
        return str(value or "").replace("\x00", "").strip()[: max(1, int(max_chars))]

    def _event_result(
        self,
        event: ParsedCalendarEvent,
        *,
        action: str,
        google_event_id: str | None,
    ) -> dict[str, Any]:
        return {
            "source_key": event.source_key,
            "ical_uid": event.uid,
            "recurrence_id": event.recurrence_id,
            "house_calendar_id": self.house_calendar_id,
            "google_event_id": google_event_id,
            "action": action,
            "payload_hash": event.payload_hash,
        }
