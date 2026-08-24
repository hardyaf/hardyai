from app.context.context_builder import ContextBuilder
from app.context.entity_registry import EntityRegistryManager
from app.context.session_context_manager import SessionContextManager
from app.core.session_store import SessionRecord


def test_context_builder_builds_bounded_packet():
    turns = SessionContextManager(max_recent_turns=24, max_recent_chars=20000)
    entities = EntityRegistryManager()
    session = SessionRecord(session_id="context-builder-1", user_id="jordan", source="web")

    for i in range(4):
        turns.record_exchange(
            session=session,
            user_text=f"user turn {i}",
            assistant_text=f"assistant turn {i}",
            intent="conversation.general",
            route="main_jarvis",
            skill_id="skill.conversation.general",
            result_status="conversation",
        )
    entities.record_entities(
        session=session,
        entities=[
            {
                "domain": "lists",
                "entity_type": "list",
                "display_name": "groceries",
                "aliases": ["grocery list"],
                "salience": 0.91,
            },
            {
                "domain": "home",
                "entity_type": "switch",
                "display_name": "kitchen light",
                "aliases": ["kitchen lamp"],
                "salience": 0.88,
            },
            {
                "domain": "calendar",
                "entity_type": "person",
                "display_name": "Jordan",
                "aliases": ["jordan"],
                "salience": 0.77,
            },
        ],
    )

    builder = ContextBuilder(
        max_recent_turns=4,
        max_entity_hints=2,
        max_memory_entries=2,
        max_text_chars=40,
    )
    packet = builder.build_packet(
        session=session,
        relevant_memory=[
            {
                "session_id": "context-builder-1",
                "user_id": "jordan",
                "intent": "lists.add_item",
                "route": "micro_tool",
                "request_text": "add apples to groceries",
                "response_summary": "Added apples.",
                "metadata": {"status": "ok"},
            },
            {
                "session_id": "context-builder-1",
                "user_id": "jordan",
                "intent": "home.set_switch",
                "route": "micro_tool",
                "request_text": "turn kitchen light off",
                "response_summary": "Turned kitchen light off.",
                "metadata": {"status": "ok"},
            },
            {
                "session_id": "context-builder-1",
                "user_id": "jordan",
                "intent": "calendar.view",
                "route": "micro_tool",
                "request_text": "show my calendar",
                "response_summary": "Here is your schedule.",
                "metadata": {"status": "ok"},
            },
        ],
        active_skill_context={"route_hint": "main_jarvis"},
        budget_metadata={"max_chars": 2400, "used_chars": 400},
    )

    assert len(packet.recent_turns) == 4
    assert len(packet.entity_hints) == 2
    assert len(packet.relevant_memory) == 2
    assert packet.active_skill_context["route_hint"] == "main_jarvis"
    assert packet.budget_metadata["max_chars"] == 2400

    payload = packet.to_dict()
    assert isinstance(payload.get("session_summary"), dict)
    assert len(payload.get("recent_turns") or []) == 4
