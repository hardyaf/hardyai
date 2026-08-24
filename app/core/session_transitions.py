from __future__ import annotations

from app.core.session_store import SessionRecord, SessionStore
from app.core.types import SessionOwner, SessionState
from app.services.event_log import EventLogService


_OWNER_LABEL = {
    SessionOwner.SYSTEM: "system",
    SessionOwner.MICRO: "micro",
    SessionOwner.MAIN: "main",
}


class SessionTransitionService:
    """Own session owner/state transitions and bounded Main follow-up affinity."""

    def __init__(
        self,
        *,
        session_store: SessionStore,
        event_log: EventLogService,
        sticky_followup_turns: int,
    ) -> None:
        self._session_store = session_store
        self._event_log = event_log
        self._sticky_followup_turns = max(0, int(sticky_followup_turns))

    @staticmethod
    def active_agent_id(session: SessionRecord) -> str:
        value = session.context_reference.get("active_agent_id")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
        return "jarvis"

    def set_owner(self, *, session: SessionRecord, owner: SessionOwner) -> None:
        if session.owner == owner:
            return
        previous = session.owner
        session.owner = owner
        self._session_store.save(session)
        self._event_log.record(
            event_type="session.owner.changed",
            session_id=session.session_id,
            payload={"from": previous.value, "to": owner.value},
        )
        self._event_log.record(
            event_type=f"handoff.{_OWNER_LABEL[previous]}_to_{_OWNER_LABEL[owner]}",
            session_id=session.session_id,
            payload={"from": previous.value, "to": owner.value},
        )

    def set_state(self, *, session: SessionRecord, state: SessionState) -> None:
        if session.state == state:
            return
        previous = session.state
        session.state = state
        self._session_store.save(session)
        self._event_log.record(
            event_type="session.state.changed",
            session_id=session.session_id,
            payload={"from": previous.value, "to": state.value},
        )

    def arm_main_followup(
        self,
        *,
        session: SessionRecord,
        reason: str,
        turns: int | None = None,
    ) -> None:
        configured_turns = self._sticky_followup_turns if turns is None else int(turns)
        if configured_turns <= 0:
            return
        context_reference = dict(session.context_reference)
        context_reference["main_sticky_followup_turns_remaining"] = max(1, configured_turns)
        context_reference["main_sticky_followup_reason"] = str(reason or "clarification")
        session.context_reference = context_reference
        session.touch()
        self._session_store.save(session)

    def consume_main_followup(self, *, session: SessionRecord) -> int:
        remaining = self.main_followup_turns_remaining(session=session)
        if remaining <= 0:
            return 0
        next_remaining = max(0, remaining - 1)
        context_reference = dict(session.context_reference)
        if next_remaining > 0:
            context_reference["main_sticky_followup_turns_remaining"] = next_remaining
        else:
            context_reference.pop("main_sticky_followup_turns_remaining", None)
            context_reference.pop("main_sticky_followup_reason", None)
        session.context_reference = context_reference
        session.touch()
        self._session_store.save(session)
        return next_remaining

    def clear_main_followup(self, *, session: SessionRecord) -> None:
        if self.main_followup_turns_remaining(session=session) <= 0:
            return
        context_reference = dict(session.context_reference)
        context_reference.pop("main_sticky_followup_turns_remaining", None)
        context_reference.pop("main_sticky_followup_reason", None)
        session.context_reference = context_reference
        session.touch()
        self._session_store.save(session)

    @staticmethod
    def main_followup_turns_remaining(*, session: SessionRecord) -> int:
        value = session.context_reference.get("main_sticky_followup_turns_remaining")
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        return 0
