from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, TYPE_CHECKING, Callable, Protocol
from uuid import uuid4

from app.core.types import SessionOwner, SessionState

if TYPE_CHECKING:
    from app.context.types import SessionContextState


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionRecord:
    session_id: str
    user_id: str
    source: str
    state: SessionState = SessionState.IDLE
    owner: SessionOwner = SessionOwner.SYSTEM
    context_reference: dict[str, Any] = field(default_factory=dict)
    last_activity_timestamp: str = field(default_factory=_utc_now)

    def touch(self) -> None:
        self.last_activity_timestamp = _utc_now()

    def context_state(self) -> "SessionContextState":
        from app.context.serialization import deserialize_session_context

        return deserialize_session_context(self.context_reference)

    def set_context_state(self, state: "SessionContextState") -> None:
        from app.context.serialization import serialize_session_context

        self.context_reference = serialize_session_context(state)

    def legacy_context_view(self) -> dict[str, Any]:
        from app.context.serialization import session_context_to_legacy_compat_dict

        return session_context_to_legacy_compat_dict(self.context_state())


@dataclass
class ChannelSessionBinding:
    channel_key: str
    session_id: str
    last_activity_monotonic: float
    last_activity_timestamp: str
    expires_at_timestamp: str


class SessionPersistence(Protocol):
    def upsert_session(
        self,
        session_id: str,
        user_id: str,
        source: str,
        state: str,
        owner: str,
        last_activity_timestamp: str,
        context_reference: dict[str, Any] | None = None,
        context_version: int | None = None,
    ) -> None:
        """Persist the latest session snapshot."""

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Load one persisted session snapshot."""


class SessionStore:
    def __init__(
        self,
        persistence: SessionPersistence | None = None,
        *,
        channel_idle_timeout_seconds: float = 180.0,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._channel_bindings: dict[str, ChannelSessionBinding] = {}
        self._persistence = persistence
        self._channel_idle_timeout_seconds = max(float(channel_idle_timeout_seconds), 1.0)
        self._time_fn = time_fn or monotonic

    def get_or_create(
        self,
        session_id: str | None,
        user_id: str,
        source: str,
        *,
        channel_key: str | None = None,
        force_new_for_channel: bool = False,
    ) -> SessionRecord:
        now = float(self._time_fn())
        self._sweep_expired_channel_bindings(now)
        now_dt = datetime.now(timezone.utc)
        now_timestamp = now_dt.isoformat()
        expires_at = (now_dt + timedelta(seconds=self._channel_idle_timeout_seconds)).isoformat()
        resolved_id = str(session_id).strip() if isinstance(session_id, str) and session_id.strip() else ""
        if channel_key and channel_key.strip():
            channel_key = channel_key.strip()
            binding = self._channel_bindings.get(channel_key)
            binding_expired = False
            if binding is not None:
                binding_expired = (now - binding.last_activity_monotonic) > self._channel_idle_timeout_seconds

            if force_new_for_channel or binding is None or binding_expired:
                if not resolved_id or force_new_for_channel or binding_expired:
                    resolved_id = str(uuid4())
                self._channel_bindings[channel_key] = ChannelSessionBinding(
                    channel_key=channel_key,
                    session_id=resolved_id,
                    last_activity_monotonic=now,
                    last_activity_timestamp=now_timestamp,
                    expires_at_timestamp=expires_at,
                )
            else:
                resolved_id = binding.session_id
                binding.last_activity_monotonic = now
                binding.last_activity_timestamp = now_timestamp
                binding.expires_at_timestamp = expires_at

        if not resolved_id:
            resolved_id = str(uuid4())
        session = self._sessions.get(resolved_id)
        if session is None:
            session = self._load_from_persistence(
                session_id=resolved_id,
                fallback_user_id=user_id,
                fallback_source=source,
            )
            if session is None:
                session = SessionRecord(session_id=resolved_id, user_id=user_id, source=source)
            self._sessions[resolved_id] = session
        session.touch()
        self.save(session)
        return session

    def get(self, session_id: str | None) -> SessionRecord | None:
        resolved_id = str(session_id).strip() if isinstance(session_id, str) else ""
        if not resolved_id:
            return None
        existing = self._sessions.get(resolved_id)
        if existing is not None:
            return existing
        loaded = self._load_from_persistence(
            session_id=resolved_id,
            fallback_user_id="unknown_user",
            fallback_source="unknown_source",
        )
        if loaded is None:
            return None
        self._sessions[resolved_id] = loaded
        return loaded

    def reset(self) -> None:
        self._sessions.clear()
        self._channel_bindings.clear()

    def save(self, session: SessionRecord) -> None:
        if self._persistence is None:
            return
        self._persistence.upsert_session(
            session_id=session.session_id,
            user_id=session.user_id,
            source=session.source,
            state=session.state.value,
            owner=session.owner.value,
            last_activity_timestamp=session.last_activity_timestamp,
            context_reference=session.context_reference,
            context_version=self._context_version(session.context_reference),
        )

    def channel_status(self, channel_key: str | None) -> dict[str, Any] | None:
        key = str(channel_key).strip() if isinstance(channel_key, str) else ""
        if not key:
            return None
        binding = self._channel_bindings.get(key)
        if binding is None:
            return None
        now = float(self._time_fn())
        idle_seconds = max(0.0, now - binding.last_activity_monotonic)
        expires_in_seconds = max(0.0, self._channel_idle_timeout_seconds - idle_seconds)
        expired = idle_seconds > self._channel_idle_timeout_seconds
        return {
            "channel_key": binding.channel_key,
            "session_id": binding.session_id,
            "last_activity_at": binding.last_activity_timestamp,
            "expires_at": binding.expires_at_timestamp,
            "idle_timeout_seconds": self._channel_idle_timeout_seconds,
            "expires_in_seconds": round(expires_in_seconds, 3),
            "expired": expired,
        }

    def sweep_expired_channel_bindings(self) -> int:
        now = float(self._time_fn())
        return self._sweep_expired_channel_bindings(now)

    def _sweep_expired_channel_bindings(self, now: float) -> int:
        expired_keys = [
            key
            for key, binding in self._channel_bindings.items()
            if (now - binding.last_activity_monotonic) > self._channel_idle_timeout_seconds
        ]
        for key in expired_keys:
            self._channel_bindings.pop(key, None)
        return len(expired_keys)

    def _load_from_persistence(
        self,
        *,
        session_id: str,
        fallback_user_id: str,
        fallback_source: str,
    ) -> SessionRecord | None:
        if self._persistence is None:
            return None
        loader = getattr(self._persistence, "get_session", None)
        if not callable(loader):
            return None
        try:
            persisted = loader(session_id)
        except Exception:
            return None
        if not isinstance(persisted, dict):
            return None

        persisted_session_id = str(persisted.get("session_id") or session_id).strip() or session_id
        persisted_user_id = str(persisted.get("user_id") or "").strip() or fallback_user_id
        persisted_source = str(persisted.get("source") or "").strip() or fallback_source
        persisted_state = self._coerce_state(persisted.get("state"))
        persisted_owner = self._coerce_owner(persisted.get("owner"))
        persisted_timestamp = str(persisted.get("last_activity_timestamp") or "").strip() or _utc_now()
        context_reference = self._coerce_context_reference(persisted)

        return SessionRecord(
            session_id=persisted_session_id,
            user_id=persisted_user_id,
            source=persisted_source,
            state=persisted_state,
            owner=persisted_owner,
            context_reference=context_reference,
            last_activity_timestamp=persisted_timestamp,
        )

    @staticmethod
    def _coerce_context_reference(payload: dict[str, Any]) -> dict[str, Any]:
        context_reference = payload.get("context_reference")
        if isinstance(context_reference, dict):
            normalized = dict(context_reference)
            if "context_version" not in normalized:
                context_version = payload.get("context_version")
                if isinstance(context_version, int):
                    normalized["context_version"] = context_version
                elif isinstance(context_version, float):
                    normalized["context_version"] = int(context_version)
            return normalized

        raw_context_json = payload.get("context_reference_json")
        if isinstance(raw_context_json, dict):
            normalized = dict(raw_context_json)
            if "context_version" not in normalized:
                context_version = payload.get("context_version")
                if isinstance(context_version, int):
                    normalized["context_version"] = context_version
                elif isinstance(context_version, float):
                    normalized["context_version"] = int(context_version)
            return normalized
        if isinstance(raw_context_json, str):
            try:
                loaded = json.loads(raw_context_json)
            except json.JSONDecodeError:
                loaded = None
            if isinstance(loaded, dict):
                if "context_version" not in loaded:
                    context_version = payload.get("context_version")
                    if isinstance(context_version, int):
                        loaded["context_version"] = context_version
                    elif isinstance(context_version, float):
                        loaded["context_version"] = int(context_version)
                return loaded

        return {}

    @staticmethod
    def _coerce_state(value: Any) -> SessionState:
        normalized = str(value or "").strip().lower()
        for candidate in SessionState:
            if candidate.value == normalized:
                return candidate
        return SessionState.IDLE

    @staticmethod
    def _coerce_owner(value: Any) -> SessionOwner:
        normalized = str(value or "").strip().lower()
        for candidate in SessionOwner:
            if candidate.value == normalized:
                return candidate
        return SessionOwner.SYSTEM

    @staticmethod
    def _context_version(context_reference: dict[str, Any]) -> int:
        raw = context_reference.get("context_version")
        if isinstance(raw, int):
            return max(0, raw)
        if isinstance(raw, float):
            return max(0, int(raw))
        if isinstance(raw, str):
            cleaned = raw.strip()
            if cleaned:
                try:
                    return max(0, int(cleaned))
                except ValueError:
                    return 0
        return 0
