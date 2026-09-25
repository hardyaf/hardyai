from __future__ import annotations

import hashlib
from typing import Any, Mapping
from uuid import UUID

from app.reviews.repository import HumanReviewRepository
from app.reviews.service import (
    action_channel_binding_hash,
    action_request_binding_hash,
    derive_action_batch_manifest,
)
from app.reviews.types import ActionProposalState
from app.skills.tool_contracts import ToolDescriptor, canonical_json


_CROSS_DOMAIN_SENSITIVITY_MATRIX = {
    "normal": frozenset(
        {"normal", "private", "financial", "identity", "highly_restricted"}
    ),
    "private": frozenset({"private", "highly_restricted"}),
    "financial": frozenset({"financial", "highly_restricted"}),
    "identity": frozenset({"identity", "highly_restricted"}),
    "highly_restricted": frozenset(),
}


class ApprovedActionExecutionService:
    """Reauthorize and dispatch one durably approved exact tool call."""

    def __init__(
        self,
        *,
        reviews: HumanReviewRepository,
        authorized_executor: Any,
        identity_service: Any,
        available_runtime_dependencies: tuple[str, ...] = (),
    ) -> None:
        self._reviews = reviews
        self._executor = authorized_executor
        self._identity_service = identity_service
        self._available_runtime_dependencies = tuple(
            dict.fromkeys(
                str(item or "").strip().casefold()
                for item in available_runtime_dependencies
                if str(item or "").strip()
            )
        )

    @staticmethod
    def _receipt_ref(result: Mapping[str, Any]) -> str | None:
        for key in ("receipt_id", "job_id"):
            value = str(result.get(key) or "").strip()
            if value:
                return value[:240]
        for key in ("receipt_ids", "job_ids"):
            values = result.get(key)
            if isinstance(values, (list, tuple)):
                for item in values:
                    value = str(item or "").strip()
                    if value:
                        return value[:240]
        return None

    @staticmethod
    def _local_task_workspace_binding(
        proposal: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Reauthorize the authenticated local operator without an external identity row."""

        channel_scope = str(proposal.get("channel_scope") or "")
        if not channel_scope.startswith("task:"):
            return None
        try:
            UUID(channel_scope.removeprefix("task:"))
        except (ValueError, AttributeError):
            return None
        if not (
            str(proposal.get("destination_purpose") or "") == "task_workspace"
            and str(proposal.get("source_interface") or "") == "task_workspace"
            and str(proposal.get("principal_kind") or "") == "operator"
            and str(proposal.get("principal_subject") or "") == "operator"
            and str(proposal.get("external_user_id") or "") == "operator"
            and str(proposal.get("requester_user_id") or "") == "operator"
            and str(proposal.get("approver_principal") or "") == "operator"
            and str(proposal.get("decided_by_principal") or "") == "operator"
            and str(proposal.get("agent_id") or "") == "jarvis"
        ):
            return None
        return {
            "active": True,
            "user_id": "operator",
            "agent_id": "jarvis",
            "age_band": None,
            "presentation_profile": "default",
            "policy_profile": "adult",
        }

    @staticmethod
    def _job_matches_proposal(payload: object, proposal: Mapping[str, Any]) -> bool:
        if not isinstance(payload, Mapping):
            return False
        return (
            set(payload)
            == {
                "proposal_id",
                "review_id",
                "operation_id",
                "authorization_binding",
                "batch_manifest_hash",
                "transfer_binding_hash",
            }
            and str(payload.get("proposal_id") or "") == str(proposal.get("proposal_id") or "")
            and str(payload.get("review_id") or "") == str(proposal.get("review_id") or "")
            and str(payload.get("operation_id") or "") == str(proposal.get("operation_id") or "")
            and str(payload.get("authorization_binding") or "")
            == str(proposal.get("authorization_binding") or "")
            and payload.get("batch_manifest_hash") == proposal.get("batch_manifest_hash")
            and payload.get("transfer_binding_hash") == proposal.get("transfer_binding_hash")
        )

    @staticmethod
    def _sha256(value: object) -> str:
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

    @staticmethod
    def _pointer_value(value: object, pointer: str) -> tuple[bool, object]:
        if pointer == "":
            return True, value
        if not pointer.startswith("/"):
            return False, None
        current = value
        for encoded in pointer[1:].split("/"):
            segment = encoded.replace("~1", "/").replace("~0", "~")
            if isinstance(current, Mapping) and segment in current:
                current = current[segment]
                continue
            if isinstance(current, (list, tuple)) and segment.isdigit():
                index = int(segment)
                if index < len(current):
                    current = current[index]
                    continue
            return False, None
        return True, current

    @staticmethod
    def _pointer_pattern_matches(pattern: str, pointer: str) -> bool:
        if not pattern.startswith("/") or not pointer.startswith("/"):
            return False
        pattern_segments = pattern[1:].split("/")
        pointer_segments = pointer[1:].split("/")
        return len(pattern_segments) <= len(pointer_segments) and all(
            expected == "*" or expected == observed
            for expected, observed in zip(pattern_segments, pointer_segments, strict=False)
        )

    def _batch_reauthorization_failure(
        self,
        *,
        proposal: Mapping[str, Any],
        envelope: Any,
        descriptor: ToolDescriptor,
    ) -> str | None:
        manifest = proposal.get("batch_manifest")
        manifest_hash = proposal.get("batch_manifest_hash")
        if descriptor.effect_cardinality != "independent_batch":
            return (
                "approval_batch_manifest_unexpected"
                if manifest is not None or manifest_hash is not None
                else None
            )
        if not isinstance(manifest, Mapping) or not isinstance(manifest_hash, str):
            return "approval_batch_manifest_missing"
        try:
            current, current_hash = derive_action_batch_manifest(
                envelope=envelope,
                descriptor=descriptor,
            )
        except (TypeError, ValueError):
            return "approval_batch_reauthorization_invalid"
        if dict(manifest) != current or manifest_hash != current_hash:
            return "approval_batch_reauthorization_changed"
        return None

    def _transfer_reauthorization_failure(
        self,
        *,
        proposal: Mapping[str, Any],
        envelope: Any,
        destination_descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        request_context: dict[str, Any],
    ) -> str | None:
        manifest = proposal.get("transfer_manifest")
        binding_hash = proposal.get("transfer_binding_hash")
        if manifest is None:
            return "approval_transfer_hash_unexpected" if binding_hash is not None else None
        if not isinstance(manifest, Mapping) or not isinstance(binding_hash, str):
            return "approval_transfer_manifest_invalid"
        if self._sha256(manifest) != binding_hash:
            return "approval_transfer_manifest_changed"
        if (
            manifest.get("request_id") != envelope.root_request_id
            or manifest.get("request_hash") != action_request_binding_hash(envelope)
            or manifest.get("requester_user_id") != envelope.user_id
            or manifest.get("agent_id") != envelope.agent_id
            or manifest.get("channel_binding_hash") != action_channel_binding_hash(envelope)
        ):
            return "approval_transfer_request_changed"
        destination = manifest.get("destination")
        expected_destination = {
            "skill_id": envelope.skill_id,
            "domain": envelope.tool_id.partition(".")[0],
            "tool_id": envelope.tool_id,
            "contract_version": envelope.contract_version,
            "descriptor_hash": self._sha256(destination_descriptor.to_storage_dict()),
            "resource_version": str(proposal.get("resource_version") or ""),
            "arguments_hash": envelope.arguments_hash,
            "sensitivity": destination_descriptor.sensitivity,
            "persistence": destination_descriptor.persistence,
        }
        if not isinstance(destination, Mapping) or dict(destination) != expected_destination:
            return "approval_transfer_destination_changed"
        destination_values = manifest.get("destination_values")
        if not isinstance(destination_values, list) or not destination_values:
            return "approval_transfer_destination_values_invalid"
        for entry in destination_values:
            if not isinstance(entry, Mapping):
                return "approval_transfer_destination_values_invalid"
            found, value = self._pointer_value(
                arguments,
                str(entry.get("destination_pointer") or ""),
            )
            if not found or entry.get("value_hash") != self._sha256(value):
                return "approval_transfer_destination_value_changed"
        authorize_source = getattr(self._executor, "authorize_tool_reference", None)
        if not callable(authorize_source):
            return "approval_transfer_source_reauthorization_unavailable"
        sources = manifest.get("sources")
        if not isinstance(sources, list) or not sources:
            return "approval_transfer_sources_invalid"
        for source in sources:
            if not isinstance(source, Mapping):
                return "approval_transfer_sources_invalid"
            current = authorize_source(
                tool_id=str(source.get("tool_id") or ""),
                contract_version=int(source.get("contract_version") or 0),
                source_interface=str(proposal["source_interface"]),
                requested_by_user_id=str(proposal["requester_user_id"]),
                agent_id=str(proposal["agent_id"]),
                request_context=request_context,
            )
            if isinstance(current, Mapping):
                return "approval_transfer_source_unauthorized"
            source_descriptor = getattr(current, "descriptor", None)
            if not isinstance(source_descriptor, ToolDescriptor):
                return "approval_transfer_source_descriptor_invalid"
            if (
                source_descriptor.skill_id != source.get("skill_id")
                or source_descriptor.tool_id != source.get("tool_id")
                or source_descriptor.tool_id.partition(".")[0] != source.get("domain")
                or source_descriptor.contract_version != source.get("contract_version")
                or getattr(current, "descriptor_hash", None) != source.get("descriptor_hash")
                or getattr(current, "resource_version", None) != source.get("resource_version")
                or source_descriptor.sensitivity != source.get("sensitivity")
                or source_descriptor.persistence != source.get("persistence")
            ):
                return "approval_transfer_source_changed"
            source_pointer = str(source.get("source_pointer") or "")
            transfer_pattern = str(source.get("transfer_pattern") or "")
            transfer_scope = str(source.get("transfer_scope") or "")
            source_domain = str(source.get("domain") or "")
            destination_domain = destination_descriptor.tool_id.partition(".")[0]
            cross_domain = source_domain != destination_domain
            if cross_domain and (
                transfer_scope != "cross_domain"
                or destination_descriptor.sensitivity
                not in _CROSS_DOMAIN_SENSITIVITY_MATRIX.get(
                    source_descriptor.sensitivity,
                    frozenset(),
                )
            ):
                return "approval_transfer_policy_changed"
            if source_pointer and not self._pointer_pattern_matches(
                transfer_pattern,
                source_pointer,
            ):
                return "approval_transfer_source_pointer_changed"
            if not any(
                field.pattern == transfer_pattern
                and field.scope == transfer_scope
                and (
                    not source_pointer
                    or self._pointer_pattern_matches(field.pattern, source_pointer)
                )
                for field in source_descriptor.transferable_observation_fields
            ):
                return "approval_transfer_source_scope_changed"
        return None

    def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload")
        proposal_id = str(payload.get("proposal_id") or "").strip() if isinstance(payload, Mapping) else ""
        proposal = self._reviews.get_action_proposal(proposal_id)
        if proposal is None or not self._job_matches_proposal(payload, proposal):
            return {"status": "denied", "reason_code": "approval_execution_binding_mismatch"}
        state = str(proposal.get("state") or "")
        if state == ActionProposalState.EXECUTED.value:
            return {
                "status": "already_executed",
                "receipt_ref": str(proposal.get("action_receipt_ref") or ""),
            }
        if state == ActionProposalState.EXECUTING.value:
            return {"status": "reconciliation_required", "reason_code": "effect_state_uncertain"}
        if state != ActionProposalState.APPROVED.value:
            return {"status": "denied", "reason_code": f"approval_state_{state or 'missing'}"}

        job_id = str(job.get("job_id") or "").strip()
        worker_id = str(job.get("lease_owner") or "").strip()
        fencing_token = int(job.get("lease_fencing_token") or 0)
        if not job_id or not worker_id or fencing_token < 1:
            return {"status": "denied", "reason_code": "approval_execution_lease_missing"}
        try:
            proposal = self._reviews.begin_action_execution(
                proposal_id=proposal_id,
                job_id=job_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
            )
        except (KeyError, ValueError):
            return {"status": "denied", "reason_code": "approval_execution_claim_denied"}

        arguments = proposal.get("destination_arguments")
        if not isinstance(arguments, dict):
            return self._finish_denied(
                proposal=proposal,
                job_id=job_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                reason_code="approval_arguments_unavailable",
            )
        binding = self._local_task_workspace_binding(proposal)
        if binding is None:
            resolve_identity = getattr(self._identity_service, "resolve", None)
            binding = (
                resolve_identity(
                    source=str(proposal["source_interface"]),
                    external_user_id=str(proposal["external_user_id"]),
                )
                if callable(resolve_identity)
                else None
            )
        if (
            not isinstance(binding, Mapping)
            or binding.get("active") is not True
            or str(binding.get("user_id") or "") != str(proposal["requester_user_id"])
            or str(binding.get("agent_id") or "") != str(proposal["agent_id"])
        ):
            return self._finish_denied(
                proposal=proposal,
                job_id=job_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                reason_code="approval_identity_revoked",
            )
        prepare = getattr(self._executor, "prepare_tool_call", None)
        if not callable(prepare):
            return self._finish_denied(
                proposal=proposal,
                job_id=job_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                reason_code="approval_executor_unavailable",
            )
        request_context = {
            "session_id": str(proposal["session_id"]),
            "principal_kind": str(proposal["principal_kind"]),
            "principal_subject": str(proposal["principal_subject"]),
            "external_user_id": str(proposal["external_user_id"]),
            "identity_bound": True,
            "age_band": binding.get("age_band"),
            "presentation_profile": binding.get("presentation_profile") or "default",
            "policy_profile": binding.get("policy_profile") or "adult",
            "is_child": bool(binding.get("age_band"))
            or str(binding.get("policy_profile") or "").startswith("child_"),
            "discord_channel_id": str(proposal["channel_scope"]),
            "session_channel": str(proposal["channel_scope"]),
            "available_runtime_dependencies": list(
                self._available_runtime_dependencies
            ),
        }
        prepared = prepare(
            tool_id=str(proposal["tool_id"]),
            contract_version=int(proposal["contract_version"]),
            arguments=arguments,
            source_interface=str(proposal["source_interface"]),
            requested_by_user_id=str(proposal["requester_user_id"]),
            agent_id=str(proposal["agent_id"]),
            request_context=request_context,
            request_id=str(proposal["root_request_id"]),
            call_ordinal=int(proposal["call_ordinal"]),
        )
        if isinstance(prepared, Mapping):
            return self._finish_denied(
                proposal=proposal,
                job_id=job_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                reason_code=str(prepared.get("denial_reason") or "approval_reauthorization_denied"),
            )
        envelope = getattr(prepared, "envelope", None)
        descriptor = getattr(prepared, "descriptor", None)
        current_descriptor_hash = hashlib.sha256(
            canonical_json(descriptor.to_storage_dict()).encode("utf-8")
        ).hexdigest() if descriptor is not None else ""
        if (
            envelope is None
            or envelope.operation_id != proposal["operation_id"]
            or envelope.arguments_hash != proposal["arguments_hash"]
            or envelope.authorization_snapshot_ref != proposal["authorization_binding"]
            or current_descriptor_hash != proposal["descriptor_hash"]
            or str(getattr(prepared, "descriptor_hash", ""))
            != proposal["descriptor_hash"]
            or str(getattr(prepared, "resource_version", "")) != proposal["resource_version"]
        ):
            return self._finish_denied(
                proposal=proposal,
                job_id=job_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                reason_code="approval_reauthorization_changed",
            )
        batch_failure = self._batch_reauthorization_failure(
            proposal=proposal,
            envelope=envelope,
            descriptor=descriptor,
        )
        if batch_failure is not None:
            return self._finish_denied(
                proposal=proposal,
                job_id=job_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                reason_code=batch_failure,
            )
        transfer_failure = self._transfer_reauthorization_failure(
            proposal=proposal,
            envelope=envelope,
            destination_descriptor=descriptor,
            arguments=arguments,
            request_context=request_context,
        )
        if transfer_failure is not None:
            return self._finish_denied(
                proposal=proposal,
                job_id=job_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                reason_code=transfer_failure,
            )
        dispatch = getattr(self._executor, "execute_prepared_tool", None)
        if not callable(dispatch):
            return self._finish_denied(
                proposal=proposal,
                job_id=job_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                reason_code="approval_executor_unavailable",
            )
        try:
            result = dispatch(prepared)
        except Exception:
            self._reviews.finish_action_execution(
                proposal_id=proposal_id,
                job_id=job_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                outcome=ActionProposalState.FAILED_TERMINAL,
                reason_code="approval_dispatch_state_uncertain",
            )
            return {"status": "failed_terminal", "reason_code": "approval_dispatch_state_uncertain"}
        if not isinstance(result, Mapping):
            result = {}
        status = str(result.get("status") or "").strip().casefold()
        if status in {"policy_denied", "denied"}:
            return self._finish_denied(
                proposal=proposal,
                job_id=job_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                reason_code=str(result.get("denial_reason") or "approval_dispatch_denied"),
            )
        if status == "retryable_error" and not bool(result.get("committed_effect")):
            self._reviews.retry_action_execution_after_no_effect(
                proposal_id=proposal_id,
                job_id=job_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                reconciliation="no_effect",
            )
            return {"status": "retryable", "reason_code": "approval_dispatch_retryable_no_effect"}
        receipt = self._receipt_ref(result)
        if status not in {"ok", "queued"} or receipt is None:
            self._reviews.finish_action_execution(
                proposal_id=proposal_id,
                job_id=job_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                outcome=ActionProposalState.FAILED_TERMINAL,
                reason_code="approval_dispatch_receipt_missing",
            )
            return {"status": "failed_terminal", "reason_code": "approval_dispatch_receipt_missing"}
        completed = self._reviews.finish_action_execution(
            proposal_id=proposal_id,
            job_id=job_id,
            worker_id=worker_id,
            fencing_token=fencing_token,
            outcome=ActionProposalState.EXECUTED,
            reason_code="approval_execution_committed",
            receipt_ref=receipt,
        )
        return {"status": "executed", "receipt_ref": str(completed["action_receipt_ref"])}

    def _finish_denied(
        self,
        *,
        proposal: Mapping[str, Any],
        job_id: str,
        worker_id: str,
        fencing_token: int,
        reason_code: str,
    ) -> dict[str, str]:
        self._reviews.finish_action_execution(
            proposal_id=str(proposal["proposal_id"]),
            job_id=job_id,
            worker_id=worker_id,
            fencing_token=fencing_token,
            outcome=ActionProposalState.DENIED,
            reason_code=str(reason_code or "approval_execution_denied")[:120],
        )
        return {"status": "denied", "reason_code": str(reason_code)[:120]}
