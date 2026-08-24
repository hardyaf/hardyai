from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, HTTPException
from uuid import uuid4

from app.api.operator_auth import require_operator
from app.api.principals import RequestPrincipal
from app.core.action_execution import ActionExecutionService
from app.core.types import Intent
from app.dependencies import get_action_execution_service, get_home_service
from app.tools.home_service import HomeService

router = APIRouter(
    prefix="/house",
    tags=["house"],
    dependencies=[Depends(require_operator)],
)


class SetSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    request_id: str | None = None


@router.get("/switches")
async def list_switches(home: HomeService = Depends(get_home_service)) -> dict:
    # Operational/dashboard polling is telemetry, not a household request. User
    # actions through /ask and direct mutations below enter the ticket ledger.
    return {"switches": home.list_switches()}


@router.post("/switches/{switch_name}")
async def set_switch(
    switch_name: str,
    payload: SetSwitchRequest,
    principal: RequestPrincipal = Depends(require_operator),
    execution: ActionExecutionService = Depends(get_action_execution_service),
) -> dict:
    request_id = str(payload.request_id or uuid4())
    outcome = execution.execute_direct(
        request_id=request_id,
        intent=Intent.HOME_SET_SWITCH,
        entities={"switch_name": switch_name, "action": payload.action},
        user_id=principal.user_id,
        agent_id="jarvis",
        source_interface=principal.source,
        request_text=f"Set {switch_name} {payload.action}",
        route="direct_house_api",
    )
    if not outcome.authorized:
        raise HTTPException(status_code=403, detail="skill_unavailable_or_unauthorized")
    return outcome.response


@router.get("/switch-actions")
async def recent_switch_actions(limit: int = 100, home: HomeService = Depends(get_home_service)) -> dict:
    return {"actions": home.recent_actions(limit=limit)}
