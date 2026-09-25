from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.operator_auth import require_operator
from app.api.principals import RequestPrincipal
from app.dependencies import (
    get_document_proposal_execution_service,
    get_human_review_repository,
    get_human_review_service,
)
from app.services.document_proposal_execution_service import DocumentProposalExecutionService
from app.reviews.repository import HumanReviewRepository
from app.reviews.service import HumanReviewService
from app.reviews.types import ActionProposalState, ReviewDecisionKind, ReviewState


router = APIRouter(prefix="/reviews", tags=["reviews"])


def _content_free_action_proposal(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["destination_arguments_present"] = result.pop("destination_arguments", None) is not None
    result.pop("batch_manifest", None)
    result.pop("transfer_manifest", None)
    return result


class ReviewDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    bound_item_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=160)


class ReviewExecutionRequest(BaseModel):
    proposal_id: str = Field(min_length=8, max_length=120)
    decision_id: str = Field(min_length=8, max_length=120)
    operation_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")


class LocalActionDecisionRequest(BaseModel):
    proposal_id: str = Field(min_length=8, max_length=120)
    decision: Literal["approve", "reject"]
    bound_proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=160)


@router.get("/action-proposals")
async def list_action_proposals(
    state: ActionProposalState | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    _: RequestPrincipal = Depends(require_operator),
    repository: HumanReviewRepository = Depends(get_human_review_repository),
) -> dict[str, Any]:
    return {
        "proposals": [
            _content_free_action_proposal(item)
            for item in repository.list_action_proposals(state=state, limit=limit)
        ]
    }


@router.get("/action-proposals/{proposal_id}")
async def get_action_proposal(
    proposal_id: str,
    _: RequestPrincipal = Depends(require_operator),
    repository: HumanReviewRepository = Depends(get_human_review_repository),
) -> dict[str, Any]:
    proposal = repository.get_action_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="action_proposal_not_found")
    return {"proposal": _content_free_action_proposal(proposal)}


@router.get("")
async def list_reviews(
    state: ReviewState | None = None,
    subject_type: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=1000),
    _: RequestPrincipal = Depends(require_operator),
    repository: HumanReviewRepository = Depends(get_human_review_repository),
) -> dict[str, Any]:
    repository.expire_due()
    return {
        "reviews": repository.list_items(
            state=state,
            subject_type=subject_type,
            limit=limit,
        )
    }


@router.get("/{review_id}")
async def get_review(
    review_id: str,
    _: RequestPrincipal = Depends(require_operator),
    repository: HumanReviewRepository = Depends(get_human_review_repository),
) -> dict[str, Any]:
    review = repository.get(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review_not_found")
    return {"review": review}


@router.post("/{review_id}/decision")
async def decide_review(
    review_id: str,
    body: ReviewDecisionRequest,
    principal: RequestPrincipal = Depends(require_operator),
    service: HumanReviewService = Depends(get_human_review_service),
) -> dict[str, Any]:
    try:
        decision = service.decide(
            review_id=review_id,
            bound_item_hash=body.bound_item_hash,
            decision=ReviewDecisionKind(body.decision),
            actor_principal=principal.subject,
            reason=body.reason,
            idempotency_key=body.idempotency_key,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="review_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"decision": decision}


@router.post("/{review_id}/local-action-decision")
async def decide_local_action(
    review_id: str,
    body: LocalActionDecisionRequest,
    principal: RequestPrincipal = Depends(require_operator),
    service: HumanReviewService = Depends(get_human_review_service),
) -> dict[str, Any]:
    try:
        return service.decide_action_proposal(
            proposal_id=body.proposal_id,
            review_id=review_id,
            bound_proposal_hash=body.bound_proposal_hash,
            decision=ReviewDecisionKind(body.decision),
            actor_principal=principal.subject,
            destination_purpose="task_workspace",
            guild_id="",
            channel_id="",
            message_id="",
            reason=body.reason,
            idempotency_key=body.idempotency_key,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="action_proposal_not_found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{review_id}/execute-document-action")
async def execute_document_action_review(
    review_id: str,
    body: ReviewExecutionRequest,
    _: RequestPrincipal = Depends(require_operator),
    service: DocumentProposalExecutionService | None = Depends(
        get_document_proposal_execution_service
    ),
) -> dict[str, Any]:
    if service is None:
        raise HTTPException(status_code=503, detail="document_proposal_execution_unavailable")
    try:
        return service.execute_action_proposal(
            review_id=review_id,
            proposal_id=body.proposal_id,
            decision_id=body.decision_id,
            operation_id=body.operation_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="document_proposal_approval_not_found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
