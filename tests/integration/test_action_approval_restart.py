from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from app.core.approved_action_execution import ApprovedActionExecutionService
from app.jobs.types import REVIEW_ACTION_EXECUTION_JOB, REVIEW_OUTCOME_DISCORD_JOB
from app.reviews.repository import HumanReviewRepository
from app.reviews.service import (
    HumanReviewService,
    action_channel_binding_hash,
    action_request_binding_hash,
)
from app.skills.authorized_executor import AuthorizedToolReference, PreparedToolCall
from app.skills.tool_contracts import ToolCallEnvelope, ToolDescriptor, canonical_json


def _descriptor() -> ToolDescriptor:
    return ToolDescriptor.from_mapping(
        {
            "tool_id": "synthetic.write",
            "skill_id": "skill.synthetic",
            "contract_version": 1,
            "purpose": "Record one synthetic test-only effect.",
            "input_schema": {
                "type": "object",
                "properties": {"target": {"type": "string", "minLength": 1, "maxLength": 40}},
                "required": ["target"],
                "additionalProperties": False,
                "minProperties": 1,
                "maxProperties": 1,
            },
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
            "persistence": "redacted",
            "idempotency": "required",
            "effect_cardinality": "single",
            "transferable_observation_fields": [],
            "runtime_dependencies": ["action_approval"],
            "timeout_seconds": 10,
            "max_result_items": 1,
            "max_observation_chars": 500,
            "legacy_intents": [],
            "interactive": True,
        }
    )


def _batch_descriptor() -> ToolDescriptor:
    value = _descriptor().to_storage_dict()
    value["input_schema"] = {
        "type": "object",
        "properties": {
            "targets": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 40},
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
    value["effect_cardinality"] = "independent_batch"
    return ToolDescriptor.from_mapping(value)


def _source_descriptor() -> ToolDescriptor:
    value = _descriptor().to_storage_dict()
    value.update(
        {
            "tool_id": "synthetic.read",
            "purpose": "Read one synthetic test reference.",
            "observation_schema": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"ref": {"type": "string", "maxLength": 40}},
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
            "sensitivity": "private",
            "persistence": "no_store",
            "idempotency": "not_applicable",
            "runtime_dependencies": [],
            "transferable_observation_fields": [
                {"pattern": "/items/*/ref", "scope": "same_domain"}
            ],
        }
    )
    return ToolDescriptor.from_mapping(value)


def _envelope(descriptor: ToolDescriptor) -> ToolCallEnvelope:
    return ToolCallEnvelope.create(
        root_request_id="restart-request",
        call_ordinal=1,
        session_id="restart-session",
        principal_kind="discord_adapter",
        principal_subject="discord_user:requester",
        external_user_id="requester",
        user_id="user-1",
        agent_id="catparty",
        source_interface="discord",
        channel_scope="interactive-channel",
        skill_id=descriptor.skill_id,
        descriptor=descriptor,
        authorization_snapshot_ref="authz_v1_" + "a" * 64,
        validated_arguments=(
            {"targets": ["alpha", "beta"]}
            if descriptor.effect_cardinality == "independent_batch"
            else {"target": "canary"}
        ),
    )


class StableExecutor:
    def __init__(
        self,
        prepared: PreparedToolCall,
        *,
        source_reference: AuthorizedToolReference | None = None,
    ) -> None:
        self.prepared = prepared
        self.source_reference = source_reference
        self.effect_count = 0

    def prepare_tool_call(self, **kwargs):
        if kwargs["tool_id"] != self.prepared.envelope.tool_id:
            return {"status": "policy_denied", "denial_reason": "tool_removed"}
        return self.prepared

    def execute_prepared_tool(self, prepared):
        assert prepared is self.prepared
        self.effect_count += 1
        return {
            "status": "ok",
            "committed_effect": True,
            "payload": {"changed": True},
            "receipt_id": "synthetic-receipt-1",
        }

    def authorize_tool_reference(self, **kwargs):
        reference = self.source_reference
        if (
            reference is None
            or kwargs["tool_id"] != reference.descriptor.tool_id
            or kwargs["contract_version"] != reference.descriptor.contract_version
        ):
            return {"status": "policy_denied", "denial_reason": "source_removed"}
        return reference


class StableIdentity:
    def resolve(self, **kwargs):
        assert kwargs == {"source": "discord", "external_user_id": "requester"}
        return {
            "active": True,
            "user_id": "user-1",
            "agent_id": "catparty",
            "age_band": None,
            "presentation_profile": "default",
            "policy_profile": "adult",
        }


def test_restart_after_effect_commit_before_job_completion_never_reexecutes(tmp_path) -> None:
    path = tmp_path / "core.db"
    descriptor = _descriptor()
    envelope = _envelope(descriptor)
    prepared = PreparedToolCall(
        envelope=envelope,
        descriptor=descriptor,
        descriptor_hash=hashlib.sha256(
            canonical_json(descriptor.to_storage_dict()).encode("utf-8")
        ).hexdigest(),
        resource_version="resource-v1",
    )
    executor = StableExecutor(prepared)
    first_repository = HumanReviewRepository(str(path))
    service = HumanReviewService(first_repository)
    created = service.create_action_proposal(
        envelope=envelope,
        descriptor=descriptor,
        resource_version="resource-v1",
        approver_principal="discord_user:approver",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )
    proposal = created["proposal"]
    first_repository.mark_action_notification_delivered(
        proposal_id=proposal["proposal_id"],
        destination_purpose="human_reviews",
        guild_id="review-guild",
        channel_id="review-channel",
        message_id="approval-card",
    )
    decision = service.decide_action_proposal(
        proposal_id=proposal["proposal_id"],
        review_id=proposal["review_id"],
        bound_proposal_hash=proposal["proposal_hash"],
        decision="approve",
        actor_principal="discord_user:approver",
        destination_purpose="human_reviews",
        guild_id="review-guild",
        channel_id="review-channel",
        message_id="approval-decision",
        reason="Approve the synthetic no-provider effect.",
        idempotency_key="restart-decision-1",
    )
    job_id = decision["execution_job"]["job_id"]
    first_claim_time = datetime.now(UTC) + timedelta(seconds=1)
    claimed = first_repository.job_repository.claim_jobs(
        job_type=REVIEW_ACTION_EXECUTION_JOB,
        worker_id="worker-before-restart",
        limit=1,
        lease_seconds=1,
        now=first_claim_time,
    )[0]
    action_execution = ApprovedActionExecutionService(
        reviews=first_repository,
        authorized_executor=executor,
        identity_service=StableIdentity(),
    )
    assert action_execution.execute(claimed) == {
        "status": "executed",
        "receipt_ref": "synthetic-receipt-1",
    }
    assert executor.effect_count == 1
    assert first_repository.job_repository.get_job(job_id)["status"] == "running"
    outcome_jobs = first_repository.job_repository.list_jobs(
        job_type=REVIEW_OUTCOME_DISCORD_JOB
    )
    assert len(outcome_jobs) == 1
    assert outcome_jobs[0]["status"] == "pending"
    assert outcome_jobs[0]["payload"]["state"] == "executed"
    first_repository.close()

    restarted = HumanReviewRepository(str(path))
    reclaimed = restarted.job_repository.claim_jobs(
        job_type=REVIEW_ACTION_EXECUTION_JOB,
        worker_id="worker-after-restart",
        limit=1,
        lease_seconds=30,
        now=first_claim_time + timedelta(seconds=2),
    )[0]
    restarted_execution = ApprovedActionExecutionService(
        reviews=restarted,
        authorized_executor=executor,
        identity_service=StableIdentity(),
    )
    assert restarted_execution.execute(reclaimed) == {
        "status": "already_executed",
        "receipt_ref": "synthetic-receipt-1",
    }
    assert executor.effect_count == 1
    assert restarted.job_repository.complete_job(
        job_id=job_id,
        worker_id="worker-after-restart",
        fencing_token=int(reclaimed["lease_fencing_token"]),
    )
    assert restarted.job_repository.get_job(job_id)["status"] == "completed"
    assert restarted.get_action_proposal(proposal["proposal_id"])["destination_arguments"] is None
    assert len(
        restarted.job_repository.list_jobs(job_type=REVIEW_OUTCOME_DISCORD_JOB)
    ) == 1
    restarted.close()


def test_restart_matrix_preserves_jobs_and_recovers_expired_leases(tmp_path) -> None:
    path = tmp_path / "core.db"
    descriptor = _descriptor()
    envelope = _envelope(descriptor)
    prepared = PreparedToolCall(
        envelope=envelope,
        descriptor=descriptor,
        descriptor_hash=hashlib.sha256(
            canonical_json(descriptor.to_storage_dict()).encode("utf-8")
        ).hexdigest(),
        resource_version="resource-v1",
    )
    executor = StableExecutor(prepared)
    repository = HumanReviewRepository(str(path))
    service = HumanReviewService(repository)
    created = service.create_action_proposal(
        envelope=envelope,
        descriptor=descriptor,
        resource_version="resource-v1",
        approver_principal="discord_user:approver",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )
    proposal = created["proposal"]
    notification_id = created["notification_job"]["job_id"]
    repository.close()

    after_create = HumanReviewRepository(str(path))
    assert len(after_create.job_repository.list_jobs()) == 1
    first_time = datetime.now(UTC) + timedelta(seconds=1)
    first_notification = after_create.job_repository.claim_jobs(
        job_type="review.notification.discord.v1",
        worker_id="notification-before-restart",
        limit=1,
        lease_seconds=1,
        now=first_time,
    )[0]
    assert first_notification["job_id"] == notification_id
    after_create.close()

    after_notification_lease = HumanReviewRepository(str(path))
    recovered_notification = after_notification_lease.job_repository.claim_jobs(
        job_type="review.notification.discord.v1",
        worker_id="notification-after-restart",
        limit=1,
        lease_seconds=30,
        now=first_time + timedelta(seconds=2),
    )[0]
    assert recovered_notification["job_id"] == notification_id
    assert recovered_notification["lease_fencing_token"] > first_notification["lease_fencing_token"]
    after_notification_lease.mark_action_notification_delivered(
        proposal_id=proposal["proposal_id"],
        destination_purpose="human_reviews",
        guild_id="review-guild",
        channel_id="review-channel",
        message_id="approval-card",
    )
    assert after_notification_lease.job_repository.complete_job(
        job_id=notification_id,
        worker_id="notification-after-restart",
        fencing_token=int(recovered_notification["lease_fencing_token"]),
    )
    decision = HumanReviewService(after_notification_lease).decide_action_proposal(
        proposal_id=proposal["proposal_id"],
        review_id=proposal["review_id"],
        bound_proposal_hash=proposal["proposal_hash"],
        decision="approve",
        actor_principal="discord_user:approver",
        destination_purpose="human_reviews",
        guild_id="review-guild",
        channel_id="review-channel",
        message_id="approval-decision",
        reason="Approve after notification restart.",
        idempotency_key="restart-matrix-decision",
    )
    execution_id = decision["execution_job"]["job_id"]
    after_notification_lease.close()

    after_decision = HumanReviewRepository(str(path))
    execution_jobs = after_decision.job_repository.list_jobs(
        job_type=REVIEW_ACTION_EXECUTION_JOB
    )
    assert len(execution_jobs) == 1
    first_execution = after_decision.job_repository.claim_jobs(
        job_type=REVIEW_ACTION_EXECUTION_JOB,
        worker_id="execution-before-restart",
        limit=1,
        lease_seconds=1,
        now=first_time + timedelta(seconds=3),
    )[0]
    after_decision.close()

    after_execution_lease = HumanReviewRepository(str(path))
    recovered_execution = after_execution_lease.job_repository.claim_jobs(
        job_type=REVIEW_ACTION_EXECUTION_JOB,
        worker_id="execution-after-restart",
        limit=1,
        lease_seconds=30,
        now=first_time + timedelta(seconds=5),
    )[0]
    assert recovered_execution["job_id"] == execution_id
    assert recovered_execution["lease_fencing_token"] > first_execution["lease_fencing_token"]
    outcome = ApprovedActionExecutionService(
        reviews=after_execution_lease,
        authorized_executor=executor,
        identity_service=StableIdentity(),
    ).execute(recovered_execution)
    assert outcome == {"status": "executed", "receipt_ref": "synthetic-receipt-1"}
    assert executor.effect_count == 1
    after_execution_lease.close()


def test_transfer_sources_are_reauthorized_after_restart_and_terminal_payload_clears(
    tmp_path,
) -> None:
    path = tmp_path / "core.db"
    descriptor_value = _descriptor().to_storage_dict()
    descriptor_value["persistence"] = "no_store"
    destination = ToolDescriptor.from_mapping(descriptor_value)
    source_value = _source_descriptor().to_storage_dict()
    source_value["tool_id"] = "source.read"
    source_value["skill_id"] = "skill.source.core"
    source_value["transferable_observation_fields"] = [
        {"pattern": "/items/*/ref", "scope": "cross_domain"}
    ]
    source = ToolDescriptor.from_mapping(source_value)
    envelope = _envelope(destination)
    destination_hash = hashlib.sha256(
        canonical_json(destination.to_storage_dict()).encode("utf-8")
    ).hexdigest()
    source_hash = hashlib.sha256(
        canonical_json(source.to_storage_dict()).encode("utf-8")
    ).hexdigest()
    prepared = PreparedToolCall(
        envelope=envelope,
        descriptor=destination,
        descriptor_hash=destination_hash,
        resource_version="resource-v1",
    )
    executor = StableExecutor(
        prepared,
        source_reference=AuthorizedToolReference(
            descriptor=source,
            descriptor_hash=source_hash,
            resource_version="source-resource-v1",
        ),
    )
    transfer_manifest = {
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
            "descriptor_hash": destination_hash,
            "resource_version": "resource-v1",
            "arguments_hash": envelope.arguments_hash,
            "sensitivity": destination.sensitivity,
            "persistence": destination.persistence,
        },
        "destination_values": [
            {
                "destination_pointer": "/target",
                "value_hash": hashlib.sha256(
                    canonical_json("canary").encode("utf-8")
                ).hexdigest(),
            }
        ],
        "sources": [
            {
                "observation_ref": "obs_v1_source",
                "operation_id": "toolop_v1_source",
                "skill_id": source.skill_id,
                "domain": "source",
                "tool_id": source.tool_id,
                "contract_version": source.contract_version,
                "descriptor_hash": source_hash,
                "resource_version": "source-resource-v1",
                "transfer_pattern": "/items/*/ref",
                "transfer_scope": "cross_domain",
                "source_pointer": "/items/0/ref",
                "subtree_hash": hashlib.sha256(
                    canonical_json("canary").encode("utf-8")
                ).hexdigest(),
                "sensitivity": source.sensitivity,
                "persistence": source.persistence,
                "untrusted": False,
            }
        ],
    }
    repository = HumanReviewRepository(str(path))
    created = HumanReviewService(repository).create_action_proposal(
        envelope=envelope,
        descriptor=destination,
        resource_version="resource-v1",
        approver_principal="discord_user:approver",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        transfer_manifest=transfer_manifest,
    )
    proposal = created["proposal"]
    repository.mark_action_notification_delivered(
        proposal_id=proposal["proposal_id"],
        destination_purpose="human_reviews",
        guild_id="review-guild",
        channel_id="review-channel",
        message_id="approval-card-transfer",
    )
    decision = HumanReviewService(repository).decide_action_proposal(
        proposal_id=proposal["proposal_id"],
        review_id=proposal["review_id"],
        bound_proposal_hash=proposal["proposal_hash"],
        decision="approve",
        actor_principal="discord_user:approver",
        destination_purpose="human_reviews",
        guild_id="review-guild",
        channel_id="review-channel",
        message_id="approval-decision-transfer",
        reason="Approve exact synthetic transfer.",
        idempotency_key="restart-transfer-decision",
    )
    repository.close()

    restarted = HumanReviewRepository(str(path))
    claimed = restarted.job_repository.claim_jobs(
        job_type=REVIEW_ACTION_EXECUTION_JOB,
        worker_id="transfer-worker",
        limit=1,
        lease_seconds=30,
    )[0]
    assert claimed["job_id"] == decision["execution_job"]["job_id"]
    outcome = ApprovedActionExecutionService(
        reviews=restarted,
        authorized_executor=executor,
        identity_service=StableIdentity(),
    ).execute(claimed)
    assert outcome == {"status": "executed", "receipt_ref": "synthetic-receipt-1"}
    persisted = restarted.get_action_proposal(proposal["proposal_id"])
    assert persisted["state"] == "executed"
    assert persisted["destination_arguments"] is None
    assert persisted["transfer_manifest"] == transfer_manifest
    assert executor.effect_count == 1
    restarted.close()


class BatchExecutor(StableExecutor):
    def __init__(self, prepared: PreparedToolCall) -> None:
        super().__init__(prepared)
        self.child_effects: dict[str, int] = {}

    def execute_prepared_tool(self, prepared):
        assert prepared is self.prepared
        self.effect_count += 1
        for target in prepared.envelope.arguments["targets"]:
            self.child_effects[str(target)] = self.child_effects.get(str(target), 0) + 1
        return {
            "status": "queued",
            "committed_effect": False,
            "receipt_ids": [f"child-receipt:{target}" for target in self.child_effects],
        }


def test_independent_batch_revalidates_children_and_restart_does_not_repeat_them(
    tmp_path,
) -> None:
    path = tmp_path / "core.db"
    descriptor = _batch_descriptor()
    envelope = _envelope(descriptor)
    prepared = PreparedToolCall(
        envelope=envelope,
        descriptor=descriptor,
        descriptor_hash=hashlib.sha256(
            canonical_json(descriptor.to_storage_dict()).encode("utf-8")
        ).hexdigest(),
        resource_version="resource-v1",
    )
    executor = BatchExecutor(prepared)
    repository = HumanReviewRepository(str(path))
    service = HumanReviewService(repository)
    created = service.create_action_proposal(
        envelope=envelope,
        descriptor=descriptor,
        resource_version="resource-v1",
        approver_principal="discord_user:approver",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )
    proposal = created["proposal"]
    assert [row["child_index"] for row in proposal["batch_manifest"]["children"]] == [1, 2]
    repository.mark_action_notification_delivered(
        proposal_id=proposal["proposal_id"],
        destination_purpose="human_reviews",
        guild_id="review-guild",
        channel_id="review-channel",
        message_id="approval-card-batch",
    )
    decision = service.decide_action_proposal(
        proposal_id=proposal["proposal_id"],
        review_id=proposal["review_id"],
        bound_proposal_hash=proposal["proposal_hash"],
        decision="approve",
        actor_principal="discord_user:approver",
        destination_purpose="human_reviews",
        guild_id="review-guild",
        channel_id="review-channel",
        message_id="approval-decision-batch",
        reason="Approve exact synthetic batch.",
        idempotency_key="restart-batch-decision",
    )
    claimed = repository.job_repository.claim_jobs(
        job_type=REVIEW_ACTION_EXECUTION_JOB,
        worker_id="batch-before-restart",
        limit=1,
        lease_seconds=1,
    )[0]
    assert ApprovedActionExecutionService(
        reviews=repository,
        authorized_executor=executor,
        identity_service=StableIdentity(),
    ).execute(claimed)["status"] == "executed"
    assert executor.child_effects == {"alpha": 1, "beta": 1}
    repository.close()

    restarted = HumanReviewRepository(str(path))
    reclaimed = restarted.job_repository.claim_jobs(
        job_type=REVIEW_ACTION_EXECUTION_JOB,
        worker_id="batch-after-restart",
        limit=1,
        lease_seconds=30,
        now=datetime.now(UTC) + timedelta(seconds=2),
    )[0]
    assert reclaimed["job_id"] == decision["execution_job"]["job_id"]
    assert ApprovedActionExecutionService(
        reviews=restarted,
        authorized_executor=executor,
        identity_service=StableIdentity(),
    ).execute(reclaimed)["status"] == "already_executed"
    assert executor.child_effects == {"alpha": 1, "beta": 1}
    restarted.close()
