from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.operator_auth import require_operator
from app.api.principals import RequestPrincipal
from app.dependencies import get_event_log, get_memory_service, get_router, get_turn_service
from app.schemas.api import AskRequest, AskResponse
from app.services.event_log import EventLogService
from app.services.memory_service import MemoryService
from app.services.turn_service import TurnQueueFullError, TurnService, TurnTimeoutError

router = APIRouter(tags=["ask"])


@router.post("/ask", response_model=AskResponse)
async def ask(
    payload: AskRequest,
    principal: RequestPrincipal = Depends(require_operator),
    turn_service: TurnService = Depends(get_turn_service),
) -> dict:
    try:
        return await turn_service.route(payload, principal=principal)
    except TurnQueueFullError as exc:
        raise HTTPException(status_code=503, detail="turn_queue_full") from exc
    except TurnTimeoutError as exc:
        raise HTTPException(status_code=504, detail="turn_timeout") from exc


@router.get("/events", dependencies=[Depends(require_operator)])
async def events(event_log: EventLogService = Depends(get_event_log)) -> dict:
    return {"events": event_log.recent(limit=200)}


@router.get("/memory/recent", dependencies=[Depends(require_operator)])
async def memory_recent(memory: MemoryService = Depends(get_memory_service)) -> dict:
    return {"memory": memory.recent(limit=200)}


@router.get(
    "/sessions/{session_id}/context",
    dependencies=[Depends(require_operator)],
)
async def session_context_snapshot(
    session_id: str,
    include_legacy: bool = True,
    include_working_context: bool = True,
    include_recent_events: bool = True,
    recent_events_limit: int = Query(default=120, ge=20, le=500),
    jarvis_router=Depends(get_router),
) -> dict:
    snapshot = jarvis_router.export_session_context_snapshot(
        session_id=session_id,
        include_legacy=include_legacy,
        include_working_context=include_working_context,
        include_recent_events=include_recent_events,
        recent_events_limit=recent_events_limit,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return snapshot
