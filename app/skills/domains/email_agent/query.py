from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.skills.tool_contracts import (
    ToolArgumentCanonicalizationError,
    ToolCallEnvelope,
    thaw_json,
)


EMAIL_QUERY_VISIBILITIES = frozenset(
    {"active", "unseen", "needs_reply", "completed", "spam", "all"}
)
EMAIL_QUERY_ORDERS = frozenset({"oldest", "newest"})
EMAIL_TYPED_READ_TOOLS = frozenset(
    {
        "email.query_messages",
        "email.get_message",
        "email.get_thread",
        "email.summarize",
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
    if value is None:
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
    start: datetime
    end: datetime
    timezone_name: str
    senders: tuple[str, ...] = ()
    recipients: tuple[str, ...] = ()
    source: str | None = None
    category: str | None = None
    visibility: str = "active"
    text: str | None = None
    has_attachment: bool | None = None
    order: str = "newest"
    limit: int = 10

    def __post_init__(self) -> None:
        normalized_start = _aware_instant(self.start, field="start")
        normalized_end = _aware_instant(self.end, field="end")
        timezone_name = str(self.timezone_name or "").strip()
        _zone(timezone_name)
        if normalized_start >= normalized_end:
            raise EmailQueryError("email_query_interval_reversed")
        visibility = str(self.visibility or "").strip().casefold()
        if visibility not in EMAIL_QUERY_VISIBILITIES:
            raise EmailQueryError("email_query_visibility_invalid")
        order = str(self.order or "").strip().casefold()
        if order not in EMAIL_QUERY_ORDERS:
            raise EmailQueryError("email_query_order_invalid")
        if not isinstance(self.limit, int) or isinstance(self.limit, bool) or not 1 <= self.limit <= 100:
            raise EmailQueryError("email_query_limit_invalid")
        if self.has_attachment is not None and not isinstance(self.has_attachment, bool):
            raise EmailQueryError("email_query_attachment_filter_invalid")
        object.__setattr__(self, "start", normalized_start)
        object.__setattr__(self, "end", normalized_end)
        object.__setattr__(self, "timezone_name", timezone_name)
        object.__setattr__(self, "senders", _bounded_emails(self.senders, field="senders"))
        object.__setattr__(self, "recipients", _bounded_emails(self.recipients, field="recipients"))
        object.__setattr__(self, "source", str(self.source or "").strip().casefold() or None)
        object.__setattr__(self, "category", str(self.category or "").strip().casefold() or None)
        object.__setattr__(self, "visibility", visibility)
        object.__setattr__(self, "text", _compact_text(self.text, maximum=200))
        object.__setattr__(self, "order", order)

    @classmethod
    def from_arguments(
        cls,
        arguments: Mapping[str, Any],
        *,
        timezone_name: str,
        allowed_sources: Iterable[str],
        allowed_categories: Iterable[str],
    ) -> EmailQuery:
        if not isinstance(arguments, Mapping):
            raise EmailQueryError("email_query_arguments_invalid")
        allowed_fields = {
            "start",
            "end",
            "senders",
            "recipients",
            "source",
            "category",
            "visibility",
            "text",
            "has_attachment",
            "order",
            "limit",
        }
        if set(arguments) - allowed_fields or not {"start", "end"}.issubset(arguments):
            raise EmailQueryError("email_query_arguments_shape_invalid")
        return cls(
            start=_aware_instant(arguments.get("start"), field="start"),
            end=_aware_instant(arguments.get("end"), field="end"),
            timezone_name=timezone_name,
            senders=_bounded_emails(arguments.get("senders"), field="senders"),
            recipients=_bounded_emails(arguments.get("recipients"), field="recipients"),
            source=_allowlisted_value(
                arguments.get("source"),
                allowed=allowed_sources,
                field="source",
            ),
            category=_allowlisted_value(
                arguments.get("category"),
                allowed=allowed_categories,
                field="category",
            ),
            visibility=str(arguments.get("visibility") or "active"),
            text=_compact_text(arguments.get("text"), maximum=200),
            has_attachment=arguments.get("has_attachment"),
            order=str(arguments.get("order") or "newest"),
            limit=arguments.get("limit", 10),
        )

    @property
    def start_internal_date(self) -> int:
        return int(self.start.timestamp() * 1000)

    @property
    def end_internal_date(self) -> int:
        return int(self.end.timestamp() * 1000)

    @property
    def text_terms(self) -> tuple[str, ...]:
        if not self.text:
            return ()
        return tuple(dict.fromkeys(item.casefold() for item in self.text.split() if item))[:20]

    def to_arguments(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "start": _iso_utc(self.start),
            "end": _iso_utc(self.end),
            "visibility": self.visibility,
            "order": self.order,
            "limit": self.limit,
        }
        if self.senders:
            result["senders"] = list(self.senders)
        if self.recipients:
            result["recipients"] = list(self.recipients)
        if self.source is not None:
            result["source"] = self.source
        if self.category is not None:
            result["category"] = self.category
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
            if normalized_tool_id == "email.query_messages":
                return self._query_from_arguments(arguments).to_arguments()
            if normalized_tool_id in {"email.get_message", "email.get_thread"}:
                result: dict[str, Any] = {
                    "message_ref": self._reference(arguments.get("message_ref"))
                }
                if normalized_tool_id == "email.get_thread":
                    result["limit"] = self._limit(arguments.get("limit", 50), maximum=50)
                return result
            if normalized_tool_id == "email.summarize":
                references = arguments.get("message_refs")
                if not isinstance(references, (list, tuple)) or not 1 <= len(references) <= 50:
                    raise EmailQueryError("email_query_message_refs_invalid")
                normalized_references = tuple(self._reference(item) for item in references)
                if len(normalized_references) != len(set(normalized_references)):
                    raise EmailQueryError("email_query_message_refs_duplicate")
                result = {"message_refs": list(normalized_references)}
                focus = re.sub(r"\s+", " ", str(arguments.get("focus") or "")).strip()
                if focus:
                    if len(focus) > 200:
                        raise EmailQueryError("email_query_focus_too_long")
                    result["focus"] = focus
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
            if envelope.tool_id == "email.query_messages":
                return self._query_messages(arguments=arguments, envelope=envelope)
            if envelope.tool_id == "email.get_message":
                return self._get_message(arguments=arguments, envelope=envelope)
            if envelope.tool_id == "email.get_thread":
                return self._get_thread(arguments=arguments, envelope=envelope)
            if envelope.tool_id == "email.summarize":
                return self._summarize(arguments=arguments, envelope=envelope)
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
            allowed_sources=(item.route_key for item in self._permissions.source_routes),
            allowed_categories=self._permissions.category_keys,
        )

    def _query_messages(
        self,
        *,
        arguments: dict[str, Any],
        envelope: ToolCallEnvelope,
    ) -> dict[str, Any]:
        query = self._query_from_arguments(arguments)
        now = self._now()
        rows = self._storage.query_messages(
            query=query,
            taxonomy_version=self._permissions.taxonomy_version,
            user_id=envelope.user_id,
            discord_channel_id=envelope.channel_scope,
            allowed_source_keys=tuple(item.route_key for item in self._permissions.source_routes),
            allowed_category_keys=tuple(sorted(self._permissions.category_keys)),
            now=_iso_utc(now),
        )
        candidates = rows[: query.limit]
        projected = self._bounded_messages(candidates)
        truncated = len(rows) > query.limit or len(projected) < len(candidates)
        if projected:
            projected_rows = candidates[: len(projected)]
            reference_set = self._create_reference_set(
                rows=projected_rows,
                user_id=envelope.user_id,
                channel_id=envelope.channel_scope,
                query_text=(
                    f"typed:{_iso_utc(query.start)}:{_iso_utc(query.end)}:{query.visibility}"
                ),
                now=now,
            )
            projected = self._bounded_messages(projected_rows, reference_set=reference_set)
        source, freshness_at = self._projection_metadata(now=now)
        return {
            "status": "ok",
            "message": (
                f"Found {len(projected)} projected email message(s)."
                if projected
                else "No projected email matched the typed query."
            ),
            "payload": {
                "messages": projected,
                "normalized_query": query.normalized(returned_count=len(projected)),
                "source": source,
                "freshness_at": freshness_at,
                "truncated": truncated,
            },
            "untrusted": True,
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
            return {"status": "error", "message": "That current Email reference is unavailable."}
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
                "message": self._message(row, index=1, reference_set=reference_set),
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
        resolved = self._resolve_reference(
            reference=self._reference(arguments.get("message_ref")),
            user_id=envelope.user_id,
            channel_id=envelope.channel_scope,
        )
        if resolved is None:
            return {"status": "error", "message": "That current Email reference is unavailable."}
        limit = self._limit(arguments.get("limit", 50), maximum=50)
        rows = self._storage.get_thread(
            gmail_thread_id=str(resolved.get("gmail_thread_id") or ""),
            taxonomy_version=self._permissions.taxonomy_version,
            limit=min(limit + 1, 51),
        )
        candidates = rows[:limit]
        projected = self._bounded_messages(candidates)
        truncated = len(rows) > limit or len(projected) < len(candidates)
        now = self._now()
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
        return {
            "status": "ok",
            "message": f"Retrieved {len(projected)} projected thread message(s).",
            "payload": {
                "messages": projected,
                "thread_ref": "thread_" + self._opaque(str(resolved.get("gmail_thread_id") or "")),
                "source": source,
                "freshness_at": freshness_at,
                "truncated": truncated,
            },
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
                },
                "source": source,
                "freshness_at": freshness_at,
                "sync_state": sync_state,
            },
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
        for index, row in enumerate(rows[:100], start=1):
            projected = self._message(row, index=index, reference_set=reference_set)
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
        return {
            "message_ref": f"E{index}",
            "thread_ref": "thread_" + self._opaque(str(row.get("gmail_thread_id") or "")),
            "received_at": received_at,
            "sender": str(row.get("sender_email") or row.get("sender_name") or "unknown")[:320],
            "recipients": [str(item)[:320] for item in (recipients or [])[:10]],
            "subject": re.sub(r"\s+", " ", str(row.get("subject") or "(no subject)"))[:300],
            "snippet": re.sub(r"\s+", " ", str(row.get("snippet") or ""))[:500],
            "summary": re.sub(r"\s+", " ", str(row.get("summary_text") or ""))[:700],
            "source": str(row.get("source_route_key") or "unknown")[:64],
            "category": str(row.get("logical_category_key") or "needs_review")[:64],
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
