from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str):
        match = re.fullmatch(r"\$\{([A-Z0-9_]+)\}", value.strip())
        if match:
            return os.getenv(match.group(1), "")
        return value
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


@dataclass
class CalendarBinding:
    person_name: str
    calendar_id: str
    account_key: str | None


class GoogleCalendarLiveService:
    def __init__(self, permissions_path: str) -> None:
        self._permissions_path = permissions_path

    def add_event(
        self,
        *,
        event_title: str,
        when_hint: str,
        invitee_names: list[str] | None = None,
    ) -> dict[str, Any]:
        config = self._load_permissions()
        calendar_cfg = config.get("calendar") or {}
        oauth_cfg = config.get("oauth") or {}
        bindings = self._calendar_bindings(calendar_cfg)
        if not bindings:
            return {"status": "error", "message": "No calendar people bindings configured."}

        host_binding = self._select_host_binding(bindings=bindings, calendar_cfg=calendar_cfg)
        if host_binding is None:
            return {"status": "error", "message": "No house/default calendar binding configured for writes."}

        normalized_title = str(event_title or "").strip()
        normalized_when_hint = str(when_hint or "").strip()
        normalized_invitees = self._normalize_invitees(invitee_names)
        scopes = self._oauth_scopes(oauth_cfg=oauth_cfg, include_write=True)
        token_store_raw = str(oauth_cfg.get("token_store_path") or "data/google_tokens.json")
        token_store_path = self._resolve_path(token_store_raw, prefer_existing=False)
        token_store = self._load_token_store(token_store_path)
        changed = False
        account_key = self._resolve_account_key(host_binding, config)

        try:
            creds, token_store, token_changed = self._load_or_authorize_credentials(
                oauth_cfg=oauth_cfg,
                account_key=account_key,
                scopes=scopes,
                token_store=token_store,
            )
            changed = changed or token_changed
            service = self._build_calendar_service(creds)

            quick_add_text = self._build_quick_add_text(event_title=normalized_title, when_hint=normalized_when_hint)
            created_event = (
                service.events()
                .quickAdd(
                    calendarId=host_binding.calendar_id,
                    text=quick_add_text,
                    sendUpdates="none",
                )
                .execute()
            )

            google_event_id = str(created_event.get("id") or "").strip()
            resolved_invitee_emails, recognized_invitees, unresolved_invitees = self._resolve_invitee_emails(
                invitee_names=normalized_invitees,
                config=config,
                bindings=bindings,
            )
            if google_event_id and resolved_invitee_emails:
                attendees_payload = [{"email": email} for email in resolved_invitee_emails]
                created_event = (
                    service.events()
                    .patch(
                        calendarId=host_binding.calendar_id,
                        eventId=google_event_id,
                        body={"attendees": attendees_payload},
                        sendUpdates="all",
                    )
                    .execute()
                )

            if changed:
                self._save_token_store(token_store_path, token_store)

            normalized_event = self._normalize_event(created_event if isinstance(created_event, dict) else {})
            suggested_contacts = self._suggested_contact_names(
                config=config,
                bindings=bindings,
                host_person_name=host_binding.person_name,
                recognized_invitees=recognized_invitees,
            )
            invite_status = "suggested"
            if recognized_invitees and unresolved_invitees:
                invite_status = "partial"
            elif recognized_invitees:
                invite_status = "sent"

            invite_prompt = "Should I invite anyone so this also appears on their personal calendar?"
            if suggested_contacts:
                invite_prompt = (
                    f"Should I invite {self._format_contact_names(suggested_contacts)} so this also appears "
                    "on their personal calendar?"
                )
            if unresolved_invitees:
                unresolved_text = ", ".join(unresolved_invitees)
                invite_prompt = f"I could not resolve invitees: {unresolved_text}. Share emails or update contacts."

            return {
                "status": "ok",
                "source": "google_live",
                "host_calendar": host_binding.person_name,
                "event": {
                    "event_title": str(created_event.get("summary") or normalized_title),
                    "when_hint": normalized_when_hint,
                    "invitee_names": recognized_invitees,
                    "start_at": normalized_event.get("start_at") or "",
                    "end_at": normalized_event.get("end_at") or "",
                    "google_event_id": str(created_event.get("id") or ""),
                    "google_event_etag": str(created_event.get("etag") or ""),
                    "google_event_link": str(created_event.get("htmlLink") or ""),
                    "host_calendar_id": host_binding.calendar_id,
                    "attendee_emails": [
                        str(item.get("email") or "")
                        for item in created_event.get("attendees", [])
                        if isinstance(item, dict) and item.get("email")
                    ],
                },
                "sync_status": "synced_to_google",
                "invite_flow": {
                    "status": invite_status,
                    "prompt": invite_prompt,
                    "suggested_contacts": suggested_contacts,
                    "recognized_invitees": recognized_invitees,
                    "unresolved_invitees": unresolved_invitees,
                },
            }
        except Exception as exc:
            if changed:
                self._save_token_store(token_store_path, token_store)
            return {"status": "error", "message": f"Google Calendar write failed: {exc}"}

    def update_event(
        self,
        *,
        event_reference: str,
        new_event_title: str | None = None,
        new_when_hint: str | None = None,
        all_day: bool | None = None,
        event_id: str | None = None,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        reference = str(event_reference or "").strip()
        title_update = str(new_event_title or "").strip()
        when_update = str(new_when_hint or "").strip()
        if not str(event_id or "").strip() and not reference:
            return {
                "status": "needs_input",
                "message": "Which calendar event should I update?",
                "missing_fields": ["event_reference"],
            }
        if not title_update and not when_update and all_day is None:
            return {
                "status": "needs_input",
                "message": "What would you like to change about the event?",
                "missing_fields": ["changes"],
            }

        try:
            config = self._load_permissions()
            calendar_cfg = config.get("calendar") or {}
            bindings = self._calendar_bindings(calendar_cfg)
            binding = self._binding_for_calendar_id(bindings, calendar_id) or self._select_host_binding(
                bindings=bindings,
                calendar_cfg=calendar_cfg,
            )
            if binding is None:
                return {"status": "error", "message": "No house/default calendar binding configured for writes."}
            service = self._authorized_calendar_service(config=config, binding=binding, include_write=True)
            matched = self._resolve_event_for_mutation(
                service=service,
                binding=binding,
                event_reference=reference,
                event_id=event_id,
                calendar_cfg=calendar_cfg,
            )
            if matched.get("status") != "ok":
                return matched
            current = dict(matched.get("event") or {})
            provider_event_id = str(current.get("id") or "").strip()
            if not provider_event_id:
                return {"status": "error", "message": "Google Calendar returned an event without an ID."}

            if title_update:
                current["summary"] = title_update

            timezone_name = str(calendar_cfg.get("default_timezone") or "UTC")
            effective_all_day = all_day
            cleaned_when = when_update
            if re.search(r"\ball[ -]?day\b", cleaned_when, flags=re.IGNORECASE):
                effective_all_day = True
                cleaned_when = re.sub(r"\ball[ -]?day\b", "", cleaned_when, flags=re.IGNORECASE).strip(" ,.-")

            if effective_all_day is True:
                start_date = self._date_for_all_day_update(
                    when_hint=cleaned_when,
                    event=current,
                    timezone_name=timezone_name,
                )
                if start_date is None:
                    return {
                        "status": "needs_input",
                        "message": "What date should the all-day event use?",
                        "missing_fields": ["new_when_hint"],
                    }
                duration_days = self._all_day_duration_days(current)
                current["start"] = {"date": start_date.isoformat()}
                current["end"] = {"date": (start_date + timedelta(days=duration_days)).isoformat()}
            elif cleaned_when:
                start_at = self._parse_update_datetime(
                    when_hint=cleaned_when,
                    event=current,
                    timezone_name=timezone_name,
                )
                if start_at is None:
                    return {
                        "status": "needs_input",
                        "message": (
                            "I could not resolve the new date and time safely. "
                            "Use an explicit value such as `August 29 at 4pm`."
                        ),
                        "missing_fields": ["new_when_hint"],
                    }
                duration = self._timed_event_duration(current, timezone_name=timezone_name)
                end_at = start_at + duration
                current["start"] = {"dateTime": start_at.isoformat(), "timeZone": timezone_name}
                current["end"] = {"dateTime": end_at.isoformat(), "timeZone": timezone_name}

            updated = (
                service.events()
                .update(
                    calendarId=binding.calendar_id,
                    eventId=provider_event_id,
                    body=current,
                    sendUpdates="none",
                )
                .execute()
            )
            normalized = self._normalize_event(updated if isinstance(updated, dict) else {})
            return {
                "status": "ok",
                "source": "google_live",
                "sync_status": "synced_to_google",
                "host_calendar": binding.person_name,
                "event": {
                    **normalized,
                    "event_title": str(updated.get("summary") or title_update or reference),
                    "when_hint": when_update,
                    "all_day": bool((updated.get("start") or {}).get("date")),
                    "google_event_id": str(updated.get("id") or provider_event_id),
                    "google_event_etag": str(updated.get("etag") or ""),
                    "host_calendar_id": binding.calendar_id,
                    "attendee_emails": self._attendee_emails(updated),
                },
            }
        except Exception as exc:
            return {"status": "error", "source": "google_live", "message": f"Google Calendar update failed: {exc}"}

    def delete_event(
        self,
        *,
        event_reference: str,
        event_id: str | None = None,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        reference = str(event_reference or "").strip()
        if not str(event_id or "").strip() and not reference:
            return {
                "status": "needs_input",
                "message": "Which calendar event should I delete?",
                "missing_fields": ["event_reference"],
            }
        try:
            config = self._load_permissions()
            calendar_cfg = config.get("calendar") or {}
            bindings = self._calendar_bindings(calendar_cfg)
            binding = self._binding_for_calendar_id(bindings, calendar_id) or self._select_host_binding(
                bindings=bindings,
                calendar_cfg=calendar_cfg,
            )
            if binding is None:
                return {"status": "error", "message": "No house/default calendar binding configured for writes."}
            service = self._authorized_calendar_service(config=config, binding=binding, include_write=True)
            matched = self._resolve_event_for_mutation(
                service=service,
                binding=binding,
                event_reference=reference,
                event_id=event_id,
                calendar_cfg=calendar_cfg,
            )
            if matched.get("status") != "ok":
                return matched
            current = dict(matched.get("event") or {})
            provider_event_id = str(current.get("id") or "").strip()
            if not provider_event_id:
                return {"status": "error", "message": "Google Calendar returned an event without an ID."}
            service.events().delete(
                calendarId=binding.calendar_id,
                eventId=provider_event_id,
                sendUpdates="none",
            ).execute()
            normalized = self._normalize_event(current)
            return {
                "status": "ok",
                "source": "google_live",
                "sync_status": "synced_to_google",
                "deleted": True,
                "host_calendar": binding.person_name,
                "event": {
                    **normalized,
                    "event_title": str(current.get("summary") or reference),
                    "google_event_id": provider_event_id,
                    "google_event_etag": str(current.get("etag") or ""),
                    "host_calendar_id": binding.calendar_id,
                    "attendee_emails": self._attendee_emails(current),
                },
            }
        except Exception as exc:
            return {"status": "error", "source": "google_live", "message": f"Google Calendar delete failed: {exc}"}

    def get_event_by_id(self, *, calendar_id: str, event_id: str) -> dict[str, Any]:
        """Read one event from Google without trusting an execution log.

        This worker-facing read refuses to start an interactive OAuth flow. Missing
        or expired credentials therefore produce an explicit unavailable result.
        """
        normalized_calendar_id = str(calendar_id or "").strip()
        normalized_event_id = str(event_id or "").strip()
        if not normalized_calendar_id or not normalized_event_id:
            return {"status": "error", "error_code": "invalid_resource_locator"}

        try:
            config = self._load_permissions()
            calendar_cfg = config.get("calendar") or {}
            oauth_cfg = config.get("oauth") or {}
            bindings = self._calendar_bindings(calendar_cfg)
            binding = next(
                (item for item in bindings if item.calendar_id == normalized_calendar_id),
                None,
            )
            if binding is None:
                return {"status": "error", "error_code": "calendar_binding_missing"}
            account_key = self._resolve_account_key(binding, config)
            scopes = self._oauth_scopes(oauth_cfg=oauth_cfg, include_write=False)
            token_store_raw = str(oauth_cfg.get("token_store_path") or "data/google_tokens.json")
            token_store_path = self._resolve_path(token_store_raw, prefer_existing=False)
            token_store = self._load_token_store(token_store_path)
            creds, token_store, changed = self._load_or_authorize_credentials(
                oauth_cfg=oauth_cfg,
                account_key=account_key,
                scopes=scopes,
                token_store=token_store,
                allow_interactive=False,
            )
            if changed:
                self._save_token_store(token_store_path, token_store)
            event = (
                self._build_calendar_service(creds)
                .events()
                .get(calendarId=normalized_calendar_id, eventId=normalized_event_id)
                .execute()
            )
            normalized = self._normalize_event(event if isinstance(event, dict) else {})
            normalized.update(
                {
                    "google_event_id": str(event.get("id") or normalized_event_id),
                    "google_event_etag": str(event.get("etag") or ""),
                    "host_calendar_id": normalized_calendar_id,
                    "status": str(event.get("status") or ""),
                    "attendee_emails": sorted(
                        str(item.get("email") or "").casefold()
                        for item in event.get("attendees", [])
                        if isinstance(item, dict) and item.get("email")
                    ),
                }
            )
            return {"status": "ok", "source": "google_live", "event": normalized}
        except Exception as exc:
            error_code = "not_found" if self._exception_status_code(exc) == 404 else type(exc).__name__
            return {"status": "error", "error_code": error_code, "message": str(exc)}

    def get_calendar_view(self, person_name: str | None, window: str = "daily") -> dict[str, Any]:
        config = self._load_permissions()
        calendar_cfg = config.get("calendar") or {}
        oauth_cfg = config.get("oauth") or {}

        bindings = self._calendar_bindings(calendar_cfg)
        if not bindings:
            return {"status": "error", "message": "No calendar people bindings configured."}

        requested_person_name = self._normalize_requested_person_name(person_name)
        effective_person_name = requested_person_name
        defaulted_to_house_calendar = False
        if not effective_person_name or not str(effective_person_name).strip():
            default_person_name = self._default_person_name(calendar_cfg)
            if default_person_name:
                effective_person_name = default_person_name
                defaulted_to_house_calendar = True
        else:
            resolved_explicit = self._resolve_explicit_person_name(
                person_name=effective_person_name,
                bindings=bindings,
                config=config,
            )
            if resolved_explicit:
                effective_person_name = resolved_explicit
            else:
                default_person_name = self._default_person_name(calendar_cfg)
                if default_person_name:
                    effective_person_name = default_person_name
                    defaulted_to_house_calendar = True

        selected = self._select_bindings(bindings, person_name=effective_person_name)
        if not selected:
            label = str(effective_person_name or requested_person_name or person_name or "").strip() or "requested person"
            return {"status": "error", "message": f"No binding found for person `{label}`."}

        window_days = 7 if window == "weekly" else 1
        now = datetime.now(timezone.utc)
        time_min = now.isoformat().replace("+00:00", "Z")
        time_max = (now + timedelta(days=window_days)).isoformat().replace("+00:00", "Z")
        timezone_name = str(calendar_cfg.get("default_timezone") or "UTC")

        scopes = self._oauth_scopes(oauth_cfg=oauth_cfg, include_write=False)

        token_store_raw = str(oauth_cfg.get("token_store_path") or "data/google_tokens.json")
        token_store_path = self._resolve_path(token_store_raw, prefer_existing=False)
        token_store = self._load_token_store(token_store_path)
        changed = False
        rows: list[dict[str, Any]] = []
        total = 0

        for binding in selected:
            account_key = self._resolve_account_key(binding, config)
            try:
                creds, token_store, token_changed = self._load_or_authorize_credentials(
                    oauth_cfg=oauth_cfg,
                    account_key=account_key,
                    scopes=scopes,
                    token_store=token_store,
                )
                changed = changed or token_changed
                service = self._build_calendar_service(creds)
                events = (
                    service.events()
                    .list(
                        calendarId=binding.calendar_id,
                        timeMin=time_min,
                        timeMax=time_max,
                        singleEvents=True,
                        orderBy="startTime",
                        maxResults=100,
                        timeZone=timezone_name,
                    )
                    .execute()
                    .get("items", [])
                )
                normalized = [self._normalize_event(event) for event in events]
                total += len(normalized)
                rows.append(
                    {
                        "person_name": binding.person_name,
                        "calendar_id": binding.calendar_id,
                        "account_key": account_key,
                        "events": normalized,
                        "error": None,
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "person_name": binding.person_name,
                        "calendar_id": binding.calendar_id,
                        "account_key": account_key,
                        "events": [],
                        "error": str(exc),
                    }
                )

        if changed:
            self._save_token_store(token_store_path, token_store)

        summary_lines = [f"Calendar view ({window}):"]
        for row in rows:
            pname = row["person_name"]
            if row["error"]:
                summary_lines.append(f"- {pname}: Error - {row['error']}")
                continue
            events = row["events"]
            if not events:
                summary_lines.append(f"- {pname}: No events found.")
                continue
            for event in events:
                summary_lines.append(f"- {pname}: {event['title']} at {event['start_at']}")

        return {
            "status": "ok",
            "source": "google_live",
            "window": window,
            "target_person_name": str(effective_person_name).strip() if effective_person_name else None,
            "defaulted_to_house_calendar": defaulted_to_house_calendar,
            "event_count": total,
            "people": rows,
            "summary": "\n".join(summary_lines),
            "time_min": time_min,
            "time_max": time_max,
        }

    @staticmethod
    def _normalize_requested_person_name(value: Any) -> str | None:
        if value is None:
            return None

        candidate: str | None = None
        if isinstance(value, list):
            for item in value:
                text = str(item).strip(" []'\"")
                if text:
                    candidate = text
                    break
        else:
            candidate = str(value).strip()
        if not candidate:
            return None

        list_repr_match = re.fullmatch(r"\[\s*['\"]?(?P<value>[^'\"]+)['\"]?\s*\]", candidate)
        if list_repr_match:
            candidate = str(list_repr_match.group("value") or "").strip()
        candidate = re.sub(r"\bcalendar\b", "", candidate, flags=re.IGNORECASE).strip(" ,.-")
        candidate = re.sub(r"^(?:for|on|in|at|to)\s+", "", candidate, flags=re.IGNORECASE).strip(" ,.-")
        if not candidate:
            return None

        normalized = re.sub(r"[^a-z0-9\s_-]+", " ", candidate.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return None

        default_aliases = {"my", "our", "me", "us", "the", "house", "home", "household"}
        if normalized in default_aliases:
            return None
        neutral_tokens = {"my", "our", "me", "us", "the", "on", "in", "at", "for", "to", "house", "home"}
        tokens = [token for token in normalized.split() if token]
        if tokens and all(token in neutral_tokens for token in tokens):
            return None

        return candidate

    @staticmethod
    def _resolve_explicit_person_name(
        *,
        person_name: str,
        bindings: list[CalendarBinding],
        config: dict[str, Any],
    ) -> str | None:
        normalized_target = re.sub(r"[^a-z0-9]+", "", person_name.lower())
        if not normalized_target:
            return None

        def normalize_name(value: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", value.lower())

        names_by_key = {normalize_name(binding.person_name): binding.person_name for binding in bindings}
        if normalized_target in names_by_key:
            return names_by_key[normalized_target]

        aliases_cfg = (config.get("contacts") or {}).get("aliases") or []
        alias_to_name: dict[str, str] = {}
        for item in aliases_cfg:
            if not isinstance(item, dict):
                continue
            canonical_name = str(item.get("name") or "").strip()
            if not canonical_name:
                continue
            alias_to_name[normalize_name(canonical_name)] = canonical_name
            raw_aliases = item.get("aliases")
            if isinstance(raw_aliases, list):
                for alias in raw_aliases:
                    alias_text = str(alias).strip()
                    if alias_text:
                        alias_to_name[normalize_name(alias_text)] = canonical_name
        canonical = alias_to_name.get(normalized_target)
        if canonical:
            canonical_key = normalize_name(canonical)
            if canonical_key in names_by_key:
                return names_by_key[canonical_key]

        for binding in bindings:
            name_key = normalize_name(binding.person_name)
            if not name_key:
                continue
            if name_key.startswith(normalized_target) or normalized_target.startswith(name_key):
                return binding.person_name
            if name_key.endswith(normalized_target) and len(name_key) - len(normalized_target) <= 2:
                return binding.person_name
        return None

    @staticmethod
    def _calendar_bindings(calendar_cfg: dict[str, Any]) -> list[CalendarBinding]:
        people_raw = calendar_cfg.get("people") or []
        bindings: list[CalendarBinding] = []
        for item in people_raw:
            if not isinstance(item, dict):
                continue
            pname = str(item.get("person_name") or "").strip()
            cid = str(item.get("calendar_id") or "").strip()
            if pname and cid:
                account_key = str(item.get("account_key") or "").strip() or None
                bindings.append(CalendarBinding(person_name=pname, calendar_id=cid, account_key=account_key))
        return bindings

    @staticmethod
    def _select_host_binding(bindings: list[CalendarBinding], calendar_cfg: dict[str, Any]) -> CalendarBinding | None:
        if not bindings:
            return None

        house_cfg = calendar_cfg.get("house_calendar")
        if isinstance(house_cfg, dict):
            house_person = str(house_cfg.get("person_name") or "").strip()
            if house_person:
                matched = GoogleCalendarLiveService._binding_for_person(bindings, house_person)
                if matched is not None:
                    return matched
            house_calendar_id = str(house_cfg.get("calendar_id") or "").strip().lower()
            if house_calendar_id:
                for binding in bindings:
                    if binding.calendar_id.strip().lower() == house_calendar_id:
                        return binding

        default_person = GoogleCalendarLiveService._default_person_name(calendar_cfg)
        if default_person:
            matched = GoogleCalendarLiveService._binding_for_person(bindings, default_person)
            if matched is not None:
                return matched

        return bindings[0]

    @staticmethod
    def _binding_for_person(bindings: list[CalendarBinding], person_name: str) -> CalendarBinding | None:
        target = person_name.strip().lower()
        for binding in bindings:
            if binding.person_name.strip().lower() == target:
                return binding
        return None

    @staticmethod
    def _oauth_scopes(oauth_cfg: dict[str, Any], *, include_write: bool) -> list[str]:
        scopes = [str(item).strip() for item in list(oauth_cfg.get("scopes") or []) if str(item).strip()]
        readonly_scope = "https://www.googleapis.com/auth/calendar.readonly"
        write_scope = "https://www.googleapis.com/auth/calendar.events"
        if readonly_scope not in scopes:
            scopes.append(readonly_scope)
        if include_write and write_scope not in scopes:
            scopes.append(write_scope)
        return scopes

    @staticmethod
    def _build_quick_add_text(*, event_title: str, when_hint: str) -> str:
        return f"{event_title.strip()} {when_hint.strip()}".strip()

    @staticmethod
    def _normalize_invitees(invitee_names: list[str] | None) -> list[str]:
        if not invitee_names:
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in invitee_names:
            name = str(item).strip(" .,'\"")
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(name)
        return normalized

    @staticmethod
    def _format_contact_names(names: list[str]) -> str:
        if len(names) <= 1:
            return names[0] if names else "anyone"
        if len(names) == 2:
            return f"{names[0]} or {names[1]}"
        return f"{', '.join(names[:-1])}, or {names[-1]}"

    @staticmethod
    def _resolve_invitee_emails(
        *,
        invitee_names: list[str],
        config: dict[str, Any],
        bindings: list[CalendarBinding],
    ) -> tuple[list[str], list[str], list[str]]:
        aliases_cfg = (config.get("contacts") or {}).get("aliases") or []
        alias_lookup: dict[str, tuple[str, str]] = {}
        for item in aliases_cfg:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            email = str(item.get("email") or "").strip()
            if not name or not email:
                continue
            alias_lookup[name.lower()] = (name, email)

        people_lookup: dict[str, tuple[str, str]] = {}
        for binding in bindings:
            email_candidate = binding.calendar_id.strip()
            if "@" not in email_candidate:
                continue
            people_lookup[binding.person_name.strip().lower()] = (binding.person_name, email_candidate)

        resolved_emails: list[str] = []
        recognized_invitees: list[str] = []
        unresolved_invitees: list[str] = []
        seen_email: set[str] = set()
        seen_name: set[str] = set()

        for raw_name in invitee_names:
            candidate = str(raw_name).strip(" .,'\"")
            if not candidate:
                continue
            candidate_key = candidate.lower()

            resolved_name: str | None = None
            resolved_email: str | None = None
            if "@" in candidate:
                resolved_name = candidate
                resolved_email = candidate
            elif candidate_key in alias_lookup:
                resolved_name, resolved_email = alias_lookup[candidate_key]
            elif candidate_key in people_lookup:
                resolved_name, resolved_email = people_lookup[candidate_key]

            if not resolved_email:
                unresolved_invitees.append(candidate)
                continue

            email_key = resolved_email.lower()
            if email_key not in seen_email:
                seen_email.add(email_key)
                resolved_emails.append(resolved_email)

            if resolved_name:
                name_key = resolved_name.lower()
                if name_key not in seen_name:
                    seen_name.add(name_key)
                    recognized_invitees.append(resolved_name)

        return resolved_emails, recognized_invitees, unresolved_invitees

    @staticmethod
    def _suggested_contact_names(
        *,
        config: dict[str, Any],
        bindings: list[CalendarBinding],
        host_person_name: str,
        recognized_invitees: list[str],
    ) -> list[str]:
        contact_names: list[str] = []
        aliases_cfg = (config.get("contacts") or {}).get("aliases") or []
        for item in aliases_cfg:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                contact_names.append(name)

        if not contact_names:
            for binding in bindings:
                name = binding.person_name.strip()
                if name:
                    contact_names.append(name)

        excluded = {host_person_name.strip().lower()}
        excluded.update(name.strip().lower() for name in recognized_invitees if name.strip())

        deduped: list[str] = []
        seen: set[str] = set()
        for name in contact_names:
            key = name.strip().lower()
            if not key or key in excluded or key in seen:
                continue
            seen.add(key)
            deduped.append(name)
        return deduped

    def _authorized_calendar_service(
        self,
        *,
        config: dict[str, Any],
        binding: CalendarBinding,
        include_write: bool,
    ) -> Any:
        oauth_cfg = config.get("oauth") or {}
        scopes = self._oauth_scopes(oauth_cfg=oauth_cfg, include_write=include_write)
        token_store_raw = str(oauth_cfg.get("token_store_path") or "data/google_tokens.json")
        token_store_path = self._resolve_path(token_store_raw, prefer_existing=False)
        token_store = self._load_token_store(token_store_path)
        credentials, token_store, changed = self._load_or_authorize_credentials(
            oauth_cfg=oauth_cfg,
            account_key=self._resolve_account_key(binding, config),
            scopes=scopes,
            token_store=token_store,
            allow_interactive=False,
        )
        # Persist a refreshed token before a mutating provider request so a
        # successful event write cannot be reported as failed only because the
        # subsequent token-store write failed.
        if changed:
            self._save_token_store(token_store_path, token_store)
        return self._build_calendar_service(credentials)

    def _resolve_event_for_mutation(
        self,
        *,
        service: Any,
        binding: CalendarBinding,
        event_reference: str,
        event_id: str | None,
        calendar_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        provider_event_id = str(event_id or "").strip()
        if provider_event_id:
            event = service.events().get(
                calendarId=binding.calendar_id,
                eventId=provider_event_id,
            ).execute()
            return {"status": "ok", "event": event}

        reference = str(event_reference or "").strip(" .,'\"")
        if self._is_deictic_event_reference(reference):
            return {
                "status": "needs_input",
                "message": "Which calendar event do you mean?",
                "missing_fields": ["event_reference"],
            }
        if not reference:
            return {
                "status": "needs_input",
                "message": "Which calendar event do you mean?",
                "missing_fields": ["event_reference"],
            }

        now = datetime.now(timezone.utc)
        timezone_name = str(calendar_cfg.get("default_timezone") or "UTC")
        items = (
            service.events()
            .list(
                calendarId=binding.calendar_id,
                timeMin=(now - timedelta(days=365)).isoformat().replace("+00:00", "Z"),
                timeMax=(now + timedelta(days=730)).isoformat().replace("+00:00", "Z"),
                singleEvents=True,
                orderBy="startTime",
                maxResults=100,
                q=reference,
                timeZone=timezone_name,
            )
            .execute()
            .get("items", [])
        )
        active = [
            item
            for item in items
            if isinstance(item, dict) and str(item.get("status") or "confirmed").casefold() != "cancelled"
        ]
        reference_key = self._event_reference_key(reference)
        exact = [
            item
            for item in active
            if self._event_reference_key(str(item.get("summary") or "")) == reference_key
        ]
        candidates = exact
        if not candidates:
            candidates = [
                item
                for item in active
                if reference_key
                and reference_key in self._event_reference_key(str(item.get("summary") or ""))
            ]
        if len(candidates) == 1:
            return {"status": "ok", "event": candidates[0]}
        if not candidates:
            return {
                "status": "not_found",
                "message": f"I could not find a calendar event matching `{reference}`.",
                "event_reference": reference,
            }
        suggestions = [
            {
                "event_reference": str(item.get("summary") or "(untitled event)"),
                "start_at": self._normalize_event(item).get("start_at"),
            }
            for item in candidates[:5]
        ]
        return {
            "status": "ambiguous_event",
            "message": f"I found multiple events matching `{reference}`. Which one do you mean?",
            "event_reference": reference,
            "suggestions": suggestions,
        }

    @staticmethod
    def _binding_for_calendar_id(
        bindings: list[CalendarBinding],
        calendar_id: str | None,
    ) -> CalendarBinding | None:
        target = str(calendar_id or "").strip().casefold()
        if not target:
            return None
        return next(
            (binding for binding in bindings if binding.calendar_id.strip().casefold() == target),
            None,
        )

    @staticmethod
    def _event_reference_key(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()

    @staticmethod
    def _is_deictic_event_reference(value: str) -> bool:
        normalized = GoogleCalendarLiveService._event_reference_key(value)
        return normalized in {
            "it",
            "that",
            "this",
            "that event",
            "this event",
            "the event",
            "same event",
        }

    @staticmethod
    def _attendee_emails(event: dict[str, Any]) -> list[str]:
        return sorted(
            str(item.get("email") or "").strip().casefold()
            for item in event.get("attendees", [])
            if isinstance(item, dict) and str(item.get("email") or "").strip()
        )

    @staticmethod
    def _exception_status_code(exc: Exception) -> int | None:
        direct = getattr(exc, "status_code", None)
        if isinstance(direct, int):
            return direct
        response = getattr(exc, "resp", None)
        response_status = getattr(response, "status", None)
        return response_status if isinstance(response_status, int) else None

    @classmethod
    def _date_for_all_day_update(
        cls,
        *,
        when_hint: str,
        event: dict[str, Any],
        timezone_name: str,
    ) -> date | None:
        existing = cls._event_start_date(event, timezone_name=timezone_name)
        if not str(when_hint or "").strip():
            return existing
        return cls._parse_date_hint(
            value=when_hint,
            default_date=existing,
            timezone_name=timezone_name,
        )

    @staticmethod
    def _all_day_duration_days(event: dict[str, Any]) -> int:
        start_raw = str((event.get("start") or {}).get("date") or "").strip()
        end_raw = str((event.get("end") or {}).get("date") or "").strip()
        if start_raw and end_raw:
            try:
                return max(1, (date.fromisoformat(end_raw) - date.fromisoformat(start_raw)).days)
            except ValueError:
                pass
        return 1

    @classmethod
    def _parse_update_datetime(
        cls,
        *,
        when_hint: str,
        event: dict[str, Any],
        timezone_name: str,
    ) -> datetime | None:
        existing = cls._event_start_datetime(event, timezone_name=timezone_name)
        default_date = existing.date() if existing is not None else None
        parsed_date = cls._parse_date_hint(
            value=when_hint,
            default_date=default_date,
            timezone_name=timezone_name,
        )
        parsed_time = cls._parse_time_hint(when_hint)
        if parsed_date is None:
            return None
        if parsed_time is None:
            if existing is None:
                return None
            parsed_time = existing.timetz().replace(tzinfo=None)
        try:
            tzinfo = ZoneInfo(timezone_name)
        except Exception:
            tzinfo = timezone.utc
        return datetime.combine(parsed_date, parsed_time, tzinfo=tzinfo)

    @staticmethod
    def _event_start_date(event: dict[str, Any], *, timezone_name: str) -> date | None:
        start = event.get("start") or {}
        date_value = str(start.get("date") or "").strip()
        if date_value:
            try:
                return date.fromisoformat(date_value)
            except ValueError:
                return None
        start_at = GoogleCalendarLiveService._event_start_datetime(event, timezone_name=timezone_name)
        return start_at.date() if start_at is not None else None

    @staticmethod
    def _event_start_datetime(event: dict[str, Any], *, timezone_name: str) -> datetime | None:
        start_raw = str((event.get("start") or {}).get("dateTime") or "").strip()
        if not start_raw:
            return None
        try:
            parsed = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            try:
                parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
            except Exception:
                parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @classmethod
    def _timed_event_duration(cls, event: dict[str, Any], *, timezone_name: str) -> timedelta:
        start_at = cls._event_start_datetime(event, timezone_name=timezone_name)
        end_raw = str((event.get("end") or {}).get("dateTime") or "").strip()
        if start_at is None or not end_raw:
            return timedelta(hours=1)
        try:
            end_at = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
        except ValueError:
            return timedelta(hours=1)
        duration = end_at - start_at
        return duration if duration.total_seconds() > 0 else timedelta(hours=1)

    @staticmethod
    def _parse_date_hint(
        *,
        value: str,
        default_date: date | None,
        timezone_name: str,
    ) -> date | None:
        cleaned = re.sub(r"\s+", " ", str(value or "").strip().casefold())
        try:
            local_today = datetime.now(ZoneInfo(timezone_name)).date()
        except Exception:
            local_today = datetime.now(timezone.utc).date()
        if re.search(r"\btomorrow\b", cleaned):
            return local_today + timedelta(days=1)
        if re.search(r"\btoday\b", cleaned):
            return local_today

        iso_match = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", cleaned)
        if iso_match:
            try:
                return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
            except ValueError:
                return None

        months = {
            "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
            "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
            "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
            "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
        }
        month_match = re.search(
            r"\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|"
            r"august|aug|september|sept|sep|october|oct|november|nov|december|dec)\s+"
            r"(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b",
            cleaned,
        )
        if month_match:
            year = int(month_match.group(3) or (default_date.year if default_date else local_today.year))
            try:
                return date(year, months[month_match.group(1)], int(month_match.group(2)))
            except ValueError:
                return None

        weekday_names = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        weekday_match = re.search(r"\b(?:(next|this)\s+)?(" + "|".join(weekday_names) + r")\b", cleaned)
        if weekday_match:
            target_weekday = weekday_names[weekday_match.group(2)]
            delta = (target_weekday - local_today.weekday()) % 7
            if delta == 0:
                delta = 7
            if weekday_match.group(1) == "next":
                delta += 7
            return local_today + timedelta(days=delta)
        return default_date

    @staticmethod
    def _parse_time_hint(value: str) -> time | None:
        cleaned = re.sub(r"\s+", " ", str(value or "").strip().casefold())
        meridiem_match = re.search(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b", cleaned)
        if meridiem_match:
            hour = int(meridiem_match.group(1)) % 12
            if meridiem_match.group(3) == "pm":
                hour += 12
            return time(hour=hour, minute=int(meridiem_match.group(2) or 0))
        # A bare `5:00` is ambiguous in natural language. Only accept an
        # explicitly zero-padded/24-hour clock when no am/pm marker is present.
        clock_24h_match = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", cleaned)
        if clock_24h_match:
            return time(hour=int(clock_24h_match.group(1)), minute=int(clock_24h_match.group(2)))
        return None

    def _load_permissions(self) -> dict[str, Any]:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required for Google permissions parsing.") from exc
        path = self._resolve_path(self._permissions_path, prefer_existing=True)
        if not path.exists():
            raise RuntimeError(f"Google permissions file not found: {self._permissions_path}")
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise RuntimeError("Google permissions YAML must be a mapping.")
        if "google" in loaded and isinstance(loaded["google"], dict):
            loaded = loaded["google"]
        return _substitute_env(loaded)

    @staticmethod
    def _select_bindings(bindings: list[CalendarBinding], person_name: str | None) -> list[CalendarBinding]:
        if person_name and person_name.strip():
            target = person_name.strip().lower()
            return [item for item in bindings if item.person_name.strip().lower() == target]
        return bindings

    @staticmethod
    def _default_person_name(calendar_cfg: dict[str, Any]) -> str | None:
        house_cfg = calendar_cfg.get("house_calendar")
        if isinstance(house_cfg, dict):
            house_person = str(house_cfg.get("person_name") or "").strip()
            if house_person:
                return house_person

        house_person = str(calendar_cfg.get("house_person_name") or "").strip()
        if house_person:
            return house_person

        default_person = str(calendar_cfg.get("default_person_name") or "").strip()
        if default_person:
            return default_person

        return None

    @staticmethod
    def _resolve_account_key(binding: CalendarBinding, config: dict[str, Any]) -> str:
        if binding.account_key:
            return binding.account_key
        accounts = (config.get("calendar") or {}).get("accounts") or []
        for account in accounts:
            if isinstance(account, dict) and account.get("enabled", True):
                key = str(account.get("account_key") or "").strip()
                if key:
                    return key
        return "default"

    @staticmethod
    def _load_token_store(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _save_token_store(path: Path, token_store: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(token_store, indent=2), encoding="utf-8")

    def _load_or_authorize_credentials(
        self,
        oauth_cfg: dict[str, Any],
        account_key: str,
        scopes: list[str],
        token_store: dict[str, Any],
        allow_interactive: bool = True,
    ) -> tuple[Any, dict[str, Any], bool]:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:
            raise RuntimeError(
                "Google Calendar dependencies are not installed. "
                "Install `google-auth`, `google-auth-oauthlib`, and `google-api-python-client`."
            ) from exc

        token_data = token_store.get(account_key)
        creds = None
        token_changed = False
        if isinstance(token_data, dict):
            try:
                creds = Credentials.from_authorized_user_info(token_data, scopes=scopes)
            except Exception:
                creds = None

        if creds and hasattr(creds, "has_scopes") and not creds.has_scopes(scopes):
            creds = None

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_changed = True

        if not creds or not creds.valid:
            if not allow_interactive:
                raise RuntimeError("Google credentials unavailable; interactive OAuth is disabled for verification.")
            client_config = self._resolve_client_config(oauth_cfg)
            flow = InstalledAppFlow.from_client_config(client_config, scopes=scopes)
            redirect_uri = str(oauth_cfg.get("redirect_uri") or "http://localhost:8080/oauth2/callback")
            flow.redirect_uri = redirect_uri
            creds = flow.run_local_server(
                port=0,
                access_type="offline",
                prompt="consent",
                include_granted_scopes="true",
            )
            token_changed = True

        if token_changed or account_key not in token_store:
            token_store[account_key] = json.loads(creds.to_json())
            token_changed = True
        return creds, token_store, token_changed

    def _resolve_client_config(self, oauth_cfg: dict[str, Any]) -> dict[str, Any]:
        client_id = str(oauth_cfg.get("client_id") or "").strip()
        client_secret = str(oauth_cfg.get("client_secret") or "").strip()
        project_id = str(oauth_cfg.get("project_id") or "").strip()
        auth_uri = str(oauth_cfg.get("auth_uri") or "https://accounts.google.com/o/oauth2/auth")
        token_uri = str(oauth_cfg.get("token_uri") or "https://oauth2.googleapis.com/token")
        redirect_uri = str(oauth_cfg.get("redirect_uri") or "http://localhost:8080/oauth2/callback")

        if client_id and client_secret:
            return {
                "installed": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "project_id": project_id,
                    "auth_uri": auth_uri,
                    "token_uri": token_uri,
                    "redirect_uris": [redirect_uri],
                }
            }

        credentials_file = str(oauth_cfg.get("client_credentials_file") or "").strip()
        if credentials_file:
            path = self._resolve_path(credentials_file, prefer_existing=True)
            if not path.exists():
                raise RuntimeError(f"Google OAuth credentials file not found: {credentials_file}")
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise RuntimeError("Google OAuth credentials file must be a JSON object.")
            if "installed" in loaded and isinstance(loaded["installed"], dict):
                return {"installed": loaded["installed"]}
            if "web" in loaded and isinstance(loaded["web"], dict):
                return {"web": loaded["web"]}
            raise RuntimeError("Google OAuth credentials JSON must have `installed` or `web`.")

        raise RuntimeError(
            "Google OAuth client credentials are missing. Set oauth.client_id/client_secret "
            "or oauth.client_credentials_file in permissions."
        )

    def _resolve_path(self, value: str, prefer_existing: bool) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path

        permissions_path = Path(self._permissions_path)
        permissions_dir = permissions_path.parent
        permissions_root = permissions_dir.parent
        candidates = [
            path,
            permissions_dir / path,
            permissions_root / path,
        ]
        if prefer_existing:
            for candidate in candidates:
                if candidate.exists():
                    return candidate
        return candidates[0]

    @staticmethod
    def _build_calendar_service(credentials: Any):
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError("google-api-python-client is not installed.") from exc
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    @staticmethod
    def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
        start = event.get("start", {}) or {}
        end = event.get("end", {}) or {}
        return {
            "title": str(event.get("summary") or "(untitled event)"),
            "start_at": str(start.get("dateTime") or start.get("date") or ""),
            "end_at": str(end.get("dateTime") or end.get("date") or ""),
            "location": str(event.get("location") or ""),
            "description": str(event.get("description") or ""),
            "google_event_id": str(event.get("id") or ""),
            "google_event_etag": str(event.get("etag") or ""),
        }
