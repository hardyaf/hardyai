from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.operator_auth import require_operator
from app.api.principals import RequestPrincipal
from app.dependencies import get_human_review_repository, get_human_review_service
from app.reviews.repository import HumanReviewRepository
from app.reviews.service import HumanReviewService
from app.reviews.types import ReviewDecisionKind, ReviewState


router = APIRouter(prefix="/reviews", tags=["reviews"])


class ReviewDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    bound_item_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=160)


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
