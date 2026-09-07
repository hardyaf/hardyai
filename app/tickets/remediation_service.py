from __future__ import annotations

from typing import Any, Mapping, Protocol

from app.tickets.async_receipts import (
    AsyncChildOutcomeSink,
    TicketEffectManifestReservation,
    build_execution_manifest,
)
from app.tickets.repository import TicketRepository, content_hash
from app.tickets.types import TicketEntryType, TicketKind, TicketStatus, iso_utc


class RemediationExecutionGateway(Protocol):
    """The same bounded authorization/approval/idempotency path used by normal tools."""

    def execute_remediation(
        self,
        *,
        operation_id: str,
        capability: str,
        entities: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class RemediationService:
    def __init__(
        self,
        *,
        repository: TicketRepository,
        lists_service: Any,
        review_delay_seconds: float,
        review_max_attempts: int,
        plane_enabled: bool = False,
        execution_gateway: RemediationExecutionGateway | None = None,
    ) -> None:
        self._repository = repository
        # Retained only as a composition compatibility parameter. Remediation
        # can no longer invoke a domain service directly.
        self._lists_service = lists_service
        self._review_delay_seconds = max(0.0, float(review_delay_seconds))
        self._review_max_attempts = max(1, int(review_max_attempts))
        self._plane_enabled = bool(plane_enabled)
        self._execution_gateway = execution_gateway
        self._manifest_reservation = TicketEffectManifestReservation(repository)
        self._outcome_sink = AsyncChildOutcomeSink(repository=repository)

    def _enqueue_plane(self, ticket_id: str) -> None:
        if not self._plane_enabled:
            return
        ticket = self._repository.get_ticket(ticket_id)
        if ticket is None:
            return
        self._repository.enqueue_job(
            job_type="plane_sync",
            aggregate_id=ticket_id,
            idempotency_key=f"plane-sync:{ticket_id}:{ticket.get('version')}:{ticket.get('status')}",
            payload={"ticket_id": ticket_id},
        )

    @staticmethod
    def _operation_ids(origin_request_id: str) -> tuple[str, str]:
        binding = content_hash({"remediation_request_id": origin_request_id})
        return f"remediation:{binding}", f"remediation-effect:{binding}"

    def _fail_closed(self, child_id: str, reason: str) -> dict[str, Any]:
        self._repository.transition_ticket(
            ticket_id=child_id,
            status=TicketStatus.RECONCILIATION_REQUIRED,
            completed_at=iso_utc(),
            terminal_reason=reason,
        )
        self._enqueue_plane(child_id)
        return self._repository.get_ticket(child_id) or {}

    def execute(
        self,
        *,
        parent_ticket: dict[str, Any],
        capability: str,
        entities: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        parent_id = str(parent_ticket["ticket_id"])
        origin_request_id = (
            f"remediation:{parent_id}:{parent_ticket.get('source_action_revision')}:"
            f"{capability}:{content_hash(entities)}"
        )
        existing = self._repository.get_ticket_by_request_id(origin_request_id)
        if existing is not None and self._repository.list_receipts(str(existing["ticket_id"])):
            return existing

        generation = int(parent_ticket.get("remediation_generation") or 0) + 1
        child = existing or self._repository.create_ticket(
            origin_request_id=origin_request_id,
            session_id=str(parent_ticket.get("session_id") or f"remediation:{parent_id}"),
            user_id=str(parent_ticket.get("user_id") or "system"),
            agent_id=str(parent_ticket.get("agent_id") or "jarvis"),
            source="ticket_review_worker",
            intent=capability,
            skill_id=str(parent_ticket.get("skill_id") or "") or None,
            route="autonomous_remediation",
            title=f"Repair: {str(parent_ticket.get('title') or capability)}",
            ticket_kind=TicketKind.REMEDIATION,
            parent_ticket_id=parent_id,
            root_ticket_id=str(parent_ticket.get("root_ticket_id") or parent_id),
            remediation_generation=generation,
        )
        child_id = str(child["ticket_id"])
        parent_operation_id, child_operation_id = self._operation_ids(origin_request_id)
        self._repository.append_entry(
            ticket_id=child_id,
            request_id=origin_request_id,
            entry_type=TicketEntryType.USER_REQUEST.value,
            actor_type="system",
            actor_id="ticket_review_worker",
            verbatim_text=reason,
            structured_payload={
                "capability": capability,
                "entities": entities,
                "parent_ticket_id": parent_id,
                "operation_id": child_operation_id,
            },
            dedupe_key=f"ticket:{child_id}:remediation-request",
        )
        self._repository.transition_ticket(
            ticket_id=child_id,
            status=TicketStatus.EXECUTING,
        )
        self._repository.transition_ticket(
            ticket_id=parent_id,
            status=TicketStatus.REMEDIATION_QUEUED,
            terminal_reason=f"child_remediation:{child_id}",
        )
        self._repository.append_entry(
            ticket_id=parent_id,
            request_id=origin_request_id,
            entry_type=TicketEntryType.REMEDIATION_CREATED.value,
            actor_type="system",
            actor_id="ticket_review_worker",
            structured_payload={
                "child_ticket_id": child_id,
                "child_operation_id": child_operation_id,
                "capability": capability,
            },
            dedupe_key=f"ticket:{parent_id}:child:{child_id}",
        )

        manifest = build_execution_manifest(
            parent_operation_id=parent_operation_id,
            authorization_binding_hash=content_hash(
                {
                    "user_id": parent_ticket.get("user_id"),
                    "agent_id": parent_ticket.get("agent_id"),
                    "source": "ticket_review_worker",
                    "capability": capability,
                }
            ),
            effect_cardinality="single",
            skill_id=str(parent_ticket.get("skill_id") or "skill.lists.core"),
            tool_id=capability,
            contract_version=1,
            descriptor_hash=content_hash({"capability": capability, "contract_version": 1}),
            resource_version=1,
            parent_arguments_hash=content_hash(entities),
            children=[
                {
                    "child_operation_id": child_operation_id,
                    "child_index": 0,
                    "target_hash": content_hash(
                        {
                            "capability": capability,
                            "list_name": entities.get("list_name"),
                        }
                    ),
                    "arguments_hash": content_hash(entities),
                }
            ],
            recovery_manifest_hash=None,
            sensitivity="normal",
            persistence="standard",
        )
        reservation = self._manifest_reservation.reserve(
            ticket_id=child_id,
            request_id=origin_request_id,
            manifest=manifest,
        )
        manifest_hash = str(reservation["manifest_hash"])
        if self._execution_gateway is None:
            return self._fail_closed(child_id, "remediation_execution_gateway_unavailable")

        context = {
            "source_interface": "ticket_review_worker",
            "requested_by_user_id": str(parent_ticket.get("user_id") or "system"),
            "agent_id": str(parent_ticket.get("agent_id") or "jarvis"),
            "request_id": origin_request_id,
            "parent_ticket_id": parent_id,
            "child_ticket_id": child_id,
        }
        try:
            execution = self._execution_gateway.execute_remediation(
                operation_id=child_operation_id,
                capability=capability,
                entities=entities,
                context=context,
            )
        except Exception:
            return self._fail_closed(child_id, "remediation_effect_completion_unknown")
        if not isinstance(execution, Mapping):
            return self._fail_closed(child_id, "remediation_gateway_result_invalid")
        if str(execution.get("authorization_status") or "") != "authorized":
            self._outcome_sink.record_terminal_child(
                parent_operation_id=parent_operation_id,
                parent_manifest_hash=manifest_hash,
                child_operation_id=child_operation_id,
                effect_state="denied",
                reason_code="authorization_denied",
            )
            return self._fail_closed(child_id, "remediation_authorization_denied")
        if str(execution.get("approval_status") or "") not in {"approved", "not_required"}:
            return self._fail_closed(child_id, "remediation_approval_incomplete")

        receipt = execution.get("receipt")
        if not isinstance(receipt, Mapping):
            return self._fail_closed(child_id, "remediation_receipt_unavailable")
        if receipt.get("operation_id") != child_operation_id:
            return self._fail_closed(child_id, "remediation_operation_binding_conflict")
        result = execution.get("result")
        self._repository.append_entry(
            ticket_id=child_id,
            request_id=origin_request_id,
            entry_type=TicketEntryType.EXECUTION_COMPLETED.value,
            actor_type="domain",
            structured_payload=dict(result) if isinstance(result, Mapping) else {},
            dedupe_key=f"ticket:{child_id}:remediation-result",
        )
        self._outcome_sink.record_terminal_child(
            parent_operation_id=parent_operation_id,
            parent_manifest_hash=manifest_hash,
            child_operation_id=child_operation_id,
            effect_state="verified",
            receipt=receipt,
        )
        self._enqueue_plane(parent_id)
        self._enqueue_plane(child_id)
        return self._repository.get_ticket(child_id) or child
