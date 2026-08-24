from datetime import datetime, timedelta, timezone

from app.context.pending import PendingInteractionManager
from app.core.session_store import SessionRecord


def test_pending_interaction_manager_set_get_continue_clear():
    manager = PendingInteractionManager(default_ttl_seconds=1800.0)
    session = SessionRecord(session_id="p1", user_id="jordan", source="web")

    manager.set_pending_interaction(
        session=session,
        intent="lists.add_item",
        entities={"item_text": "milk"},
        missing_fields=["list_name"],
        question="Which list?",
        kind="missing_field",
        status="pending",
    )

    pending = manager.get_pending_legacy_payload(session=session)
    assert pending is not None
    assert pending["intent"] == "lists.add_item"
    assert pending["missing_fields"] == ["list_name"]
    assert pending["entities"]["item_text"] == "milk"
    assert "pending_interaction" in session.context_reference

    manager.continue_pending_interaction(
        session=session,
        entities={"item_text": "milk", "list_name": "groceries"},
        missing_fields=[],
        question=None,
        status="pending",
    )
    continued = manager.get_pending_legacy_payload(session=session)
    assert continued is not None
    assert continued["missing_fields"] == []
    assert continued["entities"]["list_name"] == "groceries"

    cleared = manager.clear_pending_interaction(session=session)
    assert cleared is True
    assert manager.get_pending_legacy_payload(session=session) is None


def test_pending_interaction_manager_cancel_and_expire():
    manager = PendingInteractionManager(default_ttl_seconds=1800.0)
    session = SessionRecord(session_id="p2", user_id="jordan", source="web")

    manager.set_pending_interaction(
        session=session,
        intent="home.set_switch",
        entities={"action": "off"},
        missing_fields=["switch_name"],
        question="Which switch?",
        kind="missing_field",
        status="pending",
    )
    cancelled = manager.cancel_pending_interaction(session=session, reason="user_cancelled_pending_flow")
    assert cancelled is True
    assert manager.get_pending_legacy_payload(session=session) is None
    assert session.context_reference["context_annotations"]["last_pending_cancel_reason"] == "user_cancelled_pending_flow"

    manager.set_pending_interaction(
        session=session,
        intent="home.set_switch",
        entities={"action": "on"},
        missing_fields=["switch_name"],
        question="Which switch?",
        kind="missing_field",
        status="pending",
    )

    state = session.context_state()
    assert state.pending_interaction is not None
    stale = datetime.now(timezone.utc) - timedelta(seconds=5)
    state.pending_interaction.expires_at = stale.isoformat()
    session.set_context_state(state)

    expired = manager.expire_stale_pending_interaction(session=session)
    assert expired is True
    assert manager.get_pending_legacy_payload(session=session) is None

