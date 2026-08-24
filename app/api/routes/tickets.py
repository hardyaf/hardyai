from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.operator_auth import require_operator
from app.dependencies import get_ticket_repository
from app.tickets.repository import TicketRepository
from app.tickets.types import TicketStatus, iso_utc


router = APIRouter(
    prefix="/tickets",
    tags=["tickets"],
    dependencies=[Depends(require_operator)],
)


class RequeueReviewRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ResolveReconciliationRequest(BaseModel):
    resolution: Literal["verified", "cancelled", "escalated"]
    reason: str = Field(min_length=1, max_length=500)


@router.get("")
async def list_tickets(
    status: str | None = None,
    user_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    repository: TicketRepository = Depends(get_ticket_repository),
) -> dict[str, Any]:
    return {"tickets": repository.list_tickets(status=status, user_id=user_id, limit=limit)}


@router.get("/jobs")
async def list_ticket_jobs(
    job_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    repository: TicketRepository = Depends(get_ticket_repository),
) -> dict[str, Any]:
    return {"jobs": repository.list_jobs(job_type=job_type, limit=limit)}


def _require_ticket(repository: TicketRepository, ticket_id: str) -> dict[str, Any]:
    ticket = repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket_not_found")
    return ticket


@router.get("/{ticket_id}")
async def get_ticket(
    ticket_id: str,
    repository: TicketRepository = Depends(get_ticket_repository),
) -> dict[str, Any]:
    return {"ticket": _require_ticket(repository, ticket_id)}


@router.get("/{ticket_id}/entries")
async def get_ticket_entries(
    ticket_id: str,
    repository: TicketRepository = Depends(get_ticket_repository),
) -> dict[str, Any]:
    _require_ticket(repository, ticket_id)
    return {"entries": repository.list_entries(ticket_id)}


@router.get("/{ticket_id}/reviews")
async def get_ticket_reviews(
    ticket_id: str,
    repository: TicketRepository = Depends(get_ticket_repository),
) -> dict[str, Any]:
    _require_ticket(repository, ticket_id)
    return {"reviews": repository.list_review_runs(ticket_id)}


@router.get("/{ticket_id}/lineage")
async def get_ticket_lineage(
    ticket_id: str,
    repository: TicketRepository = Depends(get_ticket_repository),
) -> dict[str, Any]:
    _require_ticket(repository, ticket_id)
    return {"lineage": repository.list_lineage(ticket_id)}


@router.post("/{ticket_id}/requeue-review")
async def requeue_ticket_review(
    ticket_id: str,
    payload: RequeueReviewRequest,
    repository: TicketRepository = Depends(get_ticket_repository),
) -> dict[str, Any]:
    ticket = _require_ticket(repository, ticket_id)
    revision = str(ticket.get("source_action_revision") or "").strip()
    if not revision:
        raise HTTPException(status_code=409, detail="ticket_has_no_source_action_revision")
    try:
        repository.transition_ticket(
            ticket_id=ticket_id,
            status=TicketStatus.VERIFICATION_PENDING,
            terminal_reason=f"operator_requeue:{payload.reason}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    job = repository.enqueue_job(
        job_type="ticket_review",
        aggregate_id=ticket_id,
        idempotency_key=f"operator-review:{ticket_id}:{revision}:{uuid4()}",
        payload={"ticket_id": ticket_id, "source_action_revision": revision, "operator_reason": payload.reason},
        available_at=iso_utc(),
    )
    return {"ticket": repository.get_ticket(ticket_id), "job": job}


@router.post("/{ticket_id}/resolve-reconciliation")
async def resolve_ticket_reconciliation(
    ticket_id: str,
    payload: ResolveReconciliationRequest,
    repository: TicketRepository = Depends(get_ticket_repository),
) -> dict[str, Any]:
    ticket = _require_ticket(repository, ticket_id)
    if str(ticket.get("status") or "") not in {
        TicketStatus.RECONCILIATION_REQUIRED.value,
        TicketStatus.ESCALATED.value,
        TicketStatus.UNVERIFIABLE.value,
    }:
        raise HTTPException(status_code=409, detail="ticket_not_in_reconcilable_state")
    target = {
        "verified": TicketStatus.VERIFIED,
        "cancelled": TicketStatus.CANCELLED,
        "escalated": TicketStatus.ESCALATED,
    }[payload.resolution]
    updated = repository.transition_ticket(
        ticket_id=ticket_id,
        status=target,
        terminal_reason=f"operator_resolution:{payload.reason}",
    )
    return {"ticket": updated}
