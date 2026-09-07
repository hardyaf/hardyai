from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import yaml

from app.core.tool_loop_types import validate_descriptor_payload
from app.db.sqlite_store import SQLiteStore
from app.skills.domains.lights.handler import HomeToolHandler
from app.skills.domains.lights.receipts import build_operation_receipt
from app.skills.domains.lights.service import HomeService
from app.skills.tool_contracts import ToolCallEnvelope, ToolDescriptor, compile_tool_descriptors


def _home_descriptors() -> dict[str, ToolDescriptor]:
    text = Path("app/prompts/skills/lights_skill.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    descriptors, diagnostics = compile_tool_descriptors(
        skill_id=HomeToolHandler.SKILL_ID,
        contract_version=frontmatter["main_tools_contract_version"],
        declarations=frontmatter["main_tools"],
    )
    assert diagnostics == ()
    return {item.tool_id: item for item in descriptors}


def _home_envelope(
    *,
    handler: HomeToolHandler,
    descriptor: ToolDescriptor,
    arguments: dict,
    call_ordinal: int = 1,
) -> ToolCallEnvelope:
    context = {
        "requested_by_user_id": "operator",
        "agent_id": "jarvis",
        "source_interface": "discord",
        "discord_channel_id": "private-home",
        "session_id": "session-home",
    }
    validated = descriptor.validate_arguments(arguments)
    canonical = handler.canonicalize_tool_arguments(
        tool_id=descriptor.tool_id,
        validated_arguments=validated,
        request_context=context,
    )
    canonical = descriptor.validate_arguments(canonical)
    return ToolCallEnvelope.create(
        root_request_id="home-read-request",
        call_ordinal=call_ordinal,
        session_id="session-home",
        principal_kind="user",
        principal_subject="operator",
        user_id="operator",
        agent_id="jarvis",
        source_interface="discord",
        channel_scope="private-home",
        skill_id=HomeToolHandler.SKILL_ID,
        descriptor=descriptor,
        authorization_snapshot_ref="authz-home-test",
        validated_arguments=canonical,
    )


def _execute_home_read(
    handler: HomeToolHandler,
    descriptor: ToolDescriptor,
    arguments: dict,
    *,
    call_ordinal: int = 1,
) -> tuple[ToolCallEnvelope, dict]:
    envelope = _home_envelope(
        handler=handler,
        descriptor=descriptor,
        arguments=arguments,
        call_ordinal=call_ordinal,
    )
    result = handler.execute_tool(envelope=envelope, services={})
    if result.get("status") in {"ok", "needs_input"}:
        validate_descriptor_payload(descriptor, result.get("payload") or {}, observation=True)
    return envelope, result


def test_home_service_persists_switch_state_across_instances():
    data_root = (Path.cwd() / "data").resolve()
    if not data_root.exists():
        data_root = (Path.cwd() / "jarvis_poc" / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-home-test-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "home_service.db"
        store = SQLiteStore(database_path=str(db_path))

        service_one = HomeService(sqlite_store=store, default_switch_names=["office test light"])
        first = service_one.set_switch(
            switch_name="office test light",
            action="on",
            source_interface="dashboard",
            requested_by_user_id="jordan",
        )
        assert first["status"] == "ok"

        # Simulate restart by creating a new service instance over the same DB.
        service_two = HomeService(sqlite_store=store, default_switch_names=["office test light"])
        switches = service_two.list_switches()
        office = next(item for item in switches if item["name"] == "office test light")
        assert office["state"] == "on"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_home_service_resolves_alias_to_existing_named_switch():
    data_root = (Path.cwd() / "data").resolve()
    if not data_root.exists():
        data_root = (Path.cwd() / "jarvis_poc" / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-home-alias-test-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "home_alias.db"
        store = SQLiteStore(database_path=str(db_path))
        service = HomeService(sqlite_store=store, default_switch_names=["office test light"])

        result = service.set_switch(
            switch_name="office light",
            action="on",
            source_interface="dashboard",
            requested_by_user_id="jordan",
        )
        assert result["status"] == "ok"
        assert result["switch_name"] == "office test light"
        assert result["matched_existing"] is True

        switches = service.list_switches()
        office = next(item for item in switches if item["name"] == "office test light")
        assert office["state"] == "on"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_home_service_resolves_single_token_alias_to_existing_named_switch():
    data_root = (Path.cwd() / "data").resolve()
    if not data_root.exists():
        data_root = (Path.cwd() / "jarvis_poc" / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-home-short-alias-test-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "home_short_alias.db"
        store = SQLiteStore(database_path=str(db_path))
        service = HomeService(sqlite_store=store, default_switch_names=["office test light"])

        result = service.set_switch(
            switch_name="office",
            action="on",
            source_interface="dashboard",
            requested_by_user_id="jordan",
        )
        assert result["status"] == "ok"
        assert result["switch_name"] == "office test light"
        assert result["matched_existing"] is True
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_home_service_does_not_create_unknown_switches():
    data_root = (Path.cwd() / "data").resolve()
    if not data_root.exists():
        data_root = (Path.cwd() / "jarvis_poc" / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-home-unknown-test-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "home_unknown.db"
        store = SQLiteStore(database_path=str(db_path))
        service = HomeService(sqlite_store=store, default_switch_names=["office test light"])

        result = service.set_switch(
            switch_name="garage floodlight",
            action="on",
            source_interface="dashboard",
            requested_by_user_id="jordan",
        )
        assert result["status"] == "unknown_switch"

        switches = service.list_switches()
        names = [item["name"] for item in switches]
        assert names == ["office test light"]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_home_service_can_toggle_all_lights_without_creating_new_switches():
    data_root = (Path.cwd() / "data").resolve()
    if not data_root.exists():
        data_root = (Path.cwd() / "jarvis_poc" / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-home-all-test-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "home_all.db"
        store = SQLiteStore(database_path=str(db_path))
        service = HomeService(
            sqlite_store=store,
            default_switch_names=["office test light", "kitchen light", "living room lamp"],
        )

        result = service.set_switch(
            switch_name="all lights",
            action="on",
            source_interface="dashboard",
            requested_by_user_id="jordan",
        )
        assert result["status"] == "ok"
        assert result["scope"] == "all"
        assert result["affected_count"] == 3

        switches = service.list_switches()
        assert len(switches) == 3
        assert all(item["state"] == "on" for item in switches)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_home_markdown_publishes_reads_and_one_exact_write() -> None:
    descriptors = _home_descriptors()

    assert set(descriptors) == {
        "home.list_devices",
        "home.get_device_state",
        "home.set_device_state",
    }
    assert descriptors["home.list_devices"].effect == "read"
    assert descriptors["home.get_device_state"].effect == "read"
    assert descriptors["home.set_device_state"].effect == "local_write"
    assert descriptors["home.set_device_state"].input_schema["required"] == (
        "device_ref",
        "state",
    )
    assert descriptors["home.list_devices"].legacy_intents == ("home.list_switches",)
    assert descriptors["home.get_device_state"].legacy_intents == (
        "home.get_switch_state",
    )
    model_tools = [item.to_model_projection(availability_note="Available.") for item in descriptors.values()]
    serialized = str(model_tools)
    assert "home.list_switches" not in serialized
    assert "home.get_switch_state" not in serialized


def test_typed_home_device_listing_is_bounded_opaque_simulated_and_read_only() -> None:
    service = HomeService(
        default_switch_names=[
            "office test light",
            "kitchen light",
            "living room lamp",
        ]
    )
    handler = HomeToolHandler(home_service=service)
    descriptor = _home_descriptors()["home.list_devices"]
    before_actions = service.recent_actions(limit=100)

    _envelope, result = _execute_home_read(
        handler,
        descriptor,
        {"limit": 2},
    )

    assert result["status"] == "ok"
    assert result["payload"]["simulated"] is True
    assert result["payload"]["source"] == "local_simulated_state"
    assert result["payload"]["truncated"] is True
    assert len(result["payload"]["devices"]) == 2
    assert all(item["device_ref"].startswith("device_v1:") for item in result["payload"]["devices"])
    assert all(len(item["alias_hints"]) <= 8 for item in result["payload"]["devices"])
    assert service.recent_actions(limit=100) == before_actions


def test_typed_home_state_resolves_exact_name_and_unique_alias_to_same_ref() -> None:
    service = HomeService(default_switch_names=["office test light", "kitchen light"])
    handler = HomeToolHandler(home_service=service)
    descriptor = _home_descriptors()["home.get_device_state"]

    exact_envelope, exact = _execute_home_read(
        handler,
        descriptor,
        {"name": "office test light"},
    )
    alias_envelope, alias = _execute_home_read(
        handler,
        descriptor,
        {"name": "office"},
        call_ordinal=2,
    )

    assert exact["status"] == alias["status"] == "ok"
    assert exact["payload"]["device"] == alias["payload"]["device"]
    assert exact["payload"]["device"]["state"] == "off"
    assert exact["payload"]["simulated"] is alias["payload"]["simulated"] is True
    assert set(exact_envelope.arguments) == {"device_ref"}
    assert exact_envelope.arguments == alias_envelope.arguments
    assert service.recent_actions(limit=100) == []


def test_typed_home_write_is_exact_atomic_and_idempotent(tmp_path: Path) -> None:
    store = SQLiteStore(database_path=str(tmp_path / "home-write.db"))
    service = HomeService(sqlite_store=store, default_switch_names=["office test light"])
    handler = HomeToolHandler(home_service=service)
    descriptors = _home_descriptors()
    device_ref = service.list_devices(limit=1)["devices"][0]["device_ref"]
    descriptor = descriptors["home.set_device_state"]
    envelope = _home_envelope(
        handler=handler,
        descriptor=descriptor,
        arguments={"device_ref": device_ref, "state": "on"},
    )

    first = handler.execute_tool(envelope=envelope, services={})
    replay = handler.execute_tool(envelope=envelope, services={})

    validate_descriptor_payload(descriptor, first["payload"], observation=True)
    validate_descriptor_payload(descriptor, replay["payload"], observation=True)
    assert first["payload"]["changed"] is True
    assert first["payload"]["idempotent_replay"] is False
    assert replay["payload"]["changed"] is False
    assert replay["payload"]["idempotent_replay"] is True
    assert len(service.recent_actions(limit=100)) == 1

    restarted = HomeService(sqlite_store=store)
    replay_after_restart = HomeToolHandler(home_service=restarted).execute_tool(
        envelope=envelope,
        services={},
    )
    assert replay_after_restart["payload"]["idempotent_replay"] is True
    assert len(restarted.recent_actions(limit=100)) == 1


def test_typed_home_write_rejects_stale_ref_and_has_no_group_surface() -> None:
    service = HomeService(default_switch_names=["office test light", "kitchen light"])
    handler = HomeToolHandler(home_service=service)
    descriptor = _home_descriptors()["home.set_device_state"]

    _envelope, stale = _execute_home_read(
        handler,
        descriptor,
        {"device_ref": "device_v1:00000000000000000000000000000000", "state": "on"},
    )

    assert stale["status"] == "needs_input"
    assert stale["payload"]["match_status"] == "stale_reference"
    assert service.recent_actions(limit=100) == []
    assert set(descriptor.input_schema["properties"]) == {"device_ref", "state"}


def test_typed_home_state_returns_candidates_for_ambiguous_alias_without_guessing() -> None:
    service = HomeService(
        default_switch_names=["office test light", "office ceiling light", "kitchen light"]
    )
    handler = HomeToolHandler(home_service=service)
    descriptor = _home_descriptors()["home.get_device_state"]

    envelope, result = _execute_home_read(handler, descriptor, {"name": "office"})

    assert envelope.arguments == {"name": "office"}
    assert result["status"] == "needs_input"
    assert result["missing_fields"] == ["device_ref"]
    assert result["payload"]["simulated"] is True
    assert [item["name"] for item in result["payload"]["candidates"]] == [
        "office ceiling light",
        "office test light",
    ]
    assert service.recent_actions(limit=100) == []


def test_typed_home_state_rejects_stale_ref_and_missing_name_without_action() -> None:
    service = HomeService(default_switch_names=["office test light", "kitchen light"])
    handler = HomeToolHandler(home_service=service)
    descriptor = _home_descriptors()["home.get_device_state"]

    _stale_envelope, stale = _execute_home_read(
        handler,
        descriptor,
        {"device_ref": "device_v1:00000000000000000000000000000000"},
    )
    _missing_envelope, missing = _execute_home_read(
        handler,
        descriptor,
        {"name": "garage floodlight"},
        call_ordinal=2,
    )

    assert stale["status"] == missing["status"] == "needs_input"
    assert stale["payload"]["match_status"] == "stale_reference"
    assert missing["payload"]["match_status"] == "not_found"
    assert len(stale["payload"]["candidates"]) <= 3
    assert len(missing["payload"]["candidates"]) <= 3
    assert service.recent_actions(limit=100) == []


def test_home_read_receipts_use_canonical_names_and_retain_legacy_alias() -> None:
    service = HomeService(default_switch_names=["office test light"])
    handler = HomeToolHandler(home_service=service)
    descriptor = _home_descriptors()["home.get_device_state"]
    _envelope, state_result = _execute_home_read(
        handler,
        descriptor,
        {"name": "office"},
    )

    canonical = build_operation_receipt(
        intent="home.get_device_state",
        entities={"name": "office"},
        context={"request_id": "canonical-read"},
        result=state_result,
        services={"home_service": service},
    )
    legacy = build_operation_receipt(
        intent="home.get_switch_state",
        entities={"name": "office"},
        context={"request_id": "legacy-read"},
        result=state_result,
        services={"home_service": service},
    )

    assert canonical is not None and legacy is not None
    assert canonical["capability"] == legacy["capability"] == "home.get_device_state"
    assert canonical["action"] == legacy["action"] == "get_device_state"
    assert "legacy_capability" not in canonical
    assert legacy["legacy_capability"] == "home.get_switch_state"
    assert service.recent_actions(limit=100) == []
