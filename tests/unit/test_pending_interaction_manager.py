from datetime import datetime, timedelta, timezone

from app.context.pending import PendingInteractionManager
from app.core.pending_interaction import PendingInteractionCoordinator
from app.core.session_store import SessionRecord
from app.core.session_store import SessionStore
from app.services.event_log import EventLogService
from app.skills.tool_contracts import ToolDescriptor


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


def _pending_descriptor(*, persistence: str) -> ToolDescriptor:
    return ToolDescriptor.from_mapping(
        {
            "tool_id": "fixture.write",
            "skill_id": "skill.fixture.core",
            "contract_version": 1,
            "purpose": "Test pending typed calls.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["query", "target"],
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 80},
                    "target": {"type": "string", "minLength": 1, "maxLength": 80},
                },
            },
            "observation_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {},
            },
            "effect": "local_write",
            "approval_rule": "none",
            "approval_conditions": [],
            "sensitivity": "private",
            "persistence": persistence,
            "idempotency": "required",
            "effect_cardinality": "single",
            "transferable_observation_fields": [],
            "runtime_dependencies": [],
            "timeout_seconds": 5,
            "max_result_items": 1,
            "max_observation_chars": 100,
            "legacy_intents": [],
            "interactive": True,
        }
    )


def test_typed_pending_no_store_keeps_only_hashes_fields_and_bindings():
    manager = PendingInteractionManager(default_ttl_seconds=1800.0)
    store = SessionStore()
    events = EventLogService()
    coordinator = PendingInteractionCoordinator(
        manager=manager,
        session_store=store,
        event_log=events,
    )
    session = store.get_or_create(
        session_id="typed-pending",
        user_id="jordan",
        source="discord",
    )

    coordinator.store_tool_call(
        session=session,
        descriptor=_pending_descriptor(persistence="no_store"),
        partial_arguments={"query": "private-value"},
        missing_fields=["target"],
        question="Which target? Please restate all required values.",
        root_request_id="root-request",
        reserved_call_ordinal=1,
        binding_hash="pendingbind_v1_fixture",
        selected_skill_ids=["skill.fixture.core"],
    )

    pending = coordinator.get(session=session)
    assert pending is not None
    assert pending["entities"] == {}
    assert pending["metadata"]["present_fields"] == ["query"]
    assert pending["metadata"]["missing_fields"] == ["target"]
    assert pending["metadata"]["partial_arguments_hash"]
    assert "operation_id" not in pending["metadata"]
    assert "arguments_hash" not in pending["metadata"]
    assert "private-value" not in str(session.context_reference)
    assert "private-value" not in str(events.recent(limit=50))


def test_typed_pending_redacted_retains_only_purpose_bound_partial_arguments():
    manager = PendingInteractionManager(default_ttl_seconds=1800.0)
    store = SessionStore()
    coordinator = PendingInteractionCoordinator(
        manager=manager,
        session_store=store,
        event_log=EventLogService(),
    )
    session = store.get_or_create(
        session_id="typed-redacted",
        user_id="jordan",
        source="discord",
    )

    coordinator.store_tool_call(
        session=session,
        descriptor=_pending_descriptor(persistence="redacted"),
        partial_arguments={"query": "bounded-value"},
        missing_fields=["target"],
        question="Which target?",
        root_request_id="root-request",
        reserved_call_ordinal=2,
        binding_hash="pendingbind_v1_fixture",
        selected_skill_ids=["skill.fixture.core"],
    )

    pending = coordinator.get(session=session)
    assert pending is not None
    assert pending["entities"] == {"query": "bounded-value"}
    assert pending["metadata"]["reserved_call_ordinal"] == 2
