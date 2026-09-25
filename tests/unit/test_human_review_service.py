from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from app.reviews.repository import HumanReviewRepository
from app.reviews.service import (
    HumanReviewService,
    action_channel_binding_hash,
    action_request_binding_hash,
)
from app.skills.tool_contracts import ToolCallEnvelope, ToolDescriptor, canonical_json


HASH = "a" * 64


def _action_descriptor(*, cardinality: str = "single", persistence: str = "standard"):
    input_schema = (
        {
            "type": "object",
            "properties": {
                "targets": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 80},
                    "minItems": 1,
                    "maxItems": 10,
                    "uniqueItems": True,
                }
            },
            "required": ["targets"],
            "additionalProperties": False,
            "minProperties": 1,
            "maxProperties": 1,
        }
        if cardinality == "independent_batch"
        else {
            "type": "object",
            "properties": {"target": {"type": "string", "minLength": 1, "maxLength": 80}},
            "required": ["target"],
            "additionalProperties": False,
            "minProperties": 1,
            "maxProperties": 1,
        }
    )
    return ToolDescriptor.from_mapping(
        {
            "tool_id": "synthetic.write",
            "skill_id": "skill.synthetic",
            "contract_version": 1,
            "purpose": "Perform one synthetic reversible write.",
            "input_schema": input_schema,
            "observation_schema": {
                "type": "object",
                "properties": {"changed": {"type": "boolean"}},
                "required": ["changed"],
                "additionalProperties": False,
                "minProperties": 1,
                "maxProperties": 1,
            },
            "effect": "local_write",
            "approval_rule": "always",
            "approval_conditions": [],
            "sensitivity": "private",
            "persistence": persistence,
            "idempotency": "required",
            "effect_cardinality": cardinality,
            "transferable_observation_fields": [],
            "runtime_dependencies": ["action_approval"],
            "timeout_seconds": 30,
            "max_result_items": 1,
            "max_observation_chars": 500,
            "legacy_intents": [],
            "interactive": True,
        }
    )


def _action_envelope(descriptor: ToolDescriptor, *, request_id: str = "request-1"):
    return ToolCallEnvelope.create(
        root_request_id=request_id,
        call_ordinal=1,
        session_id="session-1",
        principal_kind="discord_adapter",
        principal_subject="discord_user:requester",
        external_user_id="requester",
        user_id="user-1",
        agent_id="catparty",
        source_interface="discord",
        channel_scope="channel-private",
        skill_id=descriptor.skill_id,
        descriptor=descriptor,
        authorization_snapshot_ref="authz_v1_" + "d" * 64,
        validated_arguments=(
            {"targets": ["alpha", "beta"]}
            if descriptor.effect_cardinality == "independent_batch"
            else {"target": "lamp"}
        ),
    )


def _review(service: HumanReviewService, **overrides):
    values = {
        "review_kind": "field_correction",
        "subject_type": "synthetic_domain_record",
        "subject_id": "subject-1",
        "subject_version": "version-1",
        "item_hash": HASH,
        "sensitivity": "private",
        "validator_summary": [{"code": "low_confidence", "passed": False}],
        "evidence_refs": ["opaque:evidence:1"],
    }
    values.update(overrides)
    return service.create_review(**values)


def test_review_is_generic_hash_bound_and_decision_is_idempotent(tmp_path) -> None:
    repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = HumanReviewService(repository)
    review = _review(service)
    assert review["subject_type"] == "synthetic_domain_record"
    assert review["state"] == "pending"

    with pytest.raises(ValueError, match="review_version_changed"):
        service.decide(
            review_id=review["review_id"],
            bound_item_hash="b" * 64,
            decision="approve",
            actor_principal="operator:1",
            reason="Verified against the source.",
            idempotency_key="decision-wrong",
        )
    decision = service.decide(
        review_id=review["review_id"],
        bound_item_hash=HASH,
        decision="approve",
        actor_principal="operator:1",
        reason="Verified against the source.",
        idempotency_key="decision-1",
    )
    repeated = service.decide(
        review_id=review["review_id"],
        bound_item_hash=HASH,
        decision="approve",
        actor_principal="operator:1",
        reason="Repeated request.",
        idempotency_key="decision-1",
    )
    assert decision["decision_id"] == repeated["decision_id"]
    assert repository.get(review["review_id"])["state"] == "approved"
    assert repository.mark_applied(decision_id=decision["decision_id"], action_receipt_ref="receipt:1")
    assert repository.get(review["review_id"])["state"] == "executed"
    repository.close()


def test_review_expiry_and_supersession_are_explicit(tmp_path) -> None:
    repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = HumanReviewService(repository)
    expired = _review(
        service,
        subject_id="expired",
        expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    )
    assert repository.get(expired["review_id"])["state"] == "expired"

    original = _review(service, subject_id="replace", subject_version="v1")
    replacement = _review(service, subject_id="replace", subject_version="v2", item_hash="c" * 64)
    assert repository.supersede(
        review_id=original["review_id"],
        replacement_review_id=replacement["review_id"],
    )
    persisted = repository.get(original["review_id"])
    assert persisted["state"] == "superseded"
    assert persisted["superseded_by_review_id"] == replacement["review_id"]
    repository.close()


def test_action_proposal_review_and_notification_commit_atomically_and_conflict_exactly(
    tmp_path,
) -> None:
    repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = HumanReviewService(repository)
    descriptor = _action_descriptor()
    envelope = _action_envelope(descriptor)
    expires_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    created = service.create_action_proposal(
        envelope=envelope,
        descriptor=descriptor,
        resource_version="resource-v1",
        approver_principal="discord_user:approver",
        expires_at=expires_at,
    )
    repeated = service.create_action_proposal(
        envelope=envelope,
        descriptor=descriptor,
        resource_version="resource-v1",
        approver_principal="discord_user:approver",
        expires_at=expires_at,
    )
    proposal = created["proposal"]
    assert repeated["proposal"]["proposal_id"] == proposal["proposal_id"]
    assert created["review"]["state"] == "pending"
    assert created["notification_job"]["job_type"] == "review.notification.discord.v1"
    assert created["notification_job"]["payload"] == {
        "proposal_id": proposal["proposal_id"],
        "review_id": proposal["review_id"],
        "operation_id": envelope.operation_id,
        "authorization_binding": envelope.authorization_snapshot_ref,
        "batch_manifest_hash": None,
        "transfer_binding_hash": None,
        "destination_purpose": "human_reviews",
    }
    assert len(repository.job_repository.list_jobs(job_type="review.notification.discord.v1")) == 1

    with pytest.raises(ValueError, match="action_proposal_idempotency_conflict"):
        service.create_action_proposal(
            envelope=envelope,
            descriptor=descriptor,
            resource_version="resource-v1",
            approver_principal="discord_user:approver",
            expires_at=expires_at,
            safe_action_summary="A conflicting summary.",
        )
    with pytest.raises(ValueError, match="action_review_requires_bound_decision"):
        service.decide(
            review_id=proposal["review_id"],
            bound_item_hash=proposal["proposal_hash"],
            decision="approve",
            actor_principal="operator:1",
            reason="Bypass attempt.",
            idempotency_key="bypass-action-review",
        )
    repository.close()


def test_action_decision_requires_bound_actor_channel_and_enqueues_execution_once(tmp_path) -> None:
    repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = HumanReviewService(repository)
    descriptor = _action_descriptor()
    created = service.create_action_proposal(
        envelope=_action_envelope(descriptor),
        descriptor=descriptor,
        resource_version="resource-v1",
        approver_principal="discord_user:approver",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )
    proposal = created["proposal"]
    repository.mark_action_notification_delivered(
        proposal_id=proposal["proposal_id"],
        destination_purpose="human_reviews",
        guild_id="guild-private",
        channel_id="channel-review",
        message_id="card-message",
    )
    values = {
        "proposal_id": proposal["proposal_id"],
        "review_id": proposal["review_id"],
        "bound_proposal_hash": proposal["proposal_hash"],
        "decision": "approve",
        "actor_principal": "discord_user:approver",
        "destination_purpose": "human_reviews",
        "guild_id": "guild-private",
        "channel_id": "channel-review",
        "message_id": "decision-message",
        "reason": "Approved after checking the bounded card.",
        "idempotency_key": "decision-action-1",
    }
    with pytest.raises(PermissionError, match="action_decision_actor_denied"):
        service.decide_action_proposal(**{**values, "actor_principal": "discord_user:other"})
    with pytest.raises(PermissionError, match="action_decision_channel_denied"):
        service.decide_action_proposal(**{**values, "channel_id": "channel-other"})

    decided = service.decide_action_proposal(**values)
    repeated = service.decide_action_proposal(**values)
    assert repeated["decision"]["decision_id"] == decided["decision"]["decision_id"]
    assert decided["proposal"]["state"] == "approved"
    assert decided["execution_job"]["job_type"] == "review.action_execution.v1"
    assert len(repository.job_repository.list_jobs(job_type="review.action_execution.v1")) == 1
    with pytest.raises(ValueError, match="action_decision_idempotency_conflict"):
        service.decide_action_proposal(**{**values, "reason": "Changed reason."})
    repository.close()


def test_task_workspace_approval_is_hash_bound_without_discord_delivery(tmp_path) -> None:
    repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = HumanReviewService(repository)
    descriptor = _action_descriptor()
    created = service.create_action_proposal(
        envelope=_action_envelope(descriptor, request_id="request-local-task"),
        descriptor=descriptor,
        resource_version="resource-v1",
        approver_principal="operator",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        destination_purpose="task_workspace",
    )
    proposal = created["proposal"]

    assert created["notification_job"] is None
    assert not repository.job_repository.list_jobs(
        job_type="review.notification.discord.v1"
    )
    with pytest.raises(PermissionError, match="action_decision_actor_denied"):
        service.decide_action_proposal(
            proposal_id=proposal["proposal_id"],
            review_id=proposal["review_id"],
            bound_proposal_hash=proposal["proposal_hash"],
            decision="approve",
            actor_principal="someone-else",
            destination_purpose="task_workspace",
            guild_id="",
            channel_id="",
            message_id="",
            reason="Wrong owner.",
            idempotency_key="decision-local-wrong-owner",
        )
    decided = service.decide_action_proposal(
        proposal_id=proposal["proposal_id"],
        review_id=proposal["review_id"],
        bound_proposal_hash=proposal["proposal_hash"],
        decision="approve",
        actor_principal="operator",
        destination_purpose="task_workspace",
        guild_id="",
        channel_id="",
        message_id="",
        reason="Approved in the local task workspace.",
        idempotency_key="decision-local-correct-owner",
    )
    assert decided["proposal"]["state"] == "approved"
    assert decided["execution_job"]["job_type"] == "review.action_execution.v1"
    repository.close()


def test_rejection_is_terminal_and_clears_purpose_bound_arguments(tmp_path) -> None:
    repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = HumanReviewService(repository)
    descriptor = _action_descriptor()
    created = service.create_action_proposal(
        envelope=_action_envelope(descriptor, request_id="request-reject"),
        descriptor=descriptor,
        resource_version="resource-v1",
        approver_principal="discord_user:approver",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )
    proposal = created["proposal"]
    repository.mark_action_notification_delivered(
        proposal_id=proposal["proposal_id"],
        destination_purpose="human_reviews",
        guild_id="guild-private",
        channel_id="channel-review",
        message_id="card-reject",
    )
    result = service.decide_action_proposal(
        proposal_id=proposal["proposal_id"],
        review_id=proposal["review_id"],
        bound_proposal_hash=proposal["proposal_hash"],
        decision="reject",
        actor_principal="discord_user:approver",
        destination_purpose="human_reviews",
        guild_id="guild-private",
        channel_id="channel-review",
        message_id="decision-reject",
        reason="Rejected.",
        idempotency_key="decision-action-reject",
    )
    assert result["proposal"]["state"] == "rejected"
    assert result["proposal"]["destination_arguments"] is None
    assert result["execution_job"] is None
    repository.close()


def test_action_expiry_cancels_unclaimed_notification_and_execution_jobs(tmp_path) -> None:
    repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = HumanReviewService(repository)
    descriptor = _action_descriptor()
    pending = service.create_action_proposal(
        envelope=_action_envelope(descriptor, request_id="request-expire-pending"),
        descriptor=descriptor,
        resource_version="resource-v1",
        approver_principal="discord_user:approver",
        expires_at=(datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    )
    after_expiry = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()
    repository.expire_due(now=after_expiry)
    assert repository.get_action_proposal(pending["proposal"]["proposal_id"])["state"] == "expired"
    assert repository.job_repository.get_job(pending["notification_job"]["job_id"])[
        "status"
    ] == "cancelled"

    approved = service.create_action_proposal(
        envelope=_action_envelope(descriptor, request_id="request-expire-approved"),
        descriptor=descriptor,
        resource_version="resource-v1",
        approver_principal="discord_user:approver",
        expires_at=(datetime.now(UTC) + timedelta(minutes=3)).isoformat(),
    )
    proposal = approved["proposal"]
    repository.mark_action_notification_delivered(
        proposal_id=proposal["proposal_id"],
        destination_purpose="human_reviews",
        guild_id="guild-private",
        channel_id="channel-review",
        message_id="card-expire-approved",
    )
    decided = service.decide_action_proposal(
        proposal_id=proposal["proposal_id"],
        review_id=proposal["review_id"],
        bound_proposal_hash=proposal["proposal_hash"],
        decision="approve",
        actor_principal="discord_user:approver",
        destination_purpose="human_reviews",
        guild_id="guild-private",
        channel_id="channel-review",
        message_id="decision-expire-approved",
        reason="Approve before expiry.",
        idempotency_key="decision-expire-approved",
    )
    repository.expire_due(now=(datetime.now(UTC) + timedelta(minutes=4)).isoformat())
    persisted = repository.get_action_proposal(proposal["proposal_id"])
    assert persisted["state"] == "expired"
    assert persisted["destination_arguments"] is None
    assert repository.job_repository.get_job(decided["execution_job"]["job_id"])[
        "status"
    ] == "cancelled"
    assert repository.get(proposal["review_id"])["state"] == "approved"
    repository.close()


def test_independent_batch_manifest_is_server_derived_and_exact(tmp_path) -> None:
    repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = HumanReviewService(repository)
    descriptor = _action_descriptor(cardinality="independent_batch")
    envelope = _action_envelope(descriptor, request_id="request-batch")
    created = service.create_action_proposal(
        envelope=envelope,
        descriptor=descriptor,
        resource_version="resource-v1",
        approver_principal="discord_user:approver",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )
    manifest = created["proposal"]["batch_manifest"]
    assert manifest["expected_child_count"] == 2
    assert [item["child_index"] for item in manifest["children"]] == [1, 2]
    assert len({item["child_operation_id"] for item in manifest["children"]}) == 2
    assert created["proposal"]["batch_manifest_hash"] == hashlib.sha256(
        canonical_json(manifest).encode("utf-8")
    ).hexdigest()

    changed = {**manifest, "children": [dict(item) for item in manifest["children"]]}
    changed["children"][0]["target_hash"] = "b" * 64
    with pytest.raises(ValueError, match="action_batch_manifest_changed"):
        service.create_action_proposal(
            envelope=envelope,
            descriptor=descriptor,
            resource_version="resource-v1",
            approver_principal="discord_user:approver",
            expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            batch_manifest=changed,
        )
    repository.close()


def test_transfer_manifest_is_request_channel_value_and_policy_bound(tmp_path) -> None:
    repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = HumanReviewService(repository)
    destination = _action_descriptor(persistence="no_store")
    envelope = _action_envelope(destination, request_id="request-transfer")
    source = ToolDescriptor.from_mapping(
        {
            "tool_id": "synthetic.read",
            "skill_id": "skill.synthetic",
            "contract_version": 1,
            "purpose": "Read one synthetic reference.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
                "minProperties": 0,
                "maxProperties": 0,
            },
            "observation_schema": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"ref": {"type": "string", "maxLength": 80}},
                            "required": ["ref"],
                            "additionalProperties": False,
                            "minProperties": 1,
                            "maxProperties": 1,
                        },
                        "maxItems": 10,
                    }
                },
                "required": ["items"],
                "additionalProperties": False,
                "minProperties": 1,
                "maxProperties": 1,
            },
            "effect": "read",
            "approval_rule": "none",
            "approval_conditions": [],
            "sensitivity": "private",
            "persistence": "no_store",
            "idempotency": "not_applicable",
            "effect_cardinality": "single",
            "transferable_observation_fields": [
                {"pattern": "/items/*/ref", "scope": "same_domain"}
            ],
            "runtime_dependencies": [],
            "timeout_seconds": 10,
            "max_result_items": 10,
            "max_observation_chars": 500,
            "legacy_intents": [],
            "interactive": True,
        }
    )
    resource_version = "resource-v1"
    descriptor_hash = hashlib.sha256(
        canonical_json(destination.to_storage_dict()).encode("utf-8")
    ).hexdigest()
    transfer = {
        "manifest_version": 1,
        "request_id": envelope.root_request_id,
        "request_hash": action_request_binding_hash(envelope),
        "requester_user_id": envelope.user_id,
        "agent_id": envelope.agent_id,
        "channel_binding_hash": action_channel_binding_hash(envelope),
        "destination": {
            "skill_id": envelope.skill_id,
            "domain": "synthetic",
            "tool_id": envelope.tool_id,
            "contract_version": envelope.contract_version,
            "descriptor_hash": descriptor_hash,
            "resource_version": resource_version,
            "arguments_hash": envelope.arguments_hash,
            "sensitivity": destination.sensitivity,
            "persistence": destination.persistence,
        },
        "destination_values": [
            {
                "destination_pointer": "/target",
                "value_hash": hashlib.sha256(
                    canonical_json("lamp").encode("utf-8")
                ).hexdigest(),
            }
        ],
        "sources": [
            {
                "observation_ref": "obs_v1_source",
                "operation_id": "toolop_v1_source",
                "skill_id": source.skill_id,
                "domain": "synthetic",
                "tool_id": source.tool_id,
                "contract_version": source.contract_version,
                "descriptor_hash": hashlib.sha256(
                    canonical_json(source.to_storage_dict()).encode("utf-8")
                ).hexdigest(),
                "resource_version": "source-resource-v1",
                "transfer_pattern": "/items/*/ref",
                "transfer_scope": "same_domain",
                "source_pointer": "/items/0/ref",
                "subtree_hash": hashlib.sha256(
                    canonical_json("lamp").encode("utf-8")
                ).hexdigest(),
                "sensitivity": source.sensitivity,
                "persistence": source.persistence,
                "untrusted": False,
            }
        ],
    }
    created = service.create_action_proposal(
        envelope=envelope,
        descriptor=destination,
        resource_version=resource_version,
        approver_principal="discord_user:approver",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        transfer_manifest=transfer,
    )
    assert created["proposal"]["transfer_manifest"] == transfer
    assert created["proposal"]["destination_arguments"] == {"target": "lamp"}

    with pytest.raises(ValueError, match="action_transfer_channel_changed"):
        service.create_action_proposal(
            envelope=envelope,
            descriptor=destination,
            resource_version=resource_version,
            approver_principal="discord_user:approver",
            expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            transfer_manifest={**transfer, "channel_binding_hash": "b" * 64},
        )
    repository.close()


def test_action_creation_rolls_back_proposal_and_review_if_notification_enqueue_fails(
    tmp_path,
    monkeypatch,
) -> None:
    repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = HumanReviewService(repository)
    descriptor = _action_descriptor()

    def fail_enqueue(**kwargs):
        del kwargs
        raise RuntimeError("injected-notification-enqueue-failure")

    monkeypatch.setattr(repository.job_repository, "enqueue_job", fail_enqueue)
    with pytest.raises(RuntimeError, match="injected-notification-enqueue-failure"):
        service.create_action_proposal(
            envelope=_action_envelope(descriptor, request_id="request-atomic-create"),
            descriptor=descriptor,
            resource_version="resource-v1",
            approver_principal="discord_user:approver",
            expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        )
    assert repository.list_action_proposals() == []
    assert repository.list_items() == []
    repository.close()


def test_action_approval_rolls_back_decision_if_execution_enqueue_fails(
    tmp_path,
    monkeypatch,
) -> None:
    repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = HumanReviewService(repository)
    descriptor = _action_descriptor()
    created = service.create_action_proposal(
        envelope=_action_envelope(descriptor, request_id="request-atomic-decision"),
        descriptor=descriptor,
        resource_version="resource-v1",
        approver_principal="discord_user:approver",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )
    proposal = created["proposal"]
    repository.mark_action_notification_delivered(
        proposal_id=proposal["proposal_id"],
        destination_purpose="human_reviews",
        guild_id="guild-private",
        channel_id="channel-review",
        message_id="card-atomic-decision",
    )
    original_enqueue = repository.job_repository.enqueue_job

    def fail_execution_enqueue(**kwargs):
        if kwargs.get("job_type") == "review.action_execution.v1":
            raise RuntimeError("injected-execution-enqueue-failure")
        return original_enqueue(**kwargs)

    monkeypatch.setattr(repository.job_repository, "enqueue_job", fail_execution_enqueue)
    with pytest.raises(RuntimeError, match="injected-execution-enqueue-failure"):
        service.decide_action_proposal(
            proposal_id=proposal["proposal_id"],
            review_id=proposal["review_id"],
            bound_proposal_hash=proposal["proposal_hash"],
            decision="approve",
            actor_principal="discord_user:approver",
            destination_purpose="human_reviews",
            guild_id="guild-private",
            channel_id="channel-review",
            message_id="decision-atomic",
            reason="Approve atomically.",
            idempotency_key="decision-atomic",
        )
    assert repository.get_action_proposal(proposal["proposal_id"])["state"] == "pending"
    assert repository.get(proposal["review_id"])["state"] == "pending"
    assert repository.latest_decision(review_id=proposal["review_id"]) is None
    assert repository.job_repository.list_jobs(
        job_type="review.action_execution.v1"
    ) == []
    repository.close()
