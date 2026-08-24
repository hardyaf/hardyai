from __future__ import annotations

from typing import Any

from app.tickets.context_builder import ReviewContextBuilder
from app.tickets.remediation_policy import RemediationPolicy
from app.tickets.remediation_service import RemediationService
from app.tickets.repository import TicketRepository, content_hash
from app.tickets.review_backend import ReviewBackend
from app.tickets.types import (
    ReviewDecision,
    ReviewVerdict,
    SourceObservation,
    TicketEntryType,
    TicketStatus,
)
from app.tickets.verifier_registry import VerifierRegistry


class TicketReviewService:
    PROMPT_VERSION = "action-ticket-review-v1"

    def __init__(
        self,
        *,
        repository: TicketRepository,
        verifier_registry: VerifierRegistry,
        context_builder: ReviewContextBuilder,
        review_backend: ReviewBackend,
        remediation_policy: RemediationPolicy,
        remediation_service: RemediationService,
        auto_remediation_enabled: bool,
        plane_enabled: bool = False,
    ) -> None:
        self._repository = repository
        self._verifier_registry = verifier_registry
        self._context_builder = context_builder
        self._review_backend = review_backend
        self._remediation_policy = remediation_policy
        self._remediation_service = remediation_service
        self._auto_remediation_enabled = bool(auto_remediation_enabled)
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

    @staticmethod
    def _inconclusive_observation(
        *,
        expectation: dict[str, Any],
        receipt: dict[str, Any],
        error_code: str,
    ) -> SourceObservation:
        return SourceObservation(
            verifier_name=str(expectation.get("validator_name") or "unknown"),
            verifier_version=str(expectation.get("validator_version") or "unknown"),
            resource_key=str(receipt.get("resource_key") or ""),
            exists=None,
            normalized_state={},
            deterministic_verdict=ReviewVerdict.INCONCLUSIVE,
            observed_at=str(receipt.get("committed_at") or ""),
            limitations=(error_code,),
            error_code=error_code,
        )

    @staticmethod
    def _effective_decision(
        *,
        model_decision: ReviewDecision,
        observations: list[SourceObservation],
        later_tickets: list[dict[str, Any]],
    ) -> ReviewDecision:
        deterministic = {item.deterministic_verdict for item in observations}
        if ReviewVerdict.INCORRECT in deterministic and later_tickets:
            return ReviewDecision(
                verdict=ReviewVerdict.SUPERSEDED,
                confidence=1.0,
                summary="A later committed ticket changed the same resource after this operation.",
                evidence_refs=tuple(item.evidence_id for item in observations),
            )
        if ReviewVerdict.INCONCLUSIVE in deterministic:
            return ReviewDecision(
                verdict=ReviewVerdict.INCONCLUSIVE,
                confidence=0.0,
                summary="At least one trusted source verifier could not produce conclusive evidence.",
                evidence_refs=tuple(item.evidence_id for item in observations),
            )
        if ReviewVerdict.INCORRECT in deterministic and model_decision.verdict is not ReviewVerdict.INCORRECT:
            return ReviewDecision(
                verdict=ReviewVerdict.INCONCLUSIVE,
                confidence=0.0,
                summary="The model verdict conflicts with the trusted deterministic source comparison.",
                evidence_refs=tuple(item.evidence_id for item in observations),
            )
        if deterministic and deterministic <= {ReviewVerdict.CORRECT} and model_decision.verdict is not ReviewVerdict.CORRECT:
            return ReviewDecision(
                verdict=ReviewVerdict.INCONCLUSIVE,
                confidence=0.0,
                summary="The model verdict conflicts with source observations that satisfy all expectations.",
                evidence_refs=tuple(item.evidence_id for item in observations),
            )
        return model_decision

    def process_job(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        ticket_id = str(payload.get("ticket_id") or job.get("aggregate_id") or "")
        source_action_revision = str(payload.get("source_action_revision") or "")
        ticket = self._repository.get_ticket(ticket_id)
        if ticket is None:
            return {"status": "ignored", "reason": "ticket_missing"}
        if str(ticket.get("source_action_revision") or "") != source_action_revision:
            return {"status": "ignored", "reason": "source_action_revision_changed"}
        if str(ticket.get("status") or "") not in {
            TicketStatus.VERIFICATION_PENDING.value,
            TicketStatus.VERIFYING.value,
        }:
            return {"status": "ignored", "reason": "ticket_not_reviewable"}

        attempt = int(job.get("attempt_count") or 1)
        review_run = self._repository.start_review_run(
            ticket_id=ticket_id,
            source_action_revision=source_action_revision,
            attempt_number=attempt,
            prompt_version=self.PROMPT_VERSION,
        )
        review_run_id = str(review_run["review_run_id"])
        self._repository.transition_ticket(
            ticket_id=ticket_id,
            status=TicketStatus.VERIFYING,
        )

        try:
            expectations = self._repository.list_expectations(ticket_id)
            receipts = self._repository.list_receipts(ticket_id)
            receipt_by_operation = {str(item["operation_id"]): item for item in receipts}
            observations: list[SourceObservation] = []
            for expectation in expectations:
                receipt = receipt_by_operation.get(str(expectation.get("operation_id") or ""), {})
                verifier = self._verifier_registry.get(
                    name=str(expectation.get("validator_name") or ""),
                    version=str(expectation.get("validator_version") or ""),
                )
                if verifier is None:
                    observations.append(
                        self._inconclusive_observation(
                            expectation=expectation,
                            receipt=receipt,
                            error_code="trusted_verifier_not_registered",
                        )
                    )
                    continue
                observations.append(
                    verifier.observe(
                        resource_locator=dict(expectation.get("resource_locator") or {}),
                        expected_state=dict(expectation.get("expected_state") or {}),
                        operation_receipt=receipt,
                    )
                )

            later_tickets = self._repository.find_later_tickets(
                resource_key=str(ticket.get("resource_key") or ""),
                completed_after=str(ticket.get("completed_at") or ticket.get("created_at") or ""),
                exclude_ticket_id=ticket_id,
            )
            observation_payloads = [item.to_dict() for item in observations]
            context_pack, context_hash = self._context_builder.build(
                ticket=ticket,
                expectations=expectations,
                receipts=receipts,
                observations=observation_payloads,
                later_tickets=later_tickets,
            )
            model_decision = self._review_backend.review(context_pack)
            decision = self._effective_decision(
                model_decision=model_decision,
                observations=observations,
                later_tickets=later_tickets,
            )
            proposed = decision.repair.to_dict() if decision.repair is not None else None
            self._repository.complete_review_run(
                review_run_id=review_run_id,
                status="completed",
                deterministic_verdict=",".join(sorted({item.deterministic_verdict.value for item in observations})),
                model_verdict=decision.verdict.value,
                model_name=self._review_backend.model_name,
                context_pack_hash=context_hash,
                source_evidence={"observations": observation_payloads},
                source_evidence_hash=content_hash(observation_payloads),
                discrepancy=[dict(item) for item in decision.mismatches],
                proposed_repair=proposed,
            )
            request_id = f"review:{review_run_id}"
            self._repository.append_entry(
                ticket_id=ticket_id,
                request_id=request_id,
                entry_type=TicketEntryType.SOURCE_OBSERVATION.value,
                actor_type="verifier",
                structured_payload={"observations": observation_payloads},
                dedupe_key=f"ticket:{ticket_id}:review:{review_run_id}:source",
            )
            self._repository.append_entry(
                ticket_id=ticket_id,
                request_id=request_id,
                entry_type=TicketEntryType.REVIEW_RESULT.value,
                actor_type="review_model",
                actor_id=self._review_backend.model_name,
                structured_payload=decision.to_dict(),
                dedupe_key=f"ticket:{ticket_id}:review:{review_run_id}:result",
            )

            if decision.verdict is ReviewVerdict.CORRECT:
                updated = self._repository.transition_ticket(
                    ticket_id=ticket_id,
                    status=TicketStatus.VERIFIED,
                    terminal_reason="source_truth_verified",
                )
            elif decision.verdict is ReviewVerdict.SUPERSEDED:
                updated = self._repository.transition_ticket(
                    ticket_id=ticket_id,
                    status=TicketStatus.SUPERSEDED,
                    terminal_reason="later_committed_ticket_changed_resource",
                )
            elif decision.verdict is ReviewVerdict.INCONCLUSIVE:
                updated = self._repository.transition_ticket(
                    ticket_id=ticket_id,
                    status=TicketStatus.UNVERIFIABLE,
                    terminal_reason="review_inconclusive",
                )
            else:
                policy = self._remediation_policy.evaluate(
                    ticket=ticket,
                    proposed=decision.repair,
                    expectations=expectations,
                )
                if self._auto_remediation_enabled and policy.allowed and policy.repair is not None:
                    child = self._remediation_service.execute(
                        parent_ticket=ticket,
                        capability=policy.repair.capability,
                        entities=policy.repair.entities,
                        reason=policy.repair.reason,
                    )
                    updated = self._repository.get_ticket(ticket_id)
                    self._enqueue_plane(ticket_id)
                    if child:
                        self._enqueue_plane(str(child.get("ticket_id") or ""))
                    return {
                        "status": "remediation_queued",
                        "ticket": updated,
                        "child_ticket": child,
                        "review": decision.to_dict(),
                    }
                terminal = (
                    TicketStatus.ESCALATED
                    if policy.reason == "remediation_generation_cap_reached"
                    else TicketStatus.RECONCILIATION_REQUIRED
                )
                updated = self._repository.transition_ticket(
                    ticket_id=ticket_id,
                    status=terminal,
                    terminal_reason=policy.reason,
                )
            self._enqueue_plane(ticket_id)
            return {"status": "completed", "ticket": updated, "review": decision.to_dict()}
        except Exception as exc:
            self._repository.complete_review_run(
                review_run_id=review_run_id,
                status="failed",
                deterministic_verdict=None,
                model_verdict=None,
                model_name=self._review_backend.model_name,
                context_pack_hash=None,
                source_evidence={},
                source_evidence_hash=None,
                discrepancy=[],
                proposed_repair=None,
                error_code=type(exc).__name__,
            )
            self._repository.transition_ticket(
                ticket_id=ticket_id,
                status=TicketStatus.VERIFICATION_PENDING,
                terminal_reason=f"review_retry:{type(exc).__name__}",
            )
            raise
