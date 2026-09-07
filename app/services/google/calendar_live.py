from __future__ import annotations

import base64
import hashlib
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

    def query_timezone(self) -> str | None:
        config = self._load_permissions()
        calendar_cfg = config.get("calendar") or {}
        timezone_name = str(calendar_cfg.get("default_timezone") or "UTC").strip() or "UTC"
        try:
            ZoneInfo(timezone_name)
        except Exception:
            return None
        return timezone_name

    def canonicalize_typed_write(
        self,
        *,
        tool_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve provider-owned refs/revisions before tool-operation hashing."""

        normalized_tool = str(tool_id or "").strip().casefold()
        if normalized_tool not in {
            "calendar.create_event",
            "calendar.create_event_with_invites",
            "calendar.update_event",
            "calendar.delete_event",
        }:
            raise ValueError("calendar_tool_unsupported")
        config = self._load_permissions()
        calendar_cfg = config.get("calendar") or {}
        bindings = self._calendar_bindings(calendar_cfg)
        requested_scope = str(arguments.get("calendar_scope") or "").strip()
        binding, candidates, _ = self._resolve_query_binding(
            calendar_scope=requested_scope,
            bindings=bindings,
            config=config,
            calendar_cfg=calendar_cfg,
        )
        if binding is None:
            raise ValueError(
                "calendar_scope_ambiguous" if candidates else "calendar_scope_not_authorized"
            )
        timezone_name = str(calendar_cfg.get("default_timezone") or "UTC").strip() or "UTC"
        try:
            ZoneInfo(timezone_name)
        except Exception as exc:
            raise ValueError("calendar_timezone_invalid") from exc
        requested_timezone = str(arguments.get("timezone") or "").strip()
        if requested_timezone and requested_timezone != timezone_name:
            raise ValueError("calendar_timezone_stale")
        calendar_ref = self._calendar_ref(binding.calendar_id)
        calendar_version = self._calendar_resource_version(
            binding=binding,
            timezone_name=timezone_name,
        )
        supplied_ref = str(arguments.get("calendar_ref") or "").strip()
        supplied_version = str(arguments.get("resource_version") or "").strip()
        if supplied_ref and supplied_ref != calendar_ref:
            raise ValueError("calendar_target_changed")
        canonical = dict(arguments)
        canonical.update(
            {
                "calendar_scope": binding.person_name,
                "calendar_ref": calendar_ref,
                "timezone": timezone_name,
            }
        )
        if normalized_tool in {"calendar.create_event", "calendar.create_event_with_invites"}:
            if supplied_version and supplied_version != calendar_version:
                raise ValueError("calendar_resource_version_stale")
            canonical["resource_version"] = calendar_version
            return canonical

        service = self._authorized_calendar_service(
            config=config,
            binding=binding,
            include_write=True,
        )
        current = self._resolve_typed_event(
            service=service,
            binding=binding,
            event_ref=str(arguments.get("event_ref") or ""),
            event_start=str(arguments.get("event_start") or ""),
            timezone_name=timezone_name,
        )
        current_version = self._event_resource_version(str(current.get("etag") or ""))
        if supplied_version and supplied_version != current_version:
            raise ValueError("calendar_event_revision_stale")
        canonical["resource_version"] = current_version
        return canonical

    def execute_typed_create(
        self,
        *,
        operation_id: str,
        arguments_hash: str,
        arguments: dict[str, Any],
        include_invites: bool,
    ) -> dict[str, Any]:
        try:
            binding, timezone_name, service = self._typed_runtime(arguments)
            event_id = self.typed_event_id(operation_id)
            body = self._typed_create_body(
                event_id=event_id,
                operation_id=operation_id,
                arguments_hash=arguments_hash,
                arguments=arguments,
                include_invites=include_invites,
            )
            try:
                created = (
                    service.events()
                    .insert(
                        calendarId=binding.calendar_id,
                        body=body,
                        sendUpdates="all" if include_invites else "none",
                    )
                    .execute()
                )
            except Exception as exc:
                return self._reconcile_typed_create(
                    service=service,
                    binding=binding,
                    timezone_name=timezone_name,
                    event_id=event_id,
                    expected=body,
                    operation_id=operation_id,
                    arguments_hash=arguments_hash,
                    original_error=exc,
                )
            if not isinstance(created, dict) or not self._typed_event_matches(
                created,
                expected=body,
                operation_id=operation_id,
                arguments_hash=arguments_hash,
            ):
                return self._reconcile_typed_create(
                    service=service,
                    binding=binding,
                    timezone_name=timezone_name,
                    event_id=event_id,
                    expected=body,
                    operation_id=operation_id,
                    arguments_hash=arguments_hash,
                    original_error=RuntimeError("calendar_insert_response_uncertain"),
                )
            return self._typed_success(
                event=created,
                binding=binding,
                timezone_name=timezone_name,
                operation_id=operation_id,
                action="created",
            )
        except ValueError as exc:
            return self._typed_denied(str(exc))
        except Exception as exc:
            return self._typed_retryable(type(exc).__name__)

    def execute_typed_update(
        self,
        *,
        operation_id: str,
        arguments_hash: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            binding, timezone_name, service = self._typed_runtime(arguments)
            current = self._resolve_typed_event(
                service=service,
                binding=binding,
                event_ref=str(arguments.get("event_ref") or ""),
                event_start=str(arguments.get("event_start") or ""),
                timezone_name=timezone_name,
            )
            revision = str(current.get("etag") or "")
            if self._event_resource_version(revision) != str(
                arguments.get("resource_version") or ""
            ):
                return self._typed_denied("calendar_event_revision_stale")
            patch = dict(arguments.get("patch") or {})
            body = self._typed_update_body(
                current=current,
                patch=patch,
                timezone_name=timezone_name,
                operation_id=operation_id,
                arguments_hash=arguments_hash,
            )
            event_id = str(current.get("id") or "")
            try:
                request = service.events().patch(
                    calendarId=binding.calendar_id,
                    eventId=event_id,
                    body=body,
                    sendUpdates="none",
                )
                updated = self._execute_conditional(request, revision=revision)
            except Exception as exc:
                return self._reconcile_typed_update(
                    service=service,
                    binding=binding,
                    timezone_name=timezone_name,
                    event_id=event_id,
                    expected=body,
                    original_revision=revision,
                    operation_id=operation_id,
                    arguments_hash=arguments_hash,
                    original_error=exc,
                )
            if not isinstance(updated, dict):
                return self._typed_retryable("calendar_update_response_uncertain")
            return self._typed_success(
                event=updated,
                binding=binding,
                timezone_name=timezone_name,
                operation_id=operation_id,
                action="updated",
            )
        except ValueError as exc:
            return self._typed_denied(str(exc))
        except Exception as exc:
            return self._typed_retryable(type(exc).__name__)

    def execute_typed_delete(
        self,
        *,
        operation_id: str,
        arguments_hash: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        del arguments_hash
        try:
            binding, timezone_name, service = self._typed_runtime(arguments)
            current = self._resolve_typed_event(
                service=service,
                binding=binding,
                event_ref=str(arguments.get("event_ref") or ""),
                event_start=str(arguments.get("event_start") or ""),
                timezone_name=timezone_name,
            )
            revision = str(current.get("etag") or "")
            if self._event_resource_version(revision) != str(
                arguments.get("resource_version") or ""
            ):
                return self._typed_denied("calendar_event_revision_stale")
            event_id = str(current.get("id") or "")
            try:
                request = service.events().delete(
                    calendarId=binding.calendar_id,
                    eventId=event_id,
                    sendUpdates="none",
                )
                self._execute_conditional(request, revision=revision)
            except Exception as exc:
                status, observed = self._get_exact_event(
                    service=service,
                    calendar_id=binding.calendar_id,
                    event_id=event_id,
                )
                if status == "not_found":
                    return self._typed_success(
                        event=current,
                        binding=binding,
                        timezone_name=timezone_name,
                        operation_id=operation_id,
                        action="deleted",
                        deleted=True,
                    )
                if status == "ok" and self._event_resource_version(
                    str(observed.get("etag") or "")
                ) == self._event_resource_version(revision):
                    return self._typed_retryable(type(exc).__name__)
                return self._typed_conflict("calendar_delete_revision_conflict")
            return self._typed_success(
                event=current,
                binding=binding,
                timezone_name=timezone_name,
                operation_id=operation_id,
                action="deleted",
                deleted=True,
            )
        except ValueError as exc:
            code = str(exc)
            if code == "calendar_event_not_found":
                return self._typed_denied("calendar_event_stale")
            return self._typed_denied(code)
        except Exception as exc:
            return self._typed_retryable(type(exc).__name__)

    @staticmethod
    def typed_event_id(operation_id: str) -> str:
        digest = hashlib.sha256(str(operation_id or "").encode("utf-8")).digest()
        encoded = base64.b32hexencode(digest).decode("ascii").rstrip("=").lower()
        return "jarvis" + encoded

    @staticmethod
    def _calendar_ref(calendar_id: str) -> str:
        digest = hashlib.sha256(str(calendar_id or "").encode("utf-8")).hexdigest()
        return "calendar_target_v1_" + digest[:32]

    @staticmethod
    def _event_resource_version(etag: str) -> str:
        digest = hashlib.sha256(str(etag or "").encode("utf-8")).hexdigest()
        return "calendar_revision_v1_" + digest

    @staticmethod
    def _calendar_resource_version(
        *,
        binding: CalendarBinding,
        timezone_name: str,
    ) -> str:
        material = json.dumps(
            {
                "account_key": binding.account_key or "",
                "calendar_id": binding.calendar_id,
                "person_name": binding.person_name,
                "timezone": timezone_name,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "calendar_config_v1_" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _typed_runtime(
        self,
        arguments: dict[str, Any],
    ) -> tuple[CalendarBinding, str, Any]:
        config = self._load_permissions()
        calendar_cfg = config.get("calendar") or {}
        timezone_name = str(calendar_cfg.get("default_timezone") or "UTC").strip() or "UTC"
        calendar_ref = str(arguments.get("calendar_ref") or "")
        scope = str(arguments.get("calendar_scope") or "").strip().casefold()
        matches = [
            item
            for item in self._calendar_bindings(calendar_cfg)
            if self._calendar_ref(item.calendar_id) == calendar_ref
            and item.person_name.strip().casefold() == scope
        ]
        if len(matches) != 1:
            raise ValueError("calendar_target_changed")
        binding = matches[0]
        requested_timezone = str(arguments.get("timezone") or "")
        if requested_timezone != timezone_name:
            raise ValueError("calendar_timezone_stale")
        supplied_version = str(arguments.get("resource_version") or "")
        if str(arguments.get("event_ref") or ""):
            if not supplied_version.startswith("calendar_revision_v1_"):
                raise ValueError("calendar_event_revision_invalid")
        elif supplied_version != self._calendar_resource_version(
            binding=binding,
            timezone_name=timezone_name,
        ):
            raise ValueError("calendar_resource_version_stale")
        service = self._authorized_calendar_service(
            config=config,
            binding=binding,
            include_write=True,
        )
        return binding, timezone_name, service

    def _resolve_typed_event(
        self,
        *,
        service: Any,
        binding: CalendarBinding,
        event_ref: str,
        event_start: str,
        timezone_name: str,
    ) -> dict[str, Any]:
        reference = str(event_ref or "").strip().casefold()
        if not re.fullmatch(r"calendar_event_v1_[0-9a-f]{32}", reference):
            raise ValueError("calendar_event_ref_invalid")
        raw_start = str(event_start or "").strip()
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_start):
                local_start = datetime.combine(
                    date.fromisoformat(raw_start),
                    time.min,
                    tzinfo=ZoneInfo(timezone_name),
                )
            else:
                local_start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
                if local_start.tzinfo is None:
                    raise ValueError
        except Exception as exc:
            raise ValueError("calendar_event_start_invalid") from exc
        response = (
            service.events()
            .list(
                calendarId=binding.calendar_id,
                timeMin=(local_start - timedelta(days=2)).isoformat(),
                timeMax=(local_start + timedelta(days=2)).isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=100,
                timeZone=timezone_name,
                showDeleted=False,
            )
            .execute()
        )
        items = response.get("items", []) if isinstance(response, dict) else []
        matches = [
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("status") or "confirmed").casefold() != "cancelled"
            and self._event_ref(event=item, binding=binding) == reference
        ]
        if not matches:
            raise ValueError("calendar_event_not_found")
        if len(matches) != 1:
            raise ValueError("calendar_event_ambiguous")
        return dict(matches[0])

    @classmethod
    def _event_ref(cls, *, event: dict[str, Any], binding: CalendarBinding) -> str:
        start = event.get("start") or {}
        end = event.get("end") or {}
        start_value = str(start.get("dateTime") or start.get("date") or "")[:64]
        end_value = str(end.get("dateTime") or end.get("date") or "")[:64]
        material = f"{binding.calendar_id}\n{event.get('id') or ''}\n{start_value}\n{end_value}"
        return "calendar_event_v1_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _typed_private_properties(
        *,
        operation_id: str,
        arguments_hash: str,
        current: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        private = dict((((current or {}).get("extendedProperties") or {}).get("private") or {}))
        private.update(
            {
                "jarvisOperationId": operation_id,
                "jarvisArgumentsHash": arguments_hash,
            }
        )
        return private

    def _typed_create_body(
        self,
        *,
        event_id: str,
        operation_id: str,
        arguments_hash: str,
        arguments: dict[str, Any],
        include_invites: bool,
    ) -> dict[str, Any]:
        all_day = bool(arguments.get("all_day"))
        timezone_name = str(arguments.get("timezone") or "UTC")
        body: dict[str, Any] = {
            "id": event_id,
            "summary": str(arguments.get("title") or ""),
            "start": (
                {"date": str(arguments.get("start") or "")}
                if all_day
                else {
                    "dateTime": str(arguments.get("start") or ""),
                    "timeZone": timezone_name,
                }
            ),
            "end": (
                {"date": str(arguments.get("end") or "")}
                if all_day
                else {
                    "dateTime": str(arguments.get("end") or ""),
                    "timeZone": timezone_name,
                }
            ),
            "extendedProperties": {
                "private": self._typed_private_properties(
                    operation_id=operation_id,
                    arguments_hash=arguments_hash,
                )
            },
        }
        for field in ("location", "description"):
            value = str(arguments.get(field) or "").strip()
            if value:
                body[field] = value
        if include_invites:
            body["attendees"] = [
                {"email": str(value)} for value in arguments.get("invitee_emails") or []
            ]
        return body

    def _typed_update_body(
        self,
        *,
        current: dict[str, Any],
        patch: dict[str, Any],
        timezone_name: str,
        operation_id: str,
        arguments_hash: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "extendedProperties": {
                "private": self._typed_private_properties(
                    operation_id=operation_id,
                    arguments_hash=arguments_hash,
                    current=current,
                )
            }
        }
        mappings = {"title": "summary", "location": "location", "description": "description"}
        for source, destination in mappings.items():
            if source in patch:
                body[destination] = str(patch[source])
        if "start" in patch:
            all_day = bool(patch.get("all_day"))
            if all_day:
                body["start"] = {"date": str(patch["start"])}
                body["end"] = {"date": str(patch["end"])}
            else:
                body["start"] = {
                    "dateTime": str(patch["start"]),
                    "timeZone": timezone_name,
                }
                body["end"] = {
                    "dateTime": str(patch["end"]),
                    "timeZone": timezone_name,
                }
        return body

    @classmethod
    def _typed_event_matches(
        cls,
        event: dict[str, Any],
        *,
        expected: dict[str, Any],
        operation_id: str,
        arguments_hash: str,
    ) -> bool:
        private = ((event.get("extendedProperties") or {}).get("private") or {})
        if (
            str(private.get("jarvisOperationId") or "") != operation_id
            or str(private.get("jarvisArgumentsHash") or "") != arguments_hash
        ):
            return False
        for field in ("id", "summary", "start", "end", "location", "description"):
            if field in expected and event.get(field) != expected.get(field):
                return False
        if "attendees" in expected:
            desired = sorted(
                str(item.get("email") or "").casefold()
                for item in expected.get("attendees") or []
            )
            actual = sorted(
                str(item.get("email") or "").casefold()
                for item in event.get("attendees") or []
                if isinstance(item, dict)
            )
            if desired != actual:
                return False
        return True

    @staticmethod
    def _execute_conditional(request: Any, *, revision: str) -> Any:
        headers = getattr(request, "headers", None)
        if not isinstance(headers, dict) or not str(revision or ""):
            raise ValueError("calendar_conditional_request_unavailable")
        headers["If-Match"] = revision
        return request.execute()

    def _get_exact_event(
        self,
        *,
        service: Any,
        calendar_id: str,
        event_id: str,
    ) -> tuple[str, dict[str, Any]]:
        try:
            event = service.events().get(
                calendarId=calendar_id,
                eventId=event_id,
            ).execute()
            return "ok", dict(event) if isinstance(event, dict) else {}
        except Exception as exc:
            if self._exception_status_code(exc) == 404:
                return "not_found", {}
            return "unavailable", {}

    def _reconcile_typed_create(
        self,
        *,
        service: Any,
        binding: CalendarBinding,
        timezone_name: str,
        event_id: str,
        expected: dict[str, Any],
        operation_id: str,
        arguments_hash: str,
        original_error: Exception,
    ) -> dict[str, Any]:
        status, event = self._get_exact_event(
            service=service,
            calendar_id=binding.calendar_id,
            event_id=event_id,
        )
        if status == "not_found":
            return self._typed_retryable(type(original_error).__name__)
        if status == "ok" and self._typed_event_matches(
            event,
            expected=expected,
            operation_id=operation_id,
            arguments_hash=arguments_hash,
        ):
            return self._typed_success(
                event=event,
                binding=binding,
                timezone_name=timezone_name,
                operation_id=operation_id,
                action="created",
                idempotent_replay=True,
            )
        if status == "ok":
            return self._typed_conflict("calendar_create_id_conflict")
        return self._typed_retryable(type(original_error).__name__)

    def _reconcile_typed_update(
        self,
        *,
        service: Any,
        binding: CalendarBinding,
        timezone_name: str,
        event_id: str,
        expected: dict[str, Any],
        original_revision: str,
        operation_id: str,
        arguments_hash: str,
        original_error: Exception,
    ) -> dict[str, Any]:
        status, event = self._get_exact_event(
            service=service,
            calendar_id=binding.calendar_id,
            event_id=event_id,
        )
        if status == "not_found":
            return self._typed_conflict("calendar_update_event_missing")
        if status == "ok" and self._typed_event_matches(
            event,
            expected=expected,
            operation_id=operation_id,
            arguments_hash=arguments_hash,
        ):
            return self._typed_success(
                event=event,
                binding=binding,
                timezone_name=timezone_name,
                operation_id=operation_id,
                action="updated",
                idempotent_replay=True,
            )
        if status == "ok" and str(event.get("etag") or "") == original_revision:
            return self._typed_retryable(type(original_error).__name__)
        if status == "ok":
            return self._typed_conflict("calendar_update_revision_conflict")
        return self._typed_retryable(type(original_error).__name__)

    def _typed_success(
        self,
        *,
        event: dict[str, Any],
        binding: CalendarBinding,
        timezone_name: str,
        operation_id: str,
        action: str,
        deleted: bool = False,
        idempotent_replay: bool = False,
    ) -> dict[str, Any]:
        normalized = self._normalize_event(event)
        event_id = str(event.get("id") or normalized.get("google_event_id") or "")
        event_ref = self._event_ref(event=event, binding=binding)
        return {
            "status": "ok",
            "source": "google_live",
            "message": f"Calendar event {action} and provider state verified.",
            "payload": {
                "action": action,
                "sync_status": "synced",
                "provider_event_id": event_id,
                "event_ref": event_ref,
                "calendar_ref": self._calendar_ref(binding.calendar_id),
                "resource_version": self._event_resource_version(str(event.get("etag") or "")),
                "idempotent_replay": idempotent_replay,
                "event": {
                    "title": str(event.get("summary") or normalized.get("title") or ""),
                    "start": str(normalized.get("start_at") or ""),
                    "end": str(normalized.get("end_at") or ""),
                    "all_day": bool((event.get("start") or {}).get("date")),
                    "timezone": timezone_name,
                    "location": str(event.get("location") or ""),
                    "attendee_emails": self._attendee_emails(event),
                    "deleted": deleted,
                },
            },
            "receipt_id": "calendar_receipt:" + operation_id,
            "committed_effect": not idempotent_replay,
        }

    @staticmethod
    def _typed_denied(code: str) -> dict[str, Any]:
        return {
            "status": "policy_denied",
            "message": "The Calendar target or revision is no longer authorized.",
            "denial_reason": str(code or "calendar_write_denied"),
        }

    @staticmethod
    def _typed_retryable(code: str) -> dict[str, Any]:
        return {
            "status": "retryable_error",
            "message": "The Calendar provider result was uncertain; the exact operation can be retried.",
            "error_code": str(code or "calendar_provider_uncertain"),
        }

    @staticmethod
    def _typed_conflict(code: str) -> dict[str, Any]:
        return {
            "status": "error",
            "message": "The Calendar provider state conflicts with this exact operation.",
            "error_code": code,
        }

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
            if normalized_calendar_id.startswith("calendar_target_v1_"):
                resolved = [
                    item
                    for item in bindings
                    if self._calendar_ref(item.calendar_id) == normalized_calendar_id
                ]
                if len(resolved) != 1:
                    return {"status": "error", "error_code": "calendar_binding_missing"}
                normalized_calendar_id = resolved[0].calendar_id
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

    def query_events(
        self,
        *,
        start: str,
        end: str,
        calendar_scope: str,
        text: str | None = None,
        order: str = "oldest",
        limit: int = 20,
    ) -> dict[str, Any]:
        config = self._load_permissions()
        calendar_cfg = config.get("calendar") or {}
        oauth_cfg = config.get("oauth") or {}
        bindings = self._calendar_bindings(calendar_cfg)
        timezone_name = str(calendar_cfg.get("default_timezone") or "UTC").strip() or "UTC"
        requested_scope = str(calendar_scope or "").strip()
        normalized_order = str(order or "oldest").strip().casefold()
        normalized_text = str(text or "").strip()
        try:
            ZoneInfo(timezone_name)
        except Exception:
            return self._query_error_result(
                status="error",
                message="The configured Calendar timezone is invalid.",
                start=start,
                end=end,
                timezone_name="UTC",
                requested_scope=requested_scope,
            )
        if not bindings:
            return self._query_error_result(
                status="error",
                message="No authorized Calendar scopes are configured.",
                start=start,
                end=end,
                timezone_name=timezone_name,
                requested_scope=requested_scope,
            )

        binding, candidates, is_default = self._resolve_query_binding(
            calendar_scope=requested_scope,
            bindings=bindings,
            config=config,
            calendar_cfg=calendar_cfg,
        )
        if binding is None:
            return {
                "status": "needs_input",
                "message": "Choose one exact authorized Calendar scope.",
                "missing_fields": ["calendar_scope"],
                "untrusted": True,
                "payload": self._query_payload(
                    events=[],
                    start=start,
                    end=end,
                    timezone_name=timezone_name,
                    requested_scope=requested_scope,
                    display_name=requested_scope or "Calendar",
                    resolved=False,
                    is_default=False,
                    candidates=candidates,
                    source_kind="google_calendar_live",
                    synchronized=False,
                    coverage_complete=False,
                    truncated=False,
                ),
            }

        scopes = self._oauth_scopes(oauth_cfg=oauth_cfg, include_write=False)
        token_store_raw = str(oauth_cfg.get("token_store_path") or "data/google_tokens.json")
        token_store_path = self._resolve_path(token_store_raw, prefer_existing=False)
        token_store = self._load_token_store(token_store_path)
        changed = False
        try:
            credentials, token_store, changed = self._load_or_authorize_credentials(
                oauth_cfg=oauth_cfg,
                account_key=self._resolve_account_key(binding, config),
                scopes=scopes,
                token_store=token_store,
                allow_interactive=False,
            )
            service = self._build_calendar_service(credentials)
            request_arguments: dict[str, Any] = {
                "calendarId": binding.calendar_id,
                "timeMin": start,
                "timeMax": end,
                "singleEvents": True,
                "orderBy": "startTime",
                "maxResults": min(101, limit + 1),
                "timeZone": timezone_name,
                "showDeleted": False,
            }
            if normalized_text:
                request_arguments["q"] = normalized_text
            response = service.events().list(**request_arguments).execute()
            if changed:
                self._save_token_store(token_store_path, token_store)
        except Exception:
            if changed:
                try:
                    self._save_token_store(token_store_path, token_store)
                except Exception:
                    pass
            return self._query_error_result(
                status="retryable_error",
                message="The live Calendar provider was unavailable.",
                start=start,
                end=end,
                timezone_name=timezone_name,
                requested_scope=requested_scope,
                display_name=binding.person_name,
                resolved=True,
                is_default=is_default,
            )

        raw_items = response.get("items", []) if isinstance(response, dict) else []
        projected = [
            self._query_event_projection(event=event, binding=binding)
            for event in raw_items
            if isinstance(event, dict)
            and str(event.get("status") or "confirmed").strip().casefold() != "cancelled"
        ]
        # Google already guarantees chronological order for singleEvents +
        # orderBy=startTime. Preserve that provider order (including its
        # all-day/timed-event semantics) and reverse it for the bounded
        # newest-first projection instead of re-sorting ISO strings locally.
        if normalized_order == "newest":
            projected.reverse()
        truncated = len(projected) > limit or bool(
            isinstance(response, dict) and str(response.get("nextPageToken") or "").strip()
        )
        events = projected[:limit]
        return {
            "status": "ok",
            "message": f"Found {len(events)} live Calendar event(s).",
            "untrusted": True,
            "payload": self._query_payload(
                events=events,
                start=start,
                end=end,
                timezone_name=timezone_name,
                requested_scope=requested_scope,
                display_name=binding.person_name,
                resolved=True,
                is_default=is_default,
                candidates=[],
                source_kind="google_calendar_live",
                synchronized=True,
                coverage_complete=not truncated,
                truncated=truncated,
            ),
        }

    @classmethod
    def _resolve_query_binding(
        cls,
        *,
        calendar_scope: str,
        bindings: list[CalendarBinding],
        config: dict[str, Any],
        calendar_cfg: dict[str, Any],
    ) -> tuple[CalendarBinding | None, list[str], bool]:
        scope_key = cls._query_scope_key(calendar_scope)
        if scope_key in {"default", "house", "home", "household", "my", "our"}:
            return cls._select_host_binding(bindings=bindings, calendar_cfg=calendar_cfg), [], True

        matches: dict[tuple[str, str], CalendarBinding] = {}
        for binding in bindings:
            if cls._query_scope_key(binding.person_name) == scope_key:
                matches[(binding.person_name.casefold(), binding.calendar_id)] = binding

        aliases_cfg = (config.get("contacts") or {}).get("aliases") or []
        for item in aliases_cfg:
            if not isinstance(item, dict):
                continue
            canonical_name = str(item.get("name") or "").strip()
            alias_values = [canonical_name]
            if isinstance(item.get("aliases"), list):
                alias_values.extend(str(value).strip() for value in item["aliases"])
            if not any(cls._query_scope_key(value) == scope_key for value in alias_values if value):
                continue
            for binding in bindings:
                if cls._query_scope_key(binding.person_name) == cls._query_scope_key(canonical_name):
                    matches[(binding.person_name.casefold(), binding.calendar_id)] = binding

        if len(matches) == 1:
            return next(iter(matches.values())), [], False
        if matches:
            candidates = sorted({binding.person_name for binding in matches.values()}, key=str.casefold)
            return None, candidates[:10], False
        candidates = sorted({binding.person_name for binding in bindings}, key=str.casefold)
        return None, candidates[:10], False

    @staticmethod
    def _query_scope_key(value: str) -> str:
        normalized = re.sub(
            r"(?:['\u2019]s)\b",
            "",
            str(value or ""),
            flags=re.IGNORECASE,
        )
        normalized = re.sub(r"\bcalendar\b", "", normalized, flags=re.IGNORECASE)
        return re.sub(r"[^a-z0-9]+", "", normalized.casefold())

    @classmethod
    def _query_event_projection(
        cls,
        *,
        event: dict[str, Any],
        binding: CalendarBinding,
    ) -> dict[str, Any]:
        start = event.get("start") or {}
        end = event.get("end") or {}
        start_value = str(start.get("dateTime") or start.get("date") or "")[:64]
        end_value = str(end.get("dateTime") or end.get("date") or "")[:64]
        return {
            "event_ref": cls._event_ref(event=event, binding=binding),
            "calendar_ref": cls._calendar_ref(binding.calendar_id),
            "resource_version": cls._event_resource_version(str(event.get("etag") or "")),
            "title": cls._bounded_query_text(event.get("summary"), 200, "(untitled event)"),
            "start": start_value,
            "end": end_value,
            "all_day": bool(start.get("date") and not start.get("dateTime")),
            "location": cls._bounded_query_text(event.get("location"), 300, ""),
            "calendar_name": cls._bounded_query_text(binding.person_name, 100, "Calendar"),
        }

    @classmethod
    def _query_error_result(
        cls,
        *,
        status: str,
        message: str,
        start: str,
        end: str,
        timezone_name: str,
        requested_scope: str,
        display_name: str | None = None,
        resolved: bool = False,
        is_default: bool = False,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "message": message,
            "untrusted": True,
            "payload": cls._query_payload(
                events=[],
                start=start,
                end=end,
                timezone_name=timezone_name,
                requested_scope=requested_scope,
                display_name=display_name or requested_scope or "Calendar",
                resolved=resolved,
                is_default=is_default,
                candidates=[],
                source_kind="google_calendar_live",
                synchronized=False,
                coverage_complete=False,
                truncated=False,
            ),
        }

    @classmethod
    def _query_payload(
        cls,
        *,
        events: list[dict[str, Any]],
        start: str,
        end: str,
        timezone_name: str,
        requested_scope: str,
        display_name: str,
        resolved: bool,
        is_default: bool,
        candidates: list[str],
        source_kind: str,
        synchronized: bool,
        coverage_complete: bool,
        truncated: bool,
    ) -> dict[str, Any]:
        return {
            "events": events,
            "normalized_range": {
                "start": start,
                "end": end,
                "timezone": cls._bounded_query_text(timezone_name, 64, "UTC"),
            },
            "calendar_scope": {
                "requested": cls._bounded_query_text(requested_scope, 100, "default"),
                "display_name": cls._bounded_query_text(display_name, 100, "Calendar"),
                "resolved": resolved,
                "is_default": is_default,
                "candidates": [cls._bounded_query_text(item, 100, "Calendar") for item in candidates[:10]],
            },
            "source": {
                "kind": source_kind,
                "synchronized": synchronized,
                "coverage_complete": coverage_complete,
                "queried_at": datetime.now(timezone.utc).isoformat(),
            },
            "truncated": truncated,
        }

    @staticmethod
    def _bounded_query_text(value: Any, limit: int, default: str) -> str:
        normalized = " ".join(str(value or "").split()).strip()
        return (normalized or default)[:limit]

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
