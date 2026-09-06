from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.skills.tool_contracts import (
    ToolArgumentCanonicalizationError,
    ToolCallEnvelope,
    canonical_json,
    thaw_json,
)
from app.skills.domains.email_agent.catalog import EmailCatalogService


EMAIL_QUERY_VISIBILITIES = frozenset(
    {"active", "unseen", "needs_reply", "completed", "spam", "all"}
)
EMAIL_QUERY_ORDERS = frozenset({"oldest", "newest"})
EMAIL_LABEL_MATCHES = frozenset({"any", "all"})
EMAIL_TYPED_READ_TOOLS = frozenset(
    {
        "email.list_mailboxes",
        "email.list_labels",
        "email.query_messages",
        "email.get_message",
        "email.get_thread",
        "email.status",
    }
)


class EmailQueryError(ValueError):
    """A content-free validation failure for a typed Email query."""

    def __init__(self, code: str) -> None:
        normalized = str(code or "email_query_invalid").strip().casefold()
        super().__init__(normalized)
        self.code = normalized


def _zone(timezone_name: str) -> ZoneInfo:
    normalized = str(timezone_name or "").strip()
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise EmailQueryError("email_query_timezone_invalid") from exc


def _aware_instant(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise EmailQueryError(f"email_query_{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EmailQueryError(f"email_query_{field}_timezone_missing")
    return parsed.astimezone(UTC).replace(microsecond=0)


def _iso_utc(value: datetime) -> str:
    normalized = value.astimezone(UTC).replace(microsecond=0).isoformat()
    return f"{normalized[:-6]}Z" if normalized.endswith("+00:00") else normalized


def _compact_text(value: Any, *, maximum: int) -> str | None:
    compact = re.sub(r"\s+", " ", str(value or "")).strip()
    if not compact:
        return None
    if len(compact) > maximum:
        raise EmailQueryError("email_query_text_too_long")
    return compact


def _bounded_emails(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None or value == () or value == []:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > 10:
        raise EmailQueryError(f"email_query_{field}_invalid")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        item = str(raw or "").strip().casefold()
        if (
            not item
            or len(item) > 320
            or item.startswith("@")
            or item.endswith("@")
            or item.count("@") != 1
        ):
            raise EmailQueryError(f"email_query_{field}_invalid")
        if item in seen:
            raise EmailQueryError(f"email_query_{field}_duplicate")
        normalized.append(item)
        seen.add(item)
    return tuple(normalized)


def _bounded_values(
    value: Any,
    *,
    field: str,
    maximum_items: int = 10,
    maximum_chars: int = 320,
) -> tuple[str, ...]:
    if value is None or value == () or value == []:
        return ()
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= maximum_items:
        raise EmailQueryError(f"email_query_{field}_invalid")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        item = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not item or len(item) > maximum_chars:
            raise EmailQueryError(f"email_query_{field}_invalid")
        folded = item.casefold()
        if folded in seen:
            raise EmailQueryError(f"email_query_{field}_duplicate")
        normalized.append(item)
        seen.add(folded)
    return tuple(normalized)


def _bounded_domains(value: Any) -> tuple[str, ...]:
    domains = _bounded_values(value, field="sender_domains", maximum_chars=253)
    normalized: list[str] = []
    for raw in domains:
        item = raw.casefold().lstrip("@")
        if not item or "@" in item or "." not in item or item.startswith(".") or item.endswith("."):
            raise EmailQueryError("email_query_sender_domains_invalid")
        normalized.append(item)
    return tuple(normalized)


def _allowlisted_value(
    value: Any,
    *,
    allowed: Iterable[str],
    field: str,
) -> str | None:
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return None
    allowed_values = {
        str(item or "").strip().casefold()
        for item in allowed
        if str(item or "").strip()
    }
    if normalized not in allowed_values:
        raise EmailQueryError(f"email_query_{field}_invalid")
    return normalized


def strict_local_datetime(
    value: datetime,
    *,
    timezone_name: str,
    fold: int | None = None,
) -> datetime:
    """Attach an IANA zone while rejecting ambiguous or nonexistent wall times."""

    if value.tzinfo is not None:
        raise EmailQueryError("email_query_local_datetime_already_aware")
    if fold not in {None, 0, 1}:
        raise EmailQueryError("email_query_local_datetime_fold_invalid")
    zone = _zone(timezone_name)
    candidates: list[datetime] = []
    for candidate_fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=candidate_fold)
        round_trip = candidate.astimezone(UTC).astimezone(zone)
        if (
            round_trip.replace(tzinfo=None) == value
            and round_trip.fold == candidate_fold
            and all(candidate.astimezone(UTC) != item.astimezone(UTC) for item in candidates)
        ):
            candidates.append(candidate)
    if not candidates:
        raise EmailQueryError("email_query_local_datetime_nonexistent")
    if fold is None:
        if len(candidates) != 1:
            raise EmailQueryError("email_query_local_datetime_ambiguous")
        return candidates[0]
    for candidate in candidates:
        if candidate.fold == fold:
            return candidate
    raise EmailQueryError("email_query_local_datetime_fold_invalid")


def exact_local_date_interval(
    local_date: date,
    *,
    timezone_name: str,
) -> tuple[datetime, datetime]:
    """Return [local midnight, next local midnight) as UTC instants."""

    if not isinstance(local_date, date) or isinstance(local_date, datetime):
        raise EmailQueryError("email_query_local_date_invalid")
    start = strict_local_datetime(
        datetime.combine(local_date, time.min),
        timezone_name=timezone_name,
    )
    end = strict_local_datetime(
        datetime.combine(local_date + timedelta(days=1), time.min),
        timezone_name=timezone_name,
    )
    return start.astimezone(UTC), end.astimezone(UTC)


def rolling_days_interval(
    days: int,
    *,
    now: datetime,
    timezone_name: str,
) -> tuple[datetime, datetime]:
    """Return the last N local wall-clock days ending at an injected aware clock."""

    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 3660:
        raise EmailQueryError("email_query_rolling_days_invalid")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise EmailQueryError("email_query_now_timezone_missing")
    zone = _zone(timezone_name)
    local_end = now.astimezone(zone).replace(microsecond=0)
    local_start = strict_local_datetime(
        local_end.replace(tzinfo=None) - timedelta(days=days),
        timezone_name=timezone_name,
        fold=local_end.fold,
    )
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class EmailQuery:
    start: datetime | None
    end: datetime | None
    timezone_name: str
    mailbox_refs: tuple[str, ...] = ()
    sender_addresses: tuple[str, ...] = ()
    sender_domains: tuple[str, ...] = ()
    sender_text: str | None = None
    recipient_addresses: tuple[str, ...] = ()
    label_refs: tuple[str, ...] = ()
    label_match: str = "any"
    classification: str | None = None
    visibility: str = "all"
    text: str | None = None
    has_attachment: bool | None = None
    order: str = "newest"
    limit: int = 20
    cursor_internal_date: int | None = None
    cursor_message_id: str | None = None

    def __post_init__(self) -> None:
        if (self.start is None) != (self.end is None):
            raise EmailQueryError("email_query_interval_pair_required")
        normalized_start = _aware_instant(self.start, field="start") if self.start is not None else None
        normalized_end = _aware_instant(self.end, field="end") if self.end is not None else None
        timezone_name = str(self.timezone_name or "").strip()
        _zone(timezone_name)
        if normalized_start is not None and normalized_end is not None and normalized_start >= normalized_end:
            raise EmailQueryError("email_query_interval_reversed")
        visibility = str(self.visibility or "").strip().casefold()
        if visibility not in EMAIL_QUERY_VISIBILITIES:
            raise EmailQueryError("email_query_visibility_invalid")
        order = str(self.order or "").strip().casefold()
        if order not in EMAIL_QUERY_ORDERS:
            raise EmailQueryError("email_query_order_invalid")
        label_match = str(self.label_match or "").strip().casefold()
        if label_match not in EMAIL_LABEL_MATCHES:
            raise EmailQueryError("email_query_label_match_invalid")
        if not isinstance(self.limit, int) or isinstance(self.limit, bool) or not 1 <= self.limit <= 50:
            raise EmailQueryError("email_query_limit_invalid")
        if self.has_attachment is not None and not isinstance(self.has_attachment, bool):
            raise EmailQueryError("email_query_attachment_filter_invalid")
        object.__setattr__(self, "start", normalized_start)
        object.__setattr__(self, "end", normalized_end)
        object.__setattr__(self, "timezone_name", timezone_name)
        object.__setattr__(self, "mailbox_refs", _bounded_values(self.mailbox_refs, field="mailbox_refs"))
        object.__setattr__(
            self,
            "sender_addresses",
            _bounded_emails(self.sender_addresses, field="sender_addresses"),
        )
        object.__setattr__(self, "sender_domains", _bounded_domains(self.sender_domains))
        object.__setattr__(self, "sender_text", _compact_text(self.sender_text, maximum=200))
        object.__setattr__(
            self,
            "recipient_addresses",
            _bounded_emails(self.recipient_addresses, field="recipient_addresses"),
        )
        object.__setattr__(self, "label_refs", _bounded_values(self.label_refs, field="label_refs"))
        object.__setattr__(self, "label_match", label_match)
        object.__setattr__(
            self,
            "classification",
            str(self.classification or "").strip().casefold() or None,
        )
        object.__setattr__(self, "visibility", visibility)
        object.__setattr__(self, "text", _compact_text(self.text, maximum=200))
        object.__setattr__(self, "order", order)
        if (self.cursor_internal_date is None) != (self.cursor_message_id is None):
            raise EmailQueryError("email_query_cursor_boundary_invalid")
        if self.cursor_internal_date is not None and int(self.cursor_internal_date) < 0:
            raise EmailQueryError("email_query_cursor_boundary_invalid")
        object.__setattr__(
            self,
            "cursor_message_id",
            str(self.cursor_message_id or "").strip() or None,
        )

    @classmethod
    def from_arguments(
        cls,
        arguments: Mapping[str, Any],
        *,
        timezone_name: str,
        allowed_mailbox_selectors: Iterable[str],
        allowed_categories: Iterable[str],
    ) -> EmailQuery:
        if not isinstance(arguments, Mapping):
            raise EmailQueryError("email_query_arguments_invalid")
        if not tuple(str(item or "").strip() for item in allowed_mailbox_selectors if str(item or "").strip()):
            raise EmailQueryError("email_query_mailbox_catalog_empty")
        allowed_fields = {
            "start",
            "end",
            "mailbox_refs",
            "sender_addresses",
            "sender_domains",
            "sender_text",
            "recipient_addresses",
            "label_refs",
            "label_match",
            "classification",
            "visibility",
            "text",
            "has_attachment",
            "order",
            "limit",
        }
        if set(arguments) - allowed_fields or (("start" in arguments) != ("end" in arguments)):
            raise EmailQueryError("email_query_arguments_shape_invalid")
        return cls(
            start=(
                _aware_instant(arguments.get("start"), field="start")
                if "start" in arguments
                else None
            ),
            end=(
                _aware_instant(arguments.get("end"), field="end")
                if "end" in arguments
                else None
            ),
            timezone_name=timezone_name,
            mailbox_refs=_bounded_values(arguments.get("mailbox_refs"), field="mailbox_refs"),
            sender_addresses=_bounded_emails(
                arguments.get("sender_addresses"), field="sender_addresses"
            ),
            sender_domains=_bounded_domains(arguments.get("sender_domains")),
            sender_text=_compact_text(arguments.get("sender_text"), maximum=200),
            recipient_addresses=_bounded_emails(
                arguments.get("recipient_addresses"), field="recipient_addresses"
            ),
            label_refs=_bounded_values(arguments.get("label_refs"), field="label_refs"),
            label_match=str(arguments.get("label_match") or "any"),
            classification=_allowlisted_value(
                arguments.get("classification"),
                allowed=allowed_categories,
                field="classification",
            ),
            visibility=str(arguments.get("visibility") or "all"),
            text=_compact_text(arguments.get("text"), maximum=200),
            has_attachment=arguments.get("has_attachment"),
            order=str(arguments.get("order") or "newest"),
            limit=arguments.get("limit", 20),
        )

    @property
    def start_internal_date(self) -> int:
        if self.start is None:
            raise EmailQueryError("email_query_start_unavailable")
        return int(self.start.timestamp() * 1000)

    @property
    def end_internal_date(self) -> int:
        if self.end is None:
            raise EmailQueryError("email_query_end_unavailable")
        return int(self.end.timestamp() * 1000)

    @property
    def text_terms(self) -> tuple[str, ...]:
        if not self.text:
            return ()
        return tuple(dict.fromkeys(item.casefold() for item in self.text.split() if item))[:20]

    def to_arguments(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "visibility": self.visibility,
            "order": self.order,
            "limit": self.limit,
        }
        if self.start is not None and self.end is not None:
            result["start"] = _iso_utc(self.start)
            result["end"] = _iso_utc(self.end)
        if self.mailbox_refs:
            result["mailbox_refs"] = list(self.mailbox_refs)
        if self.sender_addresses:
            result["sender_addresses"] = list(self.sender_addresses)
        if self.sender_domains:
            result["sender_domains"] = list(self.sender_domains)
        if self.sender_text is not None:
            result["sender_text"] = self.sender_text
        if self.recipient_addresses:
            result["recipient_addresses"] = list(self.recipient_addresses)
        if self.label_refs:
            result["label_refs"] = list(self.label_refs)
            result["label_match"] = self.label_match
        if self.classification is not None:
            result["classification"] = self.classification
        if self.text is not None:
            result["text"] = self.text
        if self.has_attachment is not None:
            result["has_attachment"] = self.has_attachment
        return result

    def normalized(self, *, returned_count: int) -> dict[str, Any]:
        return {
            **self.to_arguments(),
            "timezone": self.timezone_name,
            "returned_count": max(0, int(returned_count)),
        }


class EmailReadToolExecutor:
    """Typed Phase 4 reads over the existing local Email projection."""

    def __init__(
        self,
        *,
        storage: Any,
        permissions: Any,
        timezone_name: str,
        reference_retention_hours: int,
        stale_seconds: int,
        utc_clock: Any | None = None,
    ) -> None:
        self._storage = storage
        self._permissions = permissions
        self._timezone_name = str(timezone_name or "").strip()
        _zone(self._timezone_name)
        self._reference_retention_hours = max(1, min(int(reference_retention_hours), 720))
        self._stale_seconds = max(30, min(int(stale_seconds), 1800))
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))
        self._catalog = EmailCatalogService(permissions=permissions, storage=storage)

    def canonicalize(
        self,
        *,
        tool_id: str,
        validated_arguments: Mapping[str, Any],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_tool_id = str(tool_id or "").strip().casefold()
        if normalized_tool_id not in EMAIL_TYPED_READ_TOOLS:
            raise ToolArgumentCanonicalizationError("email_tool_unsupported")
        if self._permissions.authorize(request_context) is None:
            raise ToolArgumentCanonicalizationError("email_tool_unauthorized")
        arguments = dict(validated_arguments)
        try:
            if normalized_tool_id == "email.list_mailboxes":
                if arguments:
                    raise EmailQueryError("email_query_mailbox_catalog_arguments_invalid")
                return {}
            if normalized_tool_id == "email.list_labels":
                if set(arguments) - {"text"}:
                    raise EmailQueryError("email_query_label_catalog_arguments_invalid")
                text = _compact_text(arguments.get("text"), maximum=100)
                return {"text": text} if text else {}
            if normalized_tool_id == "email.query_messages":
                if "cursor" in arguments:
                    if set(arguments) != {"cursor"}:
                        raise EmailQueryError("email_query_cursor_filters_changed")
                    return {
                        "cursor": self._canonical_cursor(
                            arguments.get("cursor"),
                            request_context=request_context,
                        )
                    }
                return self._query_from_arguments(arguments).to_arguments()
            if normalized_tool_id in {"email.get_message", "email.get_thread"}:
                if normalized_tool_id == "email.get_thread" and "cursor" in arguments:
                    if set(arguments) != {"cursor"}:
                        raise EmailQueryError("email_query_cursor_filters_changed")
                    return {"cursor": self._cursor(arguments.get("cursor"))}
                result: dict[str, Any] = {
                    "message_ref": self._reference(arguments.get("message_ref"))
                }
                if normalized_tool_id == "email.get_thread":
                    result["limit"] = self._limit(arguments.get("limit", 50), maximum=50)
                return result
            if arguments:
                raise EmailQueryError("email_query_status_arguments_invalid")
            return {}
        except EmailQueryError as exc:
            raise ToolArgumentCanonicalizationError(exc.code) from exc

    def execute(self, *, envelope: ToolCallEnvelope) -> dict[str, Any]:
        if not isinstance(envelope, ToolCallEnvelope) or envelope.skill_id != "skill.email.agent":
            return self._denied("email_tool_envelope_invalid")
        if envelope.tool_id not in EMAIL_TYPED_READ_TOOLS:
            return self._denied("email_tool_unsupported")
        if self._envelope_grant(envelope) is None:
            return self._denied("email_tool_scope_changed")
        arguments = thaw_json(envelope.arguments)
        try:
            if envelope.tool_id == "email.list_mailboxes":
                return self._list_mailboxes()
            if envelope.tool_id == "email.list_labels":
                return self._list_labels(arguments=arguments)
            if envelope.tool_id == "email.query_messages":
                return self._query_messages(arguments=arguments, envelope=envelope)
            if envelope.tool_id == "email.get_message":
                return self._get_message(arguments=arguments, envelope=envelope)
            if envelope.tool_id == "email.get_thread":
                return self._get_thread(arguments=arguments, envelope=envelope)
            return self._status()
        except (EmailQueryError, TypeError, ValueError):
            return {
                "status": "error",
                "message": "The typed email read request was invalid.",
            }

    def _query_from_arguments(self, arguments: Mapping[str, Any]) -> EmailQuery:
        return EmailQuery.from_arguments(
            arguments,
            timezone_name=self._timezone_name,
            allowed_mailbox_selectors=(item.route_key for item in self._permissions.source_routes),
            allowed_categories=self._permissions.category_keys,
        )

    def _query_messages(
        self,
        *,
        arguments: dict[str, Any],
        envelope: ToolCallEnvelope,
    ) -> dict[str, Any]:
        now = self._now()
        query = (
            self._query_from_cursor(
                cursor=self._cursor(arguments.get("cursor")),
                envelope=envelope,
                now=now,
            )
            if "cursor" in arguments
            else self._query_from_arguments(arguments)
        )
        mailbox_resolution = self._catalog.resolve_mailboxes(query.mailbox_refs)
        if mailbox_resolution.status != "ok":
            return self._selector_needs_input(
                selector="mailbox_refs",
                candidates=mailbox_resolution.candidates,
            )
        label_resolution = self._catalog.resolve_labels(query.label_refs)
        if label_resolution.status != "ok":
            return self._selector_needs_input(
                selector="label_refs",
                candidates=label_resolution.candidates,
            )
        query = replace(
            query,
            mailbox_refs=mailbox_resolution.canonical_refs,
            label_refs=label_resolution.canonical_refs,
        )
        selected_source_keys = (
            self._catalog.route_keys_for_refs(query.mailbox_refs)
            if query.mailbox_refs
            else tuple(item.route_key for item in self._permissions.source_routes)
        )
        rows = self._storage.query_messages(
            query=query,
            taxonomy_version=self._permissions.taxonomy_version,
            user_id=envelope.user_id,
            discord_channel_id=envelope.channel_scope,
            allowed_source_keys=tuple(item.route_key for item in self._permissions.source_routes),
            allowed_category_keys=tuple(sorted(self._permissions.category_keys)),
            selected_source_keys=selected_source_keys,
            selected_label_refs=query.label_refs,
            now=_iso_utc(now),
        )
        candidates = rows[: query.limit]
        projected = self._bounded_messages(candidates)
        has_more = len(rows) > query.limit
        output_bounded = len(projected) < len(candidates)
        next_cursor: str | None = None
        if has_more and candidates:
            next_cursor = self._create_cursor(
                kind="query",
                state={
                    "arguments": query.to_arguments(),
                    "last_internal_date": int(candidates[-1].get("internal_date") or 0),
                    "last_message_id": str(candidates[-1].get("gmail_message_id") or ""),
                },
                user_id=envelope.user_id,
                channel_id=envelope.channel_scope,
                now=now,
            )
        result_set_ref: str | None = None
        if projected:
            projected_rows = candidates[: len(projected)]
            reference_set = self._create_reference_set(
                rows=projected_rows,
                user_id=envelope.user_id,
                channel_id=envelope.channel_scope,
                query_text="typed:v2:" + canonical_json(query.to_arguments()),
                now=now,
            )
            projected = self._bounded_messages(projected_rows, reference_set=reference_set)
            result_set_ref = "result_v1_" + self._opaque(
                str(reference_set.get("reference_set_id") or "")
            )
        source, freshness_at = self._projection_metadata(now=now)
        coverage = self._coverage(query=query, selected_source_keys=selected_source_keys)
        payload: dict[str, Any] = {
            "messages": projected,
            "normalized_query": query.normalized(returned_count=len(projected)),
            "coverage": coverage,
            "source": source,
            "freshness_at": freshness_at,
            "truncated": has_more or output_bounded,
        }
        if result_set_ref:
            payload["result_set_ref"] = result_set_ref
        if next_cursor:
            payload["next_cursor"] = next_cursor
        return {
            "status": "ok",
            "message": (
                "No projected email matched, and the requested interval is outside indexed coverage."
                if not projected and coverage.get("requested_interval_covered") is False
                else
                f"Found {len(projected)} projected email message(s)."
                if projected
                else "No projected email matched the typed query."
            ),
            "payload": payload,
            "untrusted": True,
        }

    def _list_mailboxes(self) -> dict[str, Any]:
        now = self._now()
        source, freshness_at = self._projection_metadata(now=now)
        return {
            "status": "ok",
            "message": "Returned the authorized routed mailbox catalog.",
            "payload": {
                "mailboxes": self._catalog.mailboxes(),
                "source": source,
                "freshness_at": freshness_at,
                "truncated": False,
            },
        }

    def _list_labels(self, *, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "message": "Returned the enabled Jarvis-managed label catalog.",
            "payload": {
                "labels": self._catalog.labels(text=_compact_text(arguments.get("text"), maximum=100)),
                "truncated": False,
            },
        }

    def _get_message(
        self,
        *,
        arguments: dict[str, Any],
        envelope: ToolCallEnvelope,
    ) -> dict[str, Any]:
        resolved = self._resolve_reference(
            reference=self._reference(arguments.get("message_ref")),
            user_id=envelope.user_id,
            channel_id=envelope.channel_scope,
        )
        if resolved is None:
            return self._reference_needs_input()
        row = self._storage.get_message(
            gmail_message_id=str(resolved["gmail_message_id"]),
            taxonomy_version=self._permissions.taxonomy_version,
        )
        if row is None:
            return {"status": "error", "message": "That projected email is unavailable."}
        now = self._now()
        reference_set = self._create_reference_set(
            rows=[row],
            user_id=envelope.user_id,
            channel_id=envelope.channel_scope,
            query_text="typed:get_message",
            now=now,
        )
        source, freshness_at = self._projection_metadata(now=now)
        return {
            "status": "ok",
            "message": "Retrieved one projected email message.",
            "payload": {
                "message": self._bounded_messages(
                    [row],
                    reference_set=reference_set,
                )[0],
                "source": source,
                "freshness_at": freshness_at,
            },
            "untrusted": True,
        }

    def _get_thread(
        self,
        *,
        arguments: dict[str, Any],
        envelope: ToolCallEnvelope,
    ) -> dict[str, Any]:
        now = self._now()
        after_internal_date: int | None = None
        after_message_id: str | None = None
        if "cursor" in arguments:
            state = self._cursor_state(
                cursor=self._cursor(arguments.get("cursor")),
                kind="thread",
                envelope=envelope,
                now=now,
            )
            resolved = {"gmail_thread_id": str(state.get("gmail_thread_id") or "")}
            limit = self._limit(state.get("limit", 50), maximum=50)
            after_internal_date = int(state.get("last_internal_date") or 0)
            after_message_id = str(state.get("last_message_id") or "")
        else:
            resolved = self._resolve_reference(
                reference=self._reference(arguments.get("message_ref")),
                user_id=envelope.user_id,
                channel_id=envelope.channel_scope,
            )
            if resolved is None:
                return self._reference_needs_input()
            limit = self._limit(arguments.get("limit", 50), maximum=50)
        rows = self._storage.get_thread(
            gmail_thread_id=str(resolved.get("gmail_thread_id") or ""),
            taxonomy_version=self._permissions.taxonomy_version,
            limit=min(limit + 1, 51),
            after_internal_date=after_internal_date,
            after_message_id=after_message_id,
        )
        candidates = rows[:limit]
        projected = self._bounded_messages(candidates)
        truncated = len(rows) > limit or len(projected) < len(candidates)
        next_cursor: str | None = None
        if len(rows) > limit and candidates:
            next_cursor = self._create_cursor(
                kind="thread",
                state={
                    "gmail_thread_id": str(resolved.get("gmail_thread_id") or ""),
                    "limit": limit,
                    "last_internal_date": int(candidates[-1].get("internal_date") or 0),
                    "last_message_id": str(candidates[-1].get("gmail_message_id") or ""),
                },
                user_id=envelope.user_id,
                channel_id=envelope.channel_scope,
                now=now,
            )
        if projected:
            candidates = candidates[: len(projected)]
            reference_set = self._create_reference_set(
                rows=candidates,
                user_id=envelope.user_id,
                channel_id=envelope.channel_scope,
                query_text="typed:get_thread",
                now=now,
            )
            projected = self._bounded_messages(candidates, reference_set=reference_set)
        source, freshness_at = self._projection_metadata(now=now)
        payload: dict[str, Any] = {
            "messages": projected,
            "thread_ref": "thread_" + self._opaque(str(resolved.get("gmail_thread_id") or "")),
            "source": source,
            "freshness_at": freshness_at,
            "truncated": truncated,
        }
        if next_cursor:
            payload["next_cursor"] = next_cursor
        return {
            "status": "ok",
            "message": f"Retrieved {len(projected)} projected thread message(s).",
            "payload": payload,
            "untrusted": True,
        }

    def _summarize(
        self,
        *,
        arguments: dict[str, Any],
        envelope: ToolCallEnvelope,
    ) -> dict[str, Any]:
        raw_references = arguments.get("message_refs")
        if not isinstance(raw_references, list) or not 1 <= len(raw_references) <= 50:
            raise EmailQueryError("email_query_message_refs_invalid")
        references = [self._reference(item) for item in raw_references]
        if len(references) != len(set(references)):
            raise EmailQueryError("email_query_message_refs_duplicate")
        rows: list[dict[str, Any]] = []
        for reference in references:
            resolved = self._resolve_reference(
                reference=reference,
                user_id=envelope.user_id,
                channel_id=envelope.channel_scope,
            )
            if resolved is None:
                return {
                    "status": "error",
                    "message": "One or more current Email references are unavailable.",
                }
            row = self._storage.get_message(
                gmail_message_id=str(resolved["gmail_message_id"]),
                taxonomy_version=self._permissions.taxonomy_version,
            )
            if row is None:
                return {
                    "status": "error",
                    "message": "One or more projected emails are unavailable.",
                }
            rows.append(row)
        now = self._now()
        lines: list[str] = []
        returned_rows: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            subject = re.sub(r"\s+", " ", str(row.get("subject") or "(no subject)"))[:240]
            summary = re.sub(
                r"\s+",
                " ",
                str(row.get("summary_text") or row.get("snippet") or "No preview available."),
            ).strip()[:700]
            candidate = f"E{index}: {subject} — {summary}"
            if len("\n".join([*lines, candidate])) > 6_000:
                break
            lines.append(candidate)
            returned_rows.append(row)
        self._create_reference_set(
            rows=returned_rows,
            user_id=envelope.user_id,
            channel_id=envelope.channel_scope,
            query_text="typed:summarize",
            now=now,
        )
        source, freshness_at = self._projection_metadata(now=now)
        return {
            "status": "ok",
            "message": f"Returned stored summaries for {len(returned_rows)} email message(s).",
            "payload": {
                "summary": "\n".join(lines),
                "message_refs": [f"E{index}" for index in range(1, len(returned_rows) + 1)],
                "source": source,
                "freshness_at": freshness_at,
                "truncated": len(returned_rows) < len(rows),
            },
            "untrusted": True,
        }

    def _status(self) -> dict[str, Any]:
        now = self._now()
        status = self._storage.status()
        source, freshness_at = self._projection_metadata(now=now, status=status)
        sync_state = (
            "not_activated"
            if not status.get("activation_at")
            else ("stale" if source["stale"] else "fresh")
        )
        worker: dict[str, Any] = {
            "status": status.get("operations_worker_status") or "not_started",
        }
        if status.get("operations_worker_last_seen_at"):
            worker["last_seen_at"] = status["operations_worker_last_seen_at"]
        if status.get("operations_worker_last_error_code"):
            worker["last_error_code"] = status["operations_worker_last_error_code"]
        return {
            "status": "ok",
            "message": "Returned content-free Email projection status.",
            "payload": {
                "counts": {
                    "messages": max(0, int(status.get("message_count") or 0)),
                    "needs_review": max(0, int(status.get("needs_review_count") or 0)),
                    "failed_runs": max(0, int(status.get("failed_run_count") or 0)),
                    "dead_letter_messages": max(
                        0, int(status.get("dead_letter_message_count") or 0)
                    ),
                    "managed_label_queued": max(
                        0, int(status.get("managed_label_queued_count") or 0)
                    ),
                    "managed_label_dead_letter": max(
                        0, int(status.get("managed_label_dead_letter_count") or 0)
                    ),
                    "managed_label_verified": max(
                        0, int(status.get("managed_label_verified_count") or 0)
                    ),
                },
                "source": source,
                "freshness_at": freshness_at,
                "sync_state": sync_state,
                "operations_worker": worker,
                "coverage": self._coverage(
                    query=EmailQuery(start=None, end=None, timezone_name=self._timezone_name),
                    selected_source_keys=tuple(
                        item.route_key for item in self._permissions.source_routes
                    ),
                ),
            },
        }

    def _coverage(
        self,
        *,
        query: EmailQuery,
        selected_source_keys: tuple[str, ...],
    ) -> dict[str, Any]:
        coverage = self._storage.projection_coverage(allowed_source_keys=selected_source_keys)
        earliest = self._parse_iso(coverage.get("earliest_indexed_at"))
        latest = self._parse_iso(coverage.get("latest_indexed_at"))
        interval_covered: bool | None = None
        if query.start is not None and query.end is not None:
            interval_covered = (
                earliest is not None
                and latest is not None
                and query.start >= earliest
                and query.end <= latest + timedelta(milliseconds=1)
            )
        result: dict[str, Any] = {
            "message_count": max(0, int(coverage.get("message_count") or 0)),
        }
        if coverage.get("earliest_indexed_at"):
            result["earliest_indexed_at"] = coverage["earliest_indexed_at"]
        if coverage.get("latest_indexed_at"):
            result["latest_indexed_at"] = coverage["latest_indexed_at"]
        if interval_covered is not None:
            result["requested_interval_covered"] = interval_covered
        return result

    @staticmethod
    def _selector_needs_input(
        *,
        selector: str,
        candidates: tuple[dict[str, str], ...],
    ) -> dict[str, Any]:
        return {
            "status": "needs_input",
            "message": "That Email selector was missing or ambiguous; use one of the current candidates.",
            "missing_fields": [selector],
            "payload": {
                "selector": selector,
                "candidates": list(candidates),
            },
        }

    @staticmethod
    def _reference_needs_input() -> dict[str, Any]:
        return {
            "status": "needs_input",
            "message": "That Email reference expired or is unavailable; query the messages again.",
            "missing_fields": ["message_ref"],
            "payload": {"reference_state": "stale"},
        }

    def _envelope_grant(self, envelope: ToolCallEnvelope) -> Any | None:
        if envelope.source_interface.strip().casefold() != "discord":
            return None
        user_id = envelope.user_id.strip().casefold()
        channel_id = envelope.channel_scope.strip()
        agent_id = envelope.agent_id.strip().casefold()
        for grant in self._permissions.access_grants:
            if not grant.enabled or grant.user_id != user_id or grant.discord_channel_id != channel_id:
                continue
            if agent_id not in grant.agent_ids and "all" not in grant.agent_ids:
                continue
            if "shared" in grant.audiences:
                return grant
        return None

    @staticmethod
    def _denied(reason: str) -> dict[str, Any]:
        return {
            "status": "policy_denied",
            "message": "The Email tool is unavailable in this request context.",
            "denial_reason": str(reason or "email_tool_unavailable").strip().casefold(),
        }

    @staticmethod
    def _reference(value: Any) -> str:
        normalized = str(value or "").strip().upper()
        if not re.fullmatch(r"E(?:[1-9]|[1-4][0-9]|50)", normalized):
            raise EmailQueryError("email_query_message_ref_invalid")
        return normalized

    @staticmethod
    def _cursor(value: Any) -> str:
        normalized = str(value or "").strip().casefold()
        if not re.fullmatch(
            r"cursor_v1_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            normalized,
        ):
            raise EmailQueryError("email_query_cursor_invalid")
        return normalized

    def _canonical_cursor(
        self,
        value: Any,
        *,
        request_context: Mapping[str, Any],
    ) -> str:
        normalized = str(value or "").strip().casefold()
        if normalized != "next":
            return self._cursor(normalized)
        row = self._storage.latest_cursor_reference_set(
            kind="query",
            user_id=str(request_context.get("requested_by_user_id") or ""),
            discord_channel_id=str(request_context.get("discord_channel_id") or ""),
            now=_iso_utc(self._now()),
        )
        reference_set_id = str((row or {}).get("reference_set_id") or "").strip()
        if not reference_set_id:
            raise EmailQueryError("email_query_cursor_unavailable")
        return self._cursor("cursor_v1_" + reference_set_id)

    def _query_from_cursor(
        self,
        *,
        cursor: str,
        envelope: ToolCallEnvelope,
        now: datetime,
    ) -> EmailQuery:
        state = self._cursor_state(
            cursor=cursor,
            kind="query",
            envelope=envelope,
            now=now,
        )
        arguments = state.get("arguments")
        if not isinstance(arguments, Mapping):
            raise EmailQueryError("email_query_cursor_invalid")
        query = self._query_from_arguments(arguments)
        return replace(
            query,
            cursor_internal_date=int(state.get("last_internal_date") or -1),
            cursor_message_id=str(state.get("last_message_id") or ""),
        )

    def _cursor_state(
        self,
        *,
        cursor: str,
        kind: str,
        envelope: ToolCallEnvelope,
        now: datetime,
    ) -> dict[str, Any]:
        reference_set_id = cursor.removeprefix("cursor_v1_")
        row = self._storage.get_reference_set(
            reference_set_id=reference_set_id,
            user_id=str(envelope.user_id or "").strip().casefold(),
            discord_channel_id=str(envelope.channel_scope or "").strip(),
            now=_iso_utc(now),
        )
        prefix = f"cursor:{kind}:v1:"
        query_text = str((row or {}).get("query_text") or "")
        if row is None or not query_text.startswith(prefix):
            raise EmailQueryError("email_query_cursor_unavailable")
        try:
            state = json.loads(query_text.removeprefix(prefix))
        except (TypeError, json.JSONDecodeError) as exc:
            raise EmailQueryError("email_query_cursor_invalid") from exc
        if not isinstance(state, dict):
            raise EmailQueryError("email_query_cursor_invalid")
        return state

    def _create_cursor(
        self,
        *,
        kind: str,
        state: dict[str, Any],
        user_id: str,
        channel_id: str,
        now: datetime,
    ) -> str | None:
        encoded = canonical_json(state)
        if len(encoded) > 3_900:
            return None
        reference_set = self._storage.create_reference_set(
            user_id=str(user_id or "").strip().casefold(),
            discord_channel_id=str(channel_id or "").strip(),
            query_text=f"cursor:{kind}:v1:{encoded}",
            message_ids=[],
            thread_ids=[],
            focused_message_id=None,
            focused_thread_id=None,
            created_at=_iso_utc(now),
            expires_at=_iso_utc(now + timedelta(hours=self._reference_retention_hours)),
        )
        return "cursor_v1_" + str(reference_set.get("reference_set_id") or "")

    @staticmethod
    def _limit(value: Any, *, maximum: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
            raise EmailQueryError("email_query_limit_invalid")
        return value

    def _resolve_reference(
        self,
        *,
        reference: str,
        user_id: str,
        channel_id: str,
    ) -> dict[str, Any] | None:
        return self._storage.resolve_reference(
            user_id=str(user_id or "").strip().casefold(),
            discord_channel_id=str(channel_id or "").strip(),
            reference=reference,
            now=_iso_utc(self._now()),
        )

    def _create_reference_set(
        self,
        *,
        rows: list[dict[str, Any]],
        user_id: str,
        channel_id: str,
        query_text: str,
        now: datetime,
    ) -> dict[str, Any]:
        return self._storage.create_reference_set(
            user_id=str(user_id or "").strip().casefold(),
            discord_channel_id=str(channel_id or "").strip(),
            query_text=query_text,
            message_ids=[str(row.get("gmail_message_id") or "") for row in rows],
            thread_ids=[str(row.get("gmail_thread_id") or "") for row in rows],
            focused_message_id=(str(rows[0].get("gmail_message_id") or "") if rows else None),
            focused_thread_id=(str(rows[0].get("gmail_thread_id") or "") if rows else None),
            created_at=_iso_utc(now),
            expires_at=_iso_utc(now + timedelta(hours=self._reference_retention_hours)),
        )

    def _bounded_messages(
        self,
        rows: list[dict[str, Any]],
        *,
        reference_set: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        managed_labels = self._storage.managed_labels_for_messages(
            gmail_message_ids=[str(row.get("gmail_message_id") or "") for row in rows[:100]]
        )
        for index, row in enumerate(rows[:100], start=1):
            projected = self._message(
                row,
                index=index,
                reference_set=reference_set,
                managed_labels=managed_labels.get(str(row.get("gmail_message_id") or ""), []),
            )
            candidate = [*result, projected]
            if len(json.dumps(candidate, ensure_ascii=True, sort_keys=True)) > 6_200:
                break
            result.append(projected)
        return result

    def _message(
        self,
        row: dict[str, Any],
        *,
        index: int,
        reference_set: dict[str, Any] | None,
        managed_labels: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        attachments = row.get("attachment_metadata")
        attachment_names = [
            re.sub(r"\s+", " ", str(item.get("filename") or "attachment"))[:100]
            for item in (attachments if isinstance(attachments, list) else [])[:5]
            if isinstance(item, dict)
        ]
        recipients = row.get("recipient_headers")
        received_at = "unknown"
        try:
            received_at = _iso_utc(
                datetime.fromtimestamp(int(row.get("internal_date")) / 1000, tz=UTC)
            )
        except (TypeError, ValueError, OSError):
            pass
        route_key = str(row.get("source_route_key") or "")
        route = next(
            (item for item in self._permissions.source_routes if item.route_key == route_key),
            None,
        )
        return {
            "message_ref": f"E{index}",
            "thread_ref": "thread_" + self._opaque(str(row.get("gmail_thread_id") or "")),
            "received_at": received_at,
            "sender": str(row.get("sender_email") or row.get("sender_name") or "unknown")[:320],
            "recipients": [str(item)[:320] for item in (recipients or [])[:10]],
            "subject": re.sub(r"\s+", " ", str(row.get("subject") or "(no subject)"))[:300],
            "snippet": re.sub(r"\s+", " ", str(row.get("snippet") or ""))[:500],
            "summary": re.sub(r"\s+", " ", str(row.get("summary_text") or ""))[:700],
            "mailbox": {
                "mailbox_ref": self._catalog.mailbox_ref(route_key),
                "display_name": str(getattr(route, "display_name", "") or "Unknown")[:100],
            },
            "classification": str(row.get("logical_category_key") or "needs_review")[:64],
            "managed_labels": list(managed_labels or [])[:10],
            "has_attachment": bool(attachment_names),
            "attachment_names": attachment_names,
            "reference_set_ref": (
                "refset_" + self._opaque(str(reference_set.get("reference_set_id") or ""))
                if reference_set
                else "pending"
            ),
        }

    def _projection_metadata(
        self,
        *,
        now: datetime,
        status: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        projection_status = status if isinstance(status, dict) else self._storage.status()
        freshness_at = str(
            projection_status.get("last_success_at")
            or projection_status.get("updated_at")
            or projection_status.get("activation_at")
            or "unavailable"
        )
        parsed = self._parse_iso(projection_status.get("last_success_at"))
        stale = parsed is None or (now - parsed).total_seconds() >= self._stale_seconds
        return {"kind": "email_sqlite_projection", "stale": stale}, freshness_at

    def _now(self) -> datetime:
        current = self._utc_clock()
        if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
            raise EmailQueryError("email_query_clock_invalid")
        return current.astimezone(UTC).replace(microsecond=0)

    @staticmethod
    def _parse_iso(value: Any) -> datetime | None:
        raw = str(value or "").strip()
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC)

    @staticmethod
    def _opaque(value: str) -> str:
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]
