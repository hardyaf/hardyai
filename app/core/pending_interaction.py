from __future__ import annotations

from typing import Any

from app.context.pending import PendingInteractionManager
from app.core.session_store import SessionRecord, SessionStore
from app.core.tool_loop_types import partial_arguments_hash
from app.services.event_log import EventLogService
from app.skills.tool_contracts import ToolDescriptor


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

    def store_tool_call(
        self,
        *,
        session: SessionRecord,
        descriptor: ToolDescriptor,
        partial_arguments: dict[str, Any],
        missing_fields: list[str],
        question: str,
        root_request_id: str,
        reserved_call_ordinal: int,
        binding_hash: str,
        selected_skill_ids: list[str],
    ) -> None:
        """Persist one purpose-bound typed clarification through the existing authority."""

        policy = str(descriptor.persistence)
        retained_arguments = {} if policy == "no_store" else dict(partial_arguments)
        present_fields = sorted(str(key) for key in partial_arguments)
        self.store(
            session=session,
            intent=descriptor.tool_id,
            entities=retained_arguments,
            missing_fields=list(missing_fields),
            question=question,
            kind="typed_tool_call",
            skill_id=descriptor.skill_id,
            metadata={
                "pending_type": "typed_tool_call_v1",
                "tool_id": descriptor.tool_id,
                "skill_id": descriptor.skill_id,
                "contract_version": descriptor.contract_version,
                "root_request_id": str(root_request_id),
                "reserved_call_ordinal": int(reserved_call_ordinal),
                "partial_arguments_hash": partial_arguments_hash(partial_arguments),
                "persistence": policy,
                "binding_hash": str(binding_hash),
                "selected_skill_ids": [
                    str(item).strip().casefold()
                    for item in selected_skill_ids[:3]
                    if str(item).strip()
                ],
                "present_fields": present_fields,
                "missing_fields": [str(item) for item in missing_fields[:32]],
            },
            reason="main_tool_loop_clarification_stored",
        )

    def store_generic_action_clarification(
        self,
        *,
        session: SessionRecord,
        question: str,
        root_request_id: str,
        binding_hash: str,
    ) -> None:
        self.store(
            session=session,
            intent="generic_action_candidate",
            entities={},
            missing_fields=["complete_goal"],
            question=question,
            kind="main_action_clarification_v2",
            metadata={
                "pending_type": "main_action_clarification_v2",
                "root_request_id": str(root_request_id),
                "binding_hash": str(binding_hash),
                "persistence": "no_store",
            },
            reason="main_action_clarification_stored",
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
