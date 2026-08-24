from __future__ import annotations

from typing import Any

from app.context.pending import PendingInteractionManager
from app.core.session_store import SessionRecord, SessionStore
from app.services.event_log import EventLogService


class PendingInteractionCoordinator:
    """Own durable pending-interaction lifecycle and its transition audit trail."""

    def __init__(
        self,
        *,
        manager: PendingInteractionManager,
        session_store: SessionStore,
        event_log: EventLogService,
    ) -> None:
        self._manager = manager
        self._session_store = session_store
        self._event_log = event_log

    def store(
        self,
        *,
        session: SessionRecord,
        intent: str,
        entities: dict[str, Any],
        missing_fields: list[str],
        question: str | None,
        kind: str = "missing_field",
        skill_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        reason: str = "pending_interaction_stored",
    ) -> None:
        before = self.snapshot(session=session)
        pending = self._manager.set_pending_interaction(
            session=session,
            intent=intent,
            entities=entities,
            missing_fields=missing_fields,
            question=question,
            kind=kind,
            skill_id=skill_id,
            metadata=metadata,
        )
        session.touch()
        self._session_store.save(session)
        self._record_transition(
            session=session,
            action="set",
            before=before,
            after=self._snapshot_from_object(pending),
            reason=reason,
        )

    def clear(self, *, session: SessionRecord, reason: str = "pending_interaction_cleared") -> bool:
        before = self.snapshot(session=session)
        cleared = self._manager.clear_pending_interaction(session=session)
        if not cleared:
            return False
        session.touch()
        self._session_store.save(session)
        self._record_transition(
            session=session,
            action="clear",
            before=before,
            after=None,
            reason=reason,
        )
        return True

    def cancel(self, *, session: SessionRecord, reason: str) -> bool:
        before = self.snapshot(session=session)
        cancelled = self._manager.cancel_pending_interaction(session=session, reason=reason)
        if not cancelled:
            return False
        session.touch()
        self._session_store.save(session)
        self._record_transition(
            session=session,
            action="cancel",
            before=before,
            after=None,
            reason=reason,
        )
        return True

    def continue_interaction(
        self,
        *,
        session: SessionRecord,
        entities: dict[str, Any],
        missing_fields: list[str],
        question: str | None,
        status: str = "pending",
        metadata_updates: dict[str, Any] | None = None,
        reason: str = "pending_interaction_continued",
    ) -> bool:
        before = self.snapshot(session=session)
        updated = self._manager.continue_pending_interaction(
            session=session,
            entities=entities,
            missing_fields=missing_fields,
            question=question,
            status=status,
            metadata_updates=metadata_updates,
        )
        if updated is None:
            return False
        session.touch()
        self._session_store.save(session)
        self._record_transition(
            session=session,
            action="continue",
            before=before,
            after=self._snapshot_from_object(updated),
            reason=reason,
        )
        return True

    def get(self, *, session: SessionRecord) -> dict[str, Any] | None:
        before = self.snapshot(session=session)
        expired = self._manager.expire_stale_pending_interaction(session=session)
        if expired:
            session.touch()
            self._session_store.save(session)
            self._event_log.record(
                event_type="pending.interaction.expired",
                session_id=session.session_id,
                payload={"reason": "ttl_expired"},
            )
            self._record_transition(
                session=session,
                action="expired",
                before=before,
                after=None,
                reason="ttl_expired",
            )
        return self._manager.get_pending_legacy_payload(
            session=session,
            expire_stale=False,
        )

    def snapshot(self, *, session: SessionRecord) -> dict[str, Any] | None:
        pending = self._manager.get_pending_interaction(
            session=session,
            expire_stale=False,
        )
        return self._snapshot_from_object(pending)

    @staticmethod
    def _snapshot_from_object(pending: Any) -> dict[str, Any] | None:
        if pending is None:
            return None
        return {
            "kind": str(getattr(pending, "kind", "") or "").strip() or None,
            "intent": str(getattr(pending, "intent", "") or "").strip() or None,
            "status": str(getattr(pending, "status", "") or "").strip() or None,
            "expected_fields": [
                str(item).strip()
                for item in (getattr(pending, "expected_fields", []) or [])
                if str(item).strip()
            ],
            "question": str(getattr(pending, "question", "") or "").strip() or None,
            "expires_at": str(getattr(pending, "expires_at", "") or "").strip() or None,
        }

    def _record_transition(
        self,
        *,
        session: SessionRecord,
        action: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        reason: str | None,
    ) -> None:
        payload: dict[str, Any] = {
            "action": str(action or "").strip().lower() or "unknown",
            "before": dict(before) if isinstance(before, dict) else None,
            "after": dict(after) if isinstance(after, dict) else None,
            "had_pending_before": isinstance(before, dict),
            "has_pending_after": isinstance(after, dict),
        }
        if isinstance(reason, str) and reason.strip():
            payload["reason"] = reason.strip()
        self._event_log.record(
            event_type="context.pending_interaction.transition",
            session_id=session.session_id,
            payload=payload,
        )
