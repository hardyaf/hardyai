from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from app.context.serialization import deserialize_session_context, serialize_session_context
from app.context.types import PendingInteraction

if TYPE_CHECKING:
    from app.core.session_store import SessionRecord


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PendingInteractionManager:
    def __init__(self, *, default_ttl_seconds: float | None = 1800.0) -> None:
        if default_ttl_seconds is None:
            self._default_ttl_seconds = None
        else:
            self._default_ttl_seconds = max(1.0, float(default_ttl_seconds))

    def get_pending_interaction(
        self,
        *,
        session: "SessionRecord",
        expire_stale: bool = True,
    ) -> PendingInteraction | None:
        state = deserialize_session_context(session.context_reference)
        pending = state.pending_interaction
        if pending is None:
            return None

        if expire_stale and self._is_stale(pending):
            state.pending_interaction = None
            self._write_state(session=session, state=state)
            return None
        return pending

    def get_pending_legacy_payload(
        self,
        *,
        session: "SessionRecord",
        expire_stale: bool = True,
    ) -> dict[str, Any] | None:
        pending = self.get_pending_interaction(session=session, expire_stale=expire_stale)
        if pending is None:
            return None
        entities = pending.proposed_action.get("entities") if isinstance(pending.proposed_action, dict) else {}
        if not isinstance(entities, dict):
            entities = {}
        return {
            "intent": pending.intent,
            "entities": dict(entities),
            "missing_fields": list(pending.expected_fields),
            "question": pending.question,
            "kind": pending.kind,
            "status": pending.status,
            "expires_at": pending.expires_at,
            "metadata": dict(pending.metadata),
        }

    def set_pending_interaction(
        self,
        *,
        session: "SessionRecord",
        intent: str,
        entities: dict[str, Any],
        missing_fields: list[str],
        question: str | None,
        kind: str = "missing_field",
        status: str = "pending",
        skill_id: str | None = None,
        expires_in_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PendingInteraction:
        now = _utc_now()
        ttl = self._default_ttl_seconds if expires_in_seconds is None else max(1.0, float(expires_in_seconds))
        expires_at = None
        if ttl is not None:
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()

        normalized_missing = [str(item).strip() for item in missing_fields if str(item).strip()]
        normalized_entities = dict(entities) if isinstance(entities, dict) else {}
        pending = PendingInteraction(
            kind=str(kind or "missing_field").strip() or "missing_field",
            intent=str(intent or "").strip() or None,
            skill_id=str(skill_id or "").strip() or None,
            status=str(status or "pending").strip() or "pending",
            question=str(question).strip() if isinstance(question, str) and question.strip() else None,
            expected_fields=normalized_missing,
            candidate_entities=[],
            proposed_action={"entities": normalized_entities},
            created_at=now,
            expires_at=expires_at,
            metadata=dict(metadata or {}),
        )
        state = deserialize_session_context(session.context_reference)
        state.pending_interaction = pending
        self._write_state(session=session, state=state)
        return pending

    def continue_pending_interaction(
        self,
        *,
        session: "SessionRecord",
        entities: dict[str, Any] | None = None,
        missing_fields: list[str] | None = None,
        question: str | None = None,
        status: str | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> PendingInteraction | None:
        state = deserialize_session_context(session.context_reference)
        pending = state.pending_interaction
        if pending is None:
            return None
        if self._is_stale(pending):
            state.pending_interaction = None
            self._write_state(session=session, state=state)
            return None

        next_entities = pending.proposed_action.get("entities") if isinstance(pending.proposed_action, dict) else {}
        if not isinstance(next_entities, dict):
            next_entities = {}
        if isinstance(entities, dict):
            next_entities = dict(entities)

        next_missing = list(pending.expected_fields)
        if isinstance(missing_fields, list):
            next_missing = [str(item).strip() for item in missing_fields if str(item).strip()]

        next_question = pending.question
        if isinstance(question, str):
            next_question = question.strip() or None

        next_status = pending.status
        if isinstance(status, str) and status.strip():
            next_status = status.strip()

        next_metadata = dict(pending.metadata)
        if isinstance(metadata_updates, dict):
            next_metadata.update(metadata_updates)

        updated = replace(
            pending,
            status=next_status,
            question=next_question,
            expected_fields=next_missing,
            proposed_action={"entities": next_entities},
            metadata=next_metadata,
        )
        state.pending_interaction = updated
        self._write_state(session=session, state=state)
        return updated

    def cancel_pending_interaction(
        self,
        *,
        session: "SessionRecord",
        reason: str | None = None,
    ) -> bool:
        state = deserialize_session_context(session.context_reference)
        pending = state.pending_interaction
        if pending is None:
            return False
        state.pending_interaction = None
        if reason:
            state.context_annotations["last_pending_cancel_reason"] = str(reason).strip()
            state.context_annotations["last_pending_cancelled_at"] = _utc_now()
        self._write_state(session=session, state=state)
        return True

    def clear_pending_interaction(
        self,
        *,
        session: "SessionRecord",
    ) -> bool:
        state = deserialize_session_context(session.context_reference)
        if state.pending_interaction is None:
            return False
        state.pending_interaction = None
        self._write_state(session=session, state=state)
        return True

    def expire_stale_pending_interaction(self, *, session: "SessionRecord") -> bool:
        state = deserialize_session_context(session.context_reference)
        pending = state.pending_interaction
        if pending is None:
            return False
        if not self._is_stale(pending):
            return False
        state.pending_interaction = None
        self._write_state(session=session, state=state)
        return True

    @staticmethod
    def _write_state(*, session: "SessionRecord", state: Any) -> None:
        serialized = serialize_session_context(state)
        merged = dict(session.context_reference)
        merged.update(serialized)
        session.context_reference = merged

    @staticmethod
    def _is_stale(pending: PendingInteraction) -> bool:
        if not isinstance(pending.expires_at, str) or not pending.expires_at.strip():
            return False
        try:
            expires_at = datetime.fromisoformat(pending.expires_at)
        except ValueError:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= expires_at
