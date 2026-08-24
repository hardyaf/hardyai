from app.context.session_context_manager import SessionContextManager
from app.core.session_store import SessionRecord


def test_session_context_manager_records_user_and_assistant_turns():
    manager = SessionContextManager(max_recent_turns=10, max_recent_chars=10000)
    session = SessionRecord(session_id="s1", user_id="jordan", source="web")

    update = manager.record_exchange(
        session=session,
        user_text="Add milk to groceries",
        assistant_text="Added milk to groceries.",
        intent="lists.add_item",
        route="micro_tool",
        skill_id="skill.lists.core",
        result_status="ok",
    )

    assert update.updated is True
    assert update.appended_count == 2
    turns = session.context_reference.get("recent_turns")
    assert isinstance(turns, list)
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[1]["role"] == "assistant"
    assert turns[0]["normalized_text"] == "add milk to groceries"
    assert turns[1]["references"]["status"] == "ok"


def test_session_context_manager_prunes_deterministically_by_turn_count():
    manager = SessionContextManager(max_recent_turns=4, max_recent_chars=10000)
    session = SessionRecord(session_id="s2", user_id="jordan", source="web")

    for i in range(3):
        manager.record_exchange(
            session=session,
            user_text=f"user {i}",
            assistant_text=f"assistant {i}",
            intent="conversation.general",
            route="main_jarvis",
            skill_id="skill.conversation.general",
            result_status="conversation",
        )

    turns = session.context_reference.get("recent_turns")
    assert isinstance(turns, list)
    assert len(turns) == 4
    assert turns[0]["text"] == "user 1"
    assert turns[1]["text"] == "assistant 1"
    assert turns[2]["text"] == "user 2"
    assert turns[3]["text"] == "assistant 2"


def test_session_context_manager_prunes_by_char_budget():
    manager = SessionContextManager(max_recent_turns=10, max_recent_chars=512, max_single_turn_chars=120)
    session = SessionRecord(session_id="s3", user_id="jordan", source="web")

    manager.record_exchange(
        session=session,
        user_text="x" * 90,
        assistant_text="y" * 90,
        intent="conversation.general",
        route="main_jarvis",
        skill_id="skill.conversation.general",
        result_status="conversation",
    )
    manager.record_exchange(
        session=session,
        user_text="z" * 90,
        assistant_text="w" * 90,
        intent="conversation.general",
        route="main_jarvis",
        skill_id="skill.conversation.general",
        result_status="conversation",
    )

    turns = session.context_reference.get("recent_turns")
    assert isinstance(turns, list)
    assert len(turns) <= 2
    assert turns[-1]["text"] == "w" * 90
