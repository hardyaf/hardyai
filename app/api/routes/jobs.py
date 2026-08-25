from __future__ import annotations

from typing import Any
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.operator_auth import require_operator
from app.api.principals import RequestPrincipal
from app.dependencies import get_event_log, get_job_repository
from app.jobs.repository import DurableJobRepository
from app.jobs.types import JobStatus
from app.services.event_log import EventLogService


router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobControlRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.get("")
async def list_jobs(
    job_type: str | None = Query(default=None, max_length=120),
    status: JobStatus | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    _: RequestPrincipal = Depends(require_operator),
    repository: DurableJobRepository = Depends(get_job_repository),
) -> dict[str, Any]:
    return {"jobs": repository.list_jobs(job_type=job_type, status=status, limit=limit)}


@router.get("/{job_id}")
async def get_job(
    job_id: str,
    _: RequestPrincipal = Depends(require_operator),
    repository: DurableJobRepository = Depends(get_job_repository),
) -> dict[str, Any]:
    job = repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    return {"job": job}


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    body: JobControlRequest,
    principal: RequestPrincipal = Depends(require_operator),
    repository: DurableJobRepository = Depends(get_job_repository),
    event_log: EventLogService = Depends(get_event_log),
) -> dict[str, Any]:
    job = repository.request_cancel(job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    event_log.record(
        event_type="job.cancel_requested",
        session_id="operator:jobs",
        payload={
            "job_id": job_id,
            "job_type": job.get("job_type"),
            "actor": principal.subject,
            "reason_sha256": hashlib.sha256(body.reason.encode("utf-8")).hexdigest(),
        },
    )
    return {"job": job}


@router.post("/{job_id}/requeue")
async def requeue_job(
    job_id: str,
    body: JobControlRequest,
    principal: RequestPrincipal = Depends(require_operator),
    repository: DurableJobRepository = Depends(get_job_repository),
    event_log: EventLogService = Depends(get_event_log),
) -> dict[str, Any]:
    before = repository.get_job(job_id)
    if before is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    if before.get("status") not in {JobStatus.DEAD_LETTER.value, JobStatus.CANCELLED.value}:
        raise HTTPException(status_code=409, detail="job_not_requeueable")
    job = repository.requeue_job(job_id=job_id)
    event_log.record(
        event_type="job.requeued",
        session_id="operator:jobs",
        payload={
            "job_id": job_id,
            "job_type": before.get("job_type"),
            "actor": principal.subject,
            "reason_sha256": hashlib.sha256(body.reason.encode("utf-8")).hexdigest(),
        },
    )
    return {"job": job}
