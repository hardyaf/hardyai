from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from app.skills.tool_contracts import (
    ToolCallEnvelope,
    ToolContractError,
    ToolDescriptor,
    canonical_arguments_hash,
    canonical_json,
    compile_tool_descriptors,
    tool_child_operation_id,
    tool_operation_id,
)


def _object_schema(
    properties: dict,
    *,
    required: list[str] | None = None,
) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(required if required is not None else properties),
        "properties": properties,
    }


def _descriptor_value(
    *,
    tool_id: str = "fixture.read",
    skill_id: str = "skill.fixture.core",
    contract_version: int = 1,
    input_schema: dict | None = None,
    observation_schema: dict | None = None,
    transfer_fields: list[dict] | None = None,
    runtime_dependencies: list[str] | None = None,
) -> dict:
    return {
        "tool_id": tool_id,
        "skill_id": skill_id,
        "contract_version": contract_version,
        "purpose": "Read one bounded fixture resource.",
        "input_schema": input_schema
        or _object_schema(
            {
                "target": {"type": "string", "minLength": 1, "maxLength": 80},
                "at": {
                    "type": "string",
                    "format": "date-time",
                    "minLength": 20,
                    "maxLength": 40,
                },
            },
            required=["target"],
        ),
        "observation_schema": observation_schema
        or _object_schema(
            {
                "summary": {"type": "string", "minLength": 0, "maxLength": 400},
                "items": {
                    "type": "array",
                    "minItems": 0,
                    "maxItems": 8,
                    "items": _object_schema(
                        {
                            "ref": {"type": "string", "minLength": 1, "maxLength": 80},
                            "label": {"type": "string", "minLength": 0, "maxLength": 120},
                        }
                    ),
                },
            }
        ),
        "effect": "read",
        "approval_rule": "none",
        "approval_conditions": [],
        "sensitivity": "private",
        "persistence": "redacted",
        "idempotency": "not_applicable",
        "effect_cardinality": "single",
        "transferable_observation_fields": transfer_fields or [],
        "runtime_dependencies": runtime_dependencies or [],
        "timeout_seconds": 10,
        "max_result_items": 8,
        "max_observation_chars": 1000,
        "legacy_intents": ["fixture.read"],
        "interactive": True,
    }


def test_descriptor_is_immutable_closed_bounded_and_json_serializable() -> None:
    descriptor = ToolDescriptor.from_mapping(_descriptor_value())

    assert json.loads(json.dumps(descriptor.to_storage_dict()))["tool_id"] == "fixture.read"
    with pytest.raises(FrozenInstanceError):
        descriptor.purpose = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        descriptor.input_schema["type"] = "array"
    with pytest.raises(TypeError):
        descriptor.input_schema["properties"]["target"]["maxLength"] = 500

    unknown = _descriptor_value()
    unknown["provider_setting"] = "must-not-compile"
    with pytest.raises(ToolContractError, match="descriptor_shape_invalid"):
        ToolDescriptor.from_mapping(unknown)

    unbounded = _descriptor_value()
    unbounded["input_schema"]["properties"]["target"].pop("maxLength")
    with pytest.raises(ToolContractError, match="schema_string_bounds_invalid"):
        ToolDescriptor.from_mapping(unbounded)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("effect", "execute_anything", "effect_invalid"),
        ("approval_rule", "maybe", "approval_rule_invalid"),
        ("sensitivity", "secret", "sensitivity_invalid"),
        ("persistence", "forever", "persistence_invalid"),
        ("idempotency", "optional", "idempotency_invalid"),
        ("effect_cardinality", "many", "effect_cardinality_invalid"),
        ("runtime_dependencies", ["raw_provider_worker"], "runtime_dependency_invalid"),
    ],
)
def test_descriptor_rejects_unknown_closed_values(field: str, value: object, code: str) -> None:
    raw = _descriptor_value()
    raw[field] = value
    with pytest.raises(ToolContractError, match=code):
        ToolDescriptor.from_mapping(raw)


def test_descriptor_requires_explicit_cardinality_dependencies_and_transfer_contract() -> None:
    for field in (
        "effect_cardinality",
        "runtime_dependencies",
        "transferable_observation_fields",
    ):
        raw = _descriptor_value()
        raw.pop(field)
        with pytest.raises(ToolContractError, match="descriptor_shape_invalid"):
            ToolDescriptor.from_mapping(raw)


def test_transfer_patterns_are_schema_checked_and_non_overlapping() -> None:
    descriptor = ToolDescriptor.from_mapping(
        _descriptor_value(
            transfer_fields=[
                {"pattern": "/summary", "scope": "same_domain"},
                {"pattern": "/items/*/ref", "scope": "cross_domain"},
            ]
        )
    )
    assert [item.to_dict() for item in descriptor.transferable_observation_fields] == [
        {"pattern": "/summary", "scope": "same_domain"},
        {"pattern": "/items/*/ref", "scope": "cross_domain"},
    ]

    for fields, code in (
        ([{"pattern": "summary", "scope": "same_domain"}], "transfer_pattern_invalid"),
        ([{"pattern": "/missing", "scope": "same_domain"}], "transfer_pattern_schema_mismatch"),
        ([{"pattern": "/*", "scope": "same_domain"}], "transfer_pattern_schema_mismatch"),
        (
            [
                {"pattern": "/items/*", "scope": "same_domain"},
                {"pattern": "/items/0/ref", "scope": "same_domain"},
            ],
            "transfer_pattern_overlap",
        ),
    ):
        with pytest.raises(ToolContractError, match=code):
            ToolDescriptor.from_mapping(_descriptor_value(transfer_fields=fields))


def test_argument_validation_is_closed_and_normalizes_iso_instants() -> None:
    descriptor = ToolDescriptor.from_mapping(_descriptor_value())
    arguments = descriptor.validate_arguments(
        {"target": "caf\N{LATIN SMALL LETTER E WITH ACUTE}", "at": "2026-08-30T18:30:00-04:00"}
    )
    assert arguments == {"target": "caf\N{LATIN SMALL LETTER E WITH ACUTE}", "at": "2026-08-30T22:30:00Z"}
    with pytest.raises(ToolContractError, match="arguments_unknown_field"):
        descriptor.validate_arguments({"target": "one", "execution_ref": "forged"})
    with pytest.raises(ToolContractError, match="arguments_required_field_missing"):
        descriptor.validate_arguments({})


def _email_arguments(refs: list[str]) -> dict:
    return {"message_refs": refs, "label": "managed_updates"}


def test_operation_identity_is_stable_across_order_delivery_and_reopen() -> None:
    first = tool_operation_id(
        root_request_id="discord-delivery-1",
        tool_id="email.mark_read_complete",
        contract_version=1,
        call_ordinal=2,
        arguments=_email_arguments(["msg-b", "msg-a"]),
    )
    reordered = tool_operation_id(
        root_request_id="discord-delivery-1",
        tool_id="email.mark_read_complete",
        contract_version=1,
        call_ordinal=2,
        arguments={"label": "managed_updates", "message_refs": ["msg-a", "msg-b"]},
    )
    reopened_arguments = json.loads(canonical_json(first[2]))
    reopened = tool_operation_id(
        root_request_id="discord-delivery-1",
        tool_id="email.mark_read_complete",
        contract_version=1,
        call_ordinal=2,
        arguments=reopened_arguments,
    )
    assert first == reordered == reopened
    assert canonical_arguments_hash(
        _email_arguments(["msg-a", "msg-b"]),
        tool_id="email.mark_read_complete",
    ) == first[1]
    with pytest.raises(ToolContractError, match="email_message_ref_duplicate"):
        canonical_arguments_hash(
            _email_arguments(["msg-a", "msg-a"]),
            tool_id="email.mark_read_complete",
        )


def test_operation_and_child_identity_conflict_on_every_locked_component() -> None:
    base = tool_operation_id(
        root_request_id="root-1",
        tool_id="email.mark_read_complete",
        contract_version=1,
        call_ordinal=1,
        arguments=_email_arguments(["msg-a"]),
    )[0]
    variants = [
        tool_operation_id(
            root_request_id="root-2",
            tool_id="email.mark_read_complete",
            contract_version=1,
            call_ordinal=1,
            arguments=_email_arguments(["msg-a"]),
        )[0],
        tool_operation_id(
            root_request_id="root-1",
            tool_id="email.move_to_spam",
            contract_version=1,
            call_ordinal=1,
            arguments=_email_arguments(["msg-a"]),
        )[0],
        tool_operation_id(
            root_request_id="root-1",
            tool_id="email.mark_read_complete",
            contract_version=2,
            call_ordinal=1,
            arguments=_email_arguments(["msg-a"]),
        )[0],
        tool_operation_id(
            root_request_id="root-1",
            tool_id="email.mark_read_complete",
            contract_version=1,
            call_ordinal=2,
            arguments=_email_arguments(["msg-a"]),
        )[0],
        tool_operation_id(
            root_request_id="root-1",
            tool_id="email.mark_read_complete",
            contract_version=1,
            call_ordinal=1,
            arguments=_email_arguments(["msg-b"]),
        )[0],
    ]
    assert len({base, *variants}) == 6

    child = tool_child_operation_id(
        operation_id=base,
        child_index=1,
        canonical_target_ref="msg-a",
        child_arguments={"state": "queued"},
    )[0]
    child_variants = {
        tool_child_operation_id(
            operation_id=variants[0],
            child_index=1,
            canonical_target_ref="msg-a",
            child_arguments={"state": "queued"},
        )[0],
        tool_child_operation_id(
            operation_id=base,
            child_index=2,
            canonical_target_ref="msg-a",
            child_arguments={"state": "queued"},
        )[0],
        tool_child_operation_id(
            operation_id=base,
            child_index=1,
            canonical_target_ref="msg-b",
            child_arguments={"state": "queued"},
        )[0],
        tool_child_operation_id(
            operation_id=base,
            child_index=1,
            canonical_target_ref="msg-a",
            child_arguments={"state": "committed"},
        )[0],
    }
    assert child not in child_variants
    assert len(child_variants) == 4


def test_envelope_binds_server_fields_descriptor_and_arguments() -> None:
    descriptor = ToolDescriptor.from_mapping(_descriptor_value())
    arguments = descriptor.validate_arguments({"target": "fixture"})
    envelope = ToolCallEnvelope.create(
        root_request_id="request-1",
        call_ordinal=1,
        session_id="session-1",
        principal_kind="discord_user",
        principal_subject="subject-1",
        user_id="operator",
        agent_id="jarvis",
        source_interface="discord",
        channel_scope="private-channel",
        skill_id="skill.fixture.core",
        descriptor=descriptor,
        authorization_snapshot_ref="authz_v1_fixture",
        validated_arguments=arguments,
    )
    serialized = envelope.to_dict()
    assert serialized["operation_id"].startswith("toolop_v1_")
    assert serialized["arguments"] == {"target": "fixture"}
    with pytest.raises(TypeError):
        envelope.arguments["target"] = "changed"

    tampered = dict(serialized)
    tampered["operation_id"] = "toolop_v1_" + "0" * 64
    with pytest.raises(ToolContractError, match="envelope_operation_identity_mismatch"):
        ToolCallEnvelope(**tampered)


def test_compiler_fails_closed_per_invalid_tool_with_content_free_diagnostics() -> None:
    valid = _descriptor_value()
    valid.pop("skill_id")
    invalid = dict(valid)
    invalid["tool_id"] = "fixture.invalid"
    invalid["effect"] = "provider_admin"
    descriptors, diagnostics = compile_tool_descriptors(
        skill_id="skill.fixture.core",
        contract_version=1,
        declarations=[valid, invalid],
    )
    assert [item.tool_id for item in descriptors] == ["fixture.read"]
    assert diagnostics == ({"code": "effect_invalid", "tool_id": "fixture.invalid"},)
    assert "provider" not in json.dumps(diagnostics)


def test_model_projection_contains_no_server_or_legacy_internals() -> None:
    raw = _descriptor_value()
    raw["purpose"] = "Read a fixture; api_key=do-not-project."
    raw["input_schema"]["properties"]["target"]["description"] = (
        "Internal provider settings must not be projected."
    )
    projection = ToolDescriptor.from_mapping(raw).to_model_projection(
        availability_note=(
            "Available via /etc/private/settings for 111111111111111111; "
            "access_token=do-not-project"
        )
    )
    serialized = json.dumps(projection, sort_keys=True)
    for forbidden in (
        "skill_id",
        "contract_version",
        "runtime_dependencies",
        "legacy_intents",
        "principal",
        "execution_ref",
        "storage_ref",
        "/etc/private/settings",
        "111111111111111111",
        "do-not-project",
        "Internal provider settings",
    ):
        assert forbidden not in serialized

    protected_schema = _descriptor_value()
    protected_schema["input_schema"]["properties"]["api_key"] = {
        "type": "string",
        "minLength": 1,
        "maxLength": 100,
    }
    with pytest.raises(ToolContractError, match="schema_protected_field_invalid"):
        ToolDescriptor.from_mapping(protected_schema)
