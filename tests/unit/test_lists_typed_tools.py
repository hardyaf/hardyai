from __future__ import annotations

from pathlib import Path

import pytest

from app.core.tool_loop_types import validate_descriptor_payload
from app.db.sqlite_store import SQLiteStore
from app.skills.domains.lists.storage import SQLiteListsStorage
from app.skills.domains.lists.tools import ListsToolHandler
from app.skills.registry_service import SkillRegistryService
from app.skills.tool_contracts import (
    ToolArgumentCanonicalizationError,
    ToolCallEnvelope,
    ToolContractError,
    ToolDescriptor,
)


def _runtime(tmp_path: Path):
    store = SQLiteStore(database_path=str(tmp_path / "lists-tools.db"))
    registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
    registry.seed_defaults()
    synced = registry.sync_skills_from_markdown()
    assert synced["failed_count"] == 0
    assert synced["tool_diagnostic_count"] == 0
    skill = next(
        item for item in registry.list_skills(active_only=True) if item["skill_id"] == ListsToolHandler.SKILL_ID
    )
    descriptors = {
        item.tool_id: item for item in registry.tool_descriptors_for_skill(skill)
    }
    storage = SQLiteListsStorage(sqlite_store=store)
    handler = ListsToolHandler(storage=storage)
    return store, storage, handler, descriptors


def _envelope(
    *,
    handler: ListsToolHandler,
    descriptor: ToolDescriptor,
    arguments: dict,
    root_request_id: str,
    call_ordinal: int,
    user_id: str = "natasha",
) -> ToolCallEnvelope:
    context = {
        "requested_by_user_id": user_id,
        "user_id": user_id,
        "agent_id": "jarvis",
        "source_interface": "discord",
        "discord_channel_id": "private-lists",
        "session_id": "session-lists",
    }
    validated = descriptor.validate_arguments(arguments)
    canonical = handler.canonicalize_tool_arguments(
        tool_id=descriptor.tool_id,
        validated_arguments=validated,
        request_context=context,
    )
    canonical = descriptor.validate_arguments(canonical)
    return ToolCallEnvelope.create(
        root_request_id=root_request_id,
        call_ordinal=call_ordinal,
        session_id="session-lists",
        principal_kind="user",
        principal_subject=user_id,
        user_id=user_id,
        agent_id="jarvis",
        source_interface="discord",
        channel_scope="private-lists",
        skill_id=ListsToolHandler.SKILL_ID,
        descriptor=descriptor,
        authorization_snapshot_ref="authz-lists-test",
        validated_arguments=canonical,
    )


def _execute(handler: ListsToolHandler, envelope: ToolCallEnvelope, descriptor: ToolDescriptor) -> dict:
    result = handler.execute_tool(envelope=envelope, services={})
    if result.get("status") in {"ok", "needs_input"}:
        validate_descriptor_payload(descriptor, result.get("payload") or {}, observation=True)
    return result


def test_lists_markdown_publishes_only_the_accelerated_four_tools(tmp_path: Path) -> None:
    _store, _storage, _handler, descriptors = _runtime(tmp_path)

    assert set(descriptors) == {
        "lists.list_collections",
        "lists.get_collection",
        "lists.create_collection",
        "lists.add_items",
    }
    assert descriptors["lists.add_items"].effect_cardinality == "atomic_batch"
    assert descriptors["lists.add_items"].input_schema["properties"]["items"]["maxItems"] == 50
    assert descriptors["lists.list_collections"].input_schema["properties"] == {}
    add_properties = descriptors["lists.add_items"].input_schema["properties"]
    assert "opaque collection_v1 reference" in add_properties["collection_ref"]["description"].casefold()
    assert "Human-supplied list name" in add_properties["name"]["description"]
    assert descriptors["lists.add_items"].input_schema["minProperties"] == 2
    assert descriptors["lists.add_items"].input_schema["maxProperties"] == 2


def test_list_collection_discovery_has_no_model_selected_parameters(tmp_path: Path) -> None:
    _store, _storage, handler, descriptors = _runtime(tmp_path)
    descriptor = descriptors["lists.list_collections"]

    validated = descriptor.validate_arguments({})
    canonical = handler.canonicalize_tool_arguments(
        tool_id=descriptor.tool_id,
        validated_arguments=validated,
        request_context={"requested_by_user_id": "natasha"},
    )

    assert canonical == {}
    assert descriptor.validate_arguments(canonical) == {}


def test_add_items_contract_requires_exactly_one_collection_selector(tmp_path: Path) -> None:
    _store, _storage, _handler, descriptors = _runtime(tmp_path)
    descriptor = descriptors["lists.add_items"]

    assert descriptor.validate_arguments({"name": "weekend", "items": ["milk"]})
    assert descriptor.validate_arguments(
        {"collection_ref": "collection_v1:weekend", "items": ["milk"]}
    )
    with pytest.raises(ToolContractError, match="arguments_property_count_invalid"):
        descriptor.validate_arguments(
            {
                "collection_ref": "collection_v1:weekend",
                "name": "weekend",
                "items": ["milk"],
            }
        )


@pytest.mark.parametrize(
    "items",
    [
        ["milk"],
        ["milk", "eggs"],
        ["milk; whole", "eggs (large)", "bread, sourdough"],
    ],
)
def test_create_then_add_uses_one_items_array_and_preserves_model_interpretation(
    tmp_path: Path,
    items: list[str],
) -> None:
    store, _storage, handler, descriptors = _runtime(tmp_path)
    create = _envelope(
        handler=handler,
        descriptor=descriptors["lists.create_collection"],
        arguments={"name": "Weekend Prep"},
        root_request_id="request-create-add",
        call_ordinal=1,
    )
    created = _execute(handler, create, descriptors["lists.create_collection"])
    collection_ref = created["payload"]["collection"]["collection_ref"]

    add = _envelope(
        handler=handler,
        descriptor=descriptors["lists.add_items"],
        arguments={"collection_ref": collection_ref, "items": items},
        root_request_id="request-create-add",
        call_ordinal=2,
    )
    added = _execute(handler, add, descriptors["lists.add_items"])

    assert created["status"] == "ok"
    assert added["status"] == "ok"
    assert [item["text"] for item in added["payload"]["added_items"]] == items
    assert store.list_list_items(collection_ref.removeprefix("collection_v1:"))
    with store._lock:
        operations = store._conn.execute(
            "SELECT action, status FROM list_operations ORDER BY created_at, action"
        ).fetchall()
    assert {tuple(row) for row in operations} == {
        ("lists.create_collection", "completed"),
        ("lists.add_items", "completed"),
    }


def test_add_items_replays_one_atomic_batch_without_duplicate_rows(tmp_path: Path) -> None:
    store, _storage, handler, descriptors = _runtime(tmp_path)
    create = _envelope(
        handler=handler,
        descriptor=descriptors["lists.create_collection"],
        arguments={"name": "Replay List"},
        root_request_id="request-replay",
        call_ordinal=1,
    )
    created = _execute(handler, create, descriptors["lists.create_collection"])
    collection_ref = created["payload"]["collection"]["collection_ref"]
    add = _envelope(
        handler=handler,
        descriptor=descriptors["lists.add_items"],
        arguments={"collection_ref": collection_ref, "items": ["alpha", "beta"]},
        root_request_id="request-replay",
        call_ordinal=2,
    )

    first = _execute(handler, add, descriptors["lists.add_items"])
    replay = _execute(handler, add, descriptors["lists.add_items"])
    rows = store.list_list_items(collection_ref.removeprefix("collection_v1:"))

    assert first["payload"]["idempotent_replay"] is False
    assert replay["payload"]["idempotent_replay"] is True
    assert [row["item_name"] for row in rows] == ["alpha", "beta"]


def test_exact_name_canonicalizes_but_personal_shared_collision_requires_choice(tmp_path: Path) -> None:
    _store, storage, handler, descriptors = _runtime(tmp_path)
    storage.ensure_list(
        owner_user_id="all",
        list_name="supplies",
        created_by="system",
        timestamp="2026-08-31T00:00:00+00:00",
    )
    storage.ensure_list(
        owner_user_id="natasha",
        list_name="supplies",
        created_by="natasha",
        timestamp="2026-08-31T00:00:00+00:00",
    )
    descriptor = descriptors["lists.add_items"]
    envelope = _envelope(
        handler=handler,
        descriptor=descriptor,
        arguments={"name": "Supplies", "items": ["tape", "glue"]},
        root_request_id="request-ambiguous",
        call_ordinal=1,
    )

    result = _execute(handler, envelope, descriptor)

    assert result["status"] == "needs_input"
    assert len(result["payload"]["candidates"]) == 2
    assert {item["owner_scope"] for item in result["payload"]["candidates"]} == {
        "personal",
        "shared",
    }


def test_foreign_collection_reference_is_rejected_before_envelope_creation(tmp_path: Path) -> None:
    _store, storage, handler, descriptors = _runtime(tmp_path)
    storage.ensure_list(
        owner_user_id="someone-else",
        list_name="private",
        created_by="someone-else",
        timestamp="2026-08-31T00:00:00+00:00",
    )
    foreign = storage.get_list_record(owner_user_id="someone-else", list_name="private")
    descriptor = descriptors["lists.add_items"]
    validated = descriptor.validate_arguments(
        {
            "collection_ref": "collection_v1:" + str(foreign["list_id"]),
            "items": ["do not add"],
        }
    )

    with pytest.raises(ToolArgumentCanonicalizationError, match="lists_collection_not_authorized"):
        handler.canonicalize_tool_arguments(
            tool_id=descriptor.tool_id,
            validated_arguments=validated,
            request_context={"requested_by_user_id": "natasha"},
        )
