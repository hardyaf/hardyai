from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from app.context.types import PendingInteraction, SessionContextState, TrackedEntity
from app.core.session_store import SessionStore
from app.db.sqlite_store import SQLiteStore


def _scratch_dir(prefix: str) -> Path:
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"{prefix}-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


def test_sqlite_session_persists_context_payload_and_version():
    scratch = _scratch_dir("jarvis-session-context")
    try:
        db_path = scratch / "session.db"
        persistence = SQLiteStore(database_path=str(db_path))
        store = SessionStore(persistence=persistence)

        session = store.get_or_create(
            session_id="persist-context-1",
            user_id="jordan",
            source="web",
        )
        state = SessionContextState(active_agent_id="jarvis")
        state.pending_interaction = PendingInteraction(
            kind="missing_field",
            intent="lists.add_item",
            expected_fields=["list_name"],
            proposed_action={"entities": {"item_text": "milk"}},
        )
        state.entity_registry.entities.append(
            TrackedEntity(
                domain="lists",
                entity_type="list",
                display_name="groceries",
                aliases=["grocery list", "groceries"],
                salience=0.91,
            )
        )
        session.set_context_state(state)
        store.save(session)

        persisted = persistence.get_session("persist-context-1")
        assert persisted is not None
        assert persisted["context_version"] == 1
        context_reference = persisted["context_reference"]
        pending = context_reference.get("pending_interaction")
        assert isinstance(pending, dict)
        assert pending.get("intent") == "lists.add_item"
        entities = (
            context_reference.get("entity_registry", {})
            .get("entities", [])
        )
        assert any(str(item.get("display_name") or "").strip() == "groceries" for item in entities)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_session_store_reloads_persisted_context_after_restart():
    scratch = _scratch_dir("jarvis-session-reload")
    try:
        db_path = scratch / "session.db"
        persistence = SQLiteStore(database_path=str(db_path))

        first_store = SessionStore(persistence=persistence)
        first_session = first_store.get_or_create(
            session_id="persist-context-2",
            user_id="jordan",
            source="discord",
        )
        state = SessionContextState(active_agent_id="jarvis")
        state.pending_interaction = PendingInteraction(
            kind="missing_field",
            intent="home.set_switch",
            expected_fields=["switch_name"],
            question="Which switch?",
            proposed_action={"entities": {"action": "off"}},
        )
        state.entity_registry.entities.append(
            TrackedEntity(
                domain="home",
                entity_type="switch",
                display_name="porch light",
                aliases=["porch", "front porch light"],
                salience=0.9,
            )
        )
        state.main_agent_token_session = {
            "turn_summaries": ["turn=1"],
            "total_turns": 1,
        }
        first_session.set_context_state(state)
        first_store.save(first_session)

        reloaded_store = SessionStore(persistence=persistence)
        reloaded_session = reloaded_store.get_or_create(
            session_id="persist-context-2",
            user_id="someone_else",
            source="web",
        )

        assert reloaded_session.user_id == "jordan"
        assert reloaded_session.source == "discord"
        reloaded_state = reloaded_session.context_state()
        assert reloaded_state.pending_interaction is not None
        assert reloaded_state.pending_interaction.intent == "home.set_switch"
        assert reloaded_state.pending_interaction.expected_fields == ["switch_name"]
        assert any(
            entity.domain == "home"
            and entity.entity_type == "switch"
            and entity.display_name == "porch light"
            for entity in reloaded_state.entity_registry.entities
        )
        assert reloaded_state.main_agent_token_session.get("total_turns") == 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_session_store_get_returns_existing_or_persisted_session_snapshot():
    scratch = _scratch_dir("jarvis-session-get")
    try:
        db_path = scratch / "session.db"
        persistence = SQLiteStore(database_path=str(db_path))

        first_store = SessionStore(persistence=persistence)
        created = first_store.get_or_create(
            session_id="persist-context-3",
            user_id="jordan",
            source="web",
        )
        state = SessionContextState(active_agent_id="jarvis")
        state.entity_registry.entities.append(
            TrackedEntity(
                domain="lists",
                entity_type="list",
                display_name="groceries",
                aliases=["grocery list"],
                salience=0.88,
            )
        )
        created.set_context_state(state)
        first_store.save(created)

        second_store = SessionStore(persistence=persistence)
        loaded = second_store.get("persist-context-3")
        assert loaded is not None
        assert loaded.session_id == "persist-context-3"
        assert loaded.user_id == "jordan"
        loaded_state = loaded.context_state()
        assert any(
            entity.domain == "lists"
            and entity.entity_type == "list"
            and entity.display_name == "groceries"
            for entity in loaded_state.entity_registry.entities
        )
        assert second_store.get("missing-session-id") is None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
