from app.context.entity_registry import EntityRegistryManager
from app.context.pending import PendingInteractionManager
from app.context.session_context_manager import SessionContextManager
from app.context.summarizer import SessionSummaryManager
from app.core.session_store import SessionRecord


def test_session_summary_manager_updates_on_turn_interval_and_stays_bounded():
    turns = SessionContextManager(max_recent_turns=20, max_recent_chars=10000)
    summaries = SessionSummaryManager(
        update_every_turns=4,
        budget_char_threshold=9000,
        max_summary_chars=180,
    )
    session = SessionRecord(session_id="summary-interval-1", user_id="jordan", source="web")

    for i in range(2):
        turns.record_exchange(
            session=session,
            user_text=f"user prompt {i}",
            assistant_text=f"assistant response {i}",
            intent="conversation.general",
            route="main_jarvis",
            skill_id="skill.conversation.general",
            result_status="conversation",
        )

    update = summaries.maybe_refresh(
        session=session,
        intent="conversation.general",
        route="main_jarvis",
        result_status="conversation",
    )
    assert update.updated is True
    assert update.trigger == "turn_interval"

    summary = session.context_reference.get("session_summary")
    assert isinstance(summary, dict)
    summary_text = str(summary.get("summary_text") or "")
    assert summary_text
    assert len(summary_text) <= 180

    second = summaries.maybe_refresh(
        session=session,
        intent="conversation.general",
        route="main_jarvis",
        result_status="conversation",
    )
    assert second.updated is False


def test_session_summary_manager_captures_pending_threads_and_entities():
    turns = SessionContextManager(max_recent_turns=20, max_recent_chars=10000)
    pending = PendingInteractionManager(default_ttl_seconds=600.0)
    entities = EntityRegistryManager()
    summaries = SessionSummaryManager(
        update_every_turns=100,
        budget_char_threshold=9000,
        max_summary_chars=240,
    )
    session = SessionRecord(session_id="summary-pending-1", user_id="jordan", source="web")

    turns.record_exchange(
        session=session,
        user_text="add apples to it",
        assistant_text="Which list should I use?",
        intent="lists.add_item",
        route="main_jarvis_repair",
        skill_id="skill.lists.core",
        result_status="needs_clarification",
    )
    pending.set_pending_interaction(
        session=session,
        intent="lists.add_item",
        entities={"item_text": "apples"},
        missing_fields=["list_name"],
        question="Which list?",
    )
    entities.record_entities(
        session=session,
        entities=[
            {
                "domain": "lists",
                "entity_type": "list",
                "display_name": "groceries",
                "aliases": ["grocery list"],
                "salience": 0.92,
            }
        ],
    )

    update = summaries.maybe_refresh(
        session=session,
        intent="lists.add_item",
        route="main_jarvis_repair",
        result_status="ok",
    )
    assert update.updated is True
    assert update.trigger == "task_completed"

    summary = session.context_reference.get("session_summary")
    assert isinstance(summary, dict)
    open_threads = summary.get("open_threads")
    assert isinstance(open_threads, list)
    assert open_threads and "awaiting(list_name)" in open_threads[0]

    important_entities = summary.get("important_entities")
    assert isinstance(important_entities, list)
    assert "lists:groceries" in important_entities


def test_session_summary_manager_updates_when_focus_changes():
    turns = SessionContextManager(max_recent_turns=20, max_recent_chars=10000)
    summaries = SessionSummaryManager(update_every_turns=100, budget_char_threshold=9000)
    session = SessionRecord(session_id="summary-focus-1", user_id="jordan", source="web")

    turns.record_exchange(
        session=session,
        user_text="add milk to groceries",
        assistant_text="Added milk to groceries.",
        intent="lists.add_item",
        route="micro_tool",
        skill_id="skill.lists.core",
        result_status="ok",
    )

    first = summaries.maybe_refresh(
        session=session,
        intent="lists.add_item",
        route="micro_tool",
        result_status="conversation",
        force=True,
    )
    assert first.updated is True

    second = summaries.maybe_refresh(
        session=session,
        intent="calendar.view",
        route="main_jarvis",
        result_status="conversation",
    )
    assert second.updated is True
    assert second.trigger == "focus_changed"

    annotations = session.context_reference.get("context_annotations")
    assert isinstance(annotations, dict)
    assert annotations.get("summary_last_focus_key") == "calendar.view@main_jarvis"
