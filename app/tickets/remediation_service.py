from __future__ import annotations

from typing import Any

from app.skills.domains.lists.handler import run as run_lists
from app.skills.domains.lists.receipts import build_operation_receipt as build_lists_receipt
from app.tickets.repository import TicketRepository, content_hash
from app.tickets.types import TicketEntryType, TicketKind, TicketStatus, iso_utc


class RemediationService:
    def __init__(
        self,
        *,
        repository: TicketRepository,
        lists_service: Any,
        review_delay_seconds: float,
        review_max_attempts: int,
        plane_enabled: bool = False,
    ) -> None:
        self._repository = repository
        self._lists_service = lists_service
        self._review_delay_seconds = max(0.0, float(review_delay_seconds))
        self._review_max_attempts = max(1, int(review_max_attempts))
        self._plane_enabled = bool(plane_enabled)

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
            },
            dedupe_key=f"ticket:{child_id}:remediation-request",
        )
        self._repository.transition_ticket(
            ticket_id=child_id,
            status=TicketStatus.EXECUTING,
        )

        context = {
            "source_interface": "ticket_review_worker",
            "requested_by_user_id": str(parent_ticket.get("user_id") or "system"),
            "list_owner_user_id": str(parent_ticket.get("user_id") or "all"),
            "agent_id": str(parent_ticket.get("agent_id") or "jarvis"),
            "request_id": origin_request_id,
        }
        if not capability.startswith("lists."):
            self._repository.transition_ticket(
                ticket_id=child_id,
                status=TicketStatus.RECONCILIATION_REQUIRED,
                completed_at=iso_utc(),
                terminal_reason="unsupported_remediation_capability",
            )
            self._enqueue_plane(child_id)
            return self._repository.get_ticket(child_id) or child

        result = run_lists(
            intent=capability,
            entities=entities,
            services={"lists_service": self._lists_service},
            context=context,
        )
        receipt = build_lists_receipt(
            intent=capability,
            entities=entities,
            context=context,
            result=result,
            services={"lists_service": self._lists_service},
        )
        self._repository.append_entry(
            ticket_id=child_id,
            request_id=origin_request_id,
            entry_type=TicketEntryType.EXECUTION_COMPLETED.value,
            actor_type="domain",
            structured_payload=result,
            dedupe_key=f"ticket:{child_id}:remediation-result",
        )
        if receipt is None:
            self._repository.transition_ticket(
                ticket_id=child_id,
                status=TicketStatus.UNVERIFIABLE,
                completed_at=iso_utc(),
                terminal_reason="remediation_receipt_unavailable",
            )
            self._enqueue_plane(child_id)
            return self._repository.get_ticket(child_id) or child

        recorded = self._repository.record_operation_receipt(ticket_id=child_id, receipt=receipt)
        expectation = self._repository.create_expectation(
            ticket_id=child_id,
            operation_id=str(recorded["operation_id"]),
            capability=str(recorded["capability"]),
            validator_name=str(recorded["validator_name"]),
            validator_version=str(recorded["validator_version"]),
            resource_locator=dict(recorded.get("resource_locator") or {}),
            expected_state=dict(recorded.get("expected_effect") or {}),
            source_revision_at_execution=(
                str(recorded.get("provider_revision"))
                if recorded.get("provider_revision") is not None
                else None
            ),
        )
        source_revision = content_hash(
            {
                "operation_id": recorded["operation_id"],
                "expected_state_hash": expectation["expected_state_hash"],
                "provider_revision": recorded.get("provider_revision"),
            }
        )
        self._repository.transition_ticket(
            ticket_id=child_id,
            status=TicketStatus.EXECUTING,
            resource_key=str(recorded["resource_key"]),
            source_action_revision=source_revision,
            expected_effect_hash=str(expectation["expected_state_hash"]),
        )
        self._repository.schedule_verification(
            ticket_id=child_id,
            source_action_revision=source_revision,
            delay_seconds=self._review_delay_seconds,
            max_attempts=self._review_max_attempts,
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
            structured_payload={"child_ticket_id": child_id, "capability": capability},
            dedupe_key=f"ticket:{parent_id}:child:{child_id}",
        )
        self._enqueue_plane(parent_id)
        self._enqueue_plane(child_id)
        return self._repository.get_ticket(child_id) or child
