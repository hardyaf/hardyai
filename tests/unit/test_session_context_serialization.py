from app.context.serialization import (
    deserialize_session_context,
    serialize_session_context,
    session_context_to_legacy_compat_dict,
)
from app.context.types import CURRENT_SESSION_CONTEXT_VERSION, PendingInteraction, SessionContextState
from app.core.session_store import SessionRecord


def test_session_context_round_trip_serialization():
    original = SessionContextState(
        version=CURRENT_SESSION_CONTEXT_VERSION,
        active_agent_id="jarvis",
        active_skill_id="skill.lists.core",
        focus_stack=["lists", "shopping"],
        context_annotations={"source": "unit_test"},
        channel_runtime={"channel_key": "jordan:dashboard.command"},
        main_agent_token_session={"turn_summaries": ["summary"], "total_turns": 1},
    )
    original.pending_interaction = PendingInteraction(
        kind="missing_field",
        intent="lists.add_item",
        question="Which list?",
        expected_fields=["list_name"],
        proposed_action={"entities": {"item_text": "milk"}},
    )

    serialized = serialize_session_context(original)
    restored = deserialize_session_context(serialized)

    assert serialized["context_version"] == CURRENT_SESSION_CONTEXT_VERSION
    assert restored.active_agent_id == "jarvis"
    assert restored.active_skill_id == "skill.lists.core"
    assert restored.pending_interaction is not None
    assert restored.pending_interaction.intent == "lists.add_item"
    assert restored.pending_interaction.expected_fields == ["list_name"]
    assert restored.main_agent_token_session["total_turns"] == 1


def test_deserialize_session_context_migrates_legacy_fields():
    legacy = {
        "active_agent_id": "jarvis",
        "last_list_name": "groceries",
        "last_switch_name": "porch light",
        "last_calendar_person": "Jordan",
        "pending_clarification": {
            "intent": "lists.add_item",
            "entities": {"item_text": "milk"},
            "missing_fields": ["list_name"],
            "question": "Which list should I use?",
        },
        "main_sticky_followup_turns_remaining": 2,
        "main_sticky_followup_reason": "pending_clarification_continue",
        "main_agent_token_session": {"turn_summaries": ["prior"], "total_turns": 3},
        "channel_session": {"channel_key": "jordan:dashboard.command", "session_id": "abc123"},
    }

    state = deserialize_session_context(legacy)

    assert state.pending_interaction is not None
    assert state.pending_interaction.intent == "lists.add_item"
    assert state.pending_interaction.expected_fields == ["list_name"]
    assert len(state.entity_registry.entities) >= 3
    assert state.context_annotations["main_sticky_followup"]["turns_remaining"] == 2
    assert state.context_annotations["main_sticky_followup"]["reason"] == "pending_clarification_continue"
    assert state.main_agent_token_session["total_turns"] == 3
    assert state.channel_runtime["channel_key"] == "jordan:dashboard.command"


def test_session_context_legacy_compat_projection():
    state = deserialize_session_context(
        {
            "active_agent_id": "jarvis",
            "last_list_name": "Costco",
            "pending_clarification": {
                "intent": "lists.add_item",
                "entities": {"item_text": "apples"},
                "missing_fields": ["list_name"],
                "question": "Which list?",
            },
            "main_sticky_followup_turns_remaining": 1,
            "main_sticky_followup_reason": "tool_followup_required",
        }
    )

    legacy = session_context_to_legacy_compat_dict(state)

    assert legacy["last_list_name"] == "Costco"
    assert legacy["pending_clarification"]["intent"] == "lists.add_item"
    assert legacy["pending_clarification"]["missing_fields"] == ["list_name"]
    assert legacy["main_sticky_followup_turns_remaining"] == 1
    assert legacy["main_sticky_followup_reason"] == "tool_followup_required"


def test_session_record_context_state_helpers():
    session = SessionRecord(session_id="s1", user_id="jordan", source="web")
    session.context_reference = {"last_list_name": "groceries"}

    state = session.context_state()
    assert state.active_agent_id == "jarvis"
    assert any(entity.display_name == "groceries" for entity in state.entity_registry.entities)

    state.focus_stack = ["lists"]
    session.set_context_state(state)
    assert session.context_reference["context_version"] == CURRENT_SESSION_CONTEXT_VERSION

    legacy_view = session.legacy_context_view()
    assert legacy_view["last_list_name"] == "groceries"

