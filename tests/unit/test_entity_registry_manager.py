from app.context.entity_registry import EntityRegistryManager
from app.core.session_store import SessionRecord


def test_entity_registry_manager_records_and_returns_latest_entity():
    manager = EntityRegistryManager(max_entities=32)
    session = SessionRecord(session_id="e1", user_id="jordan", source="web")

    update = manager.record_entities(
        session=session,
        entities=[
            {
                "domain": "lists",
                "entity_type": "list",
                "display_name": "Groceries",
                "aliases": ["grocery list"],
                "salience": 0.85,
            }
        ],
    )
    assert update["updated"] is True
    assert update["upserted_count"] == 1

    latest = manager.latest_entity_display_name(
        session=session,
        domain="lists",
        entity_type="list",
    )
    assert latest == "Groceries"

    second_update = manager.record_entities(
        session=session,
        entities=[
            {
                "domain": "lists",
                "entity_type": "list",
                "display_name": "Groceries",
                "aliases": ["shopping list"],
                "salience": 0.9,
            }
        ],
    )
    assert second_update["updated"] is True
    registry = session.context_state().entity_registry
    assert len(registry.entities) == 1
    assert "shopping list" in registry.entities[0].aliases

