from __future__ import annotations

from typing import Any

from app.core.action_execution import ActionExecutionService
from app.core.types import Intent
from app.provenance.repository import ProvenanceRepository
from app.reviews.repository import HumanReviewRepository
from app.skills.domains.documents.ports import DocumentQueryPort


class DocumentProposalExecutionService:
    """Executes one approved document proposal through the canonical action path."""

    def __init__(
        self,
        *,
        gateway: DocumentQueryPort,
        reviews: HumanReviewRepository,
        actions: ActionExecutionService,
        provenance: ProvenanceRepository,
    ) -> None:
        self.gateway = gateway
        self.reviews = reviews
        self.actions = actions
        self.provenance = provenance

    def execute_action_proposal(
        self,
        *,
        review_id: str,
        proposal_id: str,
        decision_id: str,
        operation_id: str,
    ) -> dict[str, Any]:
        review = self.reviews.get(review_id)
        decision = self.reviews.get_decision(decision_id)
        proposal = self.gateway.action_proposal(proposal_id=proposal_id)
        self._validate(review=review, decision=decision, proposal=proposal)
        if str(proposal.get("state")) == "executed":
            self.reviews.mark_applied(
                decision_id=decision_id,
                action_receipt_ref=str(proposal.get("execution_ref") or "") or None,
            )
            return {
                "status": "ok",
                "idempotent_replay": True,
                "proposal_id": proposal_id,
                "target_item_ref": proposal.get("target_item_ref"),
                "execution_ref": proposal.get("execution_ref"),
            }
        owner_id = str(proposal.get("owner_id") or "").strip()
        if not owner_id:
            raise ValueError("document proposal owner is unavailable")
        outcome = self.actions.execute_direct(
            request_id=str(operation_id),
            intent=Intent.LIST_ADD_ITEM,
            entities={
                "list_name": str(proposal["target_list_name"]),
                "item_text": str(proposal["action_text"]),
            },
            user_id=owner_id,
            agent_id="jarvis",
            source_interface="document_review",
            request_text="Approved document action proposal",
            route="document_review_approved",
        )
        if not outcome.authorized:
            raise PermissionError("document proposal list action is not authorized")
        response = dict(outcome.response)
        if str(response.get("status") or "").strip().casefold() not in {"ok", "partial"}:
            raise RuntimeError("document proposal list action did not complete")
        target_item_ref = str(response.get("item_id") or "").strip()
        if not target_item_ref:
            raise RuntimeError("document proposal list item reference is unavailable")
        ticket = response.get("ticket") if isinstance(response.get("ticket"), dict) else {}
        execution_ref = str(ticket.get("ticket_id") or response.get("request_id") or operation_id)
        provenance = self.provenance.create(
            source_domain="documents",
            source_type="action_proposal",
            source_ref=proposal_id,
            source_version=str(proposal["source_version_id"]),
            source_hash=str(proposal["item_hash"]),
            target_domain="lists",
            target_type="list_item",
            target_ref=target_item_ref,
            link_kind="derived_from",
            operation_id=f"{operation_id}:provenance",
        )
        bound = self.gateway.bind_action_execution(
            proposal_id=proposal_id,
            review_id=review_id,
            execution_ref=execution_ref,
            target_item_ref=target_item_ref,
        )
        self.reviews.mark_applied(
            decision_id=decision_id,
            action_receipt_ref=execution_ref,
        )
        return {
            "status": "ok",
            "proposal_id": proposal_id,
            "target_item_ref": target_item_ref,
            "execution_ref": execution_ref,
            "provenance_id": provenance["provenance_id"],
            "proposal_state": bound.get("state"),
            "action": response,
        }

    @staticmethod
    def _validate(
        *,
        review: dict[str, Any] | None,
        decision: dict[str, Any] | None,
        proposal: dict[str, Any],
    ) -> None:
        if review is None or decision is None:
            raise KeyError("document proposal approval is unavailable")
        if str(review.get("review_kind")) != "downstream_action":
            raise ValueError("document proposal review kind is invalid")
        if str(review.get("subject_type")) != "document_action_proposal":
            raise ValueError("document proposal review subject is invalid")
        if str(review.get("subject_id")) != str(proposal.get("proposal_id")):
            raise ValueError("document proposal review binding changed")
        if str(review.get("subject_version")) != str(proposal.get("source_version_id")):
            raise ValueError("document proposal source version changed")
        if str(review.get("item_hash")) != str(proposal.get("item_hash")):
            raise ValueError("document proposal value changed")
        if str(review.get("target_operation")) != "lists.add_item":
            raise ValueError("document proposal target operation is invalid")
        if str(review.get("state")) not in {"approved", "applied", "executed"}:
            raise ValueError("document proposal is not approved")
        if str(decision.get("review_id")) != str(review.get("review_id")):
            raise ValueError("document proposal decision binding changed")
        if str(decision.get("decision")) != "approve":
            raise ValueError("document proposal was rejected")
        if str(decision.get("bound_item_hash")) != str(review.get("item_hash")):
            raise ValueError("document proposal approval version changed")
