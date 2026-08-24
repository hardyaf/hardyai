from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, HTTPException
from uuid import uuid4

from app.api.operator_auth import require_operator
from app.api.principals import RequestPrincipal
from app.dependencies import get_action_ticket_service, get_home_service, get_skill_registry
from app.skills.registry_service import SkillRegistryService
from app.skills.domains.lights.receipts import build_operation_receipt
from app.tickets.service import ActionTicketService
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
    home: HomeService = Depends(get_home_service),
    tickets: ActionTicketService = Depends(get_action_ticket_service),
    skill_registry: SkillRegistryService = Depends(get_skill_registry),
) -> dict:
    request_id = str(payload.request_id or uuid4())
    user_id = principal.user_id
    source_interface = principal.source
    skill = skill_registry.resolve_skill(
        intent="home.set_switch",
        user_id=user_id,
        agent_id="jarvis",
    )
    if not isinstance(skill, dict) or not str(skill.get("execution_ref") or "").strip():
        raise HTTPException(status_code=403, detail="skill_unavailable_or_unauthorized")
    replay = tickets.replay_response(request_id)
    if replay is not None:
        response = dict(replay.get("result") or {})
        response["request_id"] = request_id
        ticket = dict(replay.get("ticket") or {})
        response["ticket"] = {
            "ticket_id": ticket.get("ticket_id"),
            "status": ticket.get("status"),
            "review_due_at": ticket.get("review_due_at"),
        }
        return response
    classification = {
        "intent": "home.set_switch",
        "confidence": 1.0,
        "entities": {"switch_name": switch_name, "action": payload.action},
        "ambiguity_flags": [],
        "recommended_owner": "micro",
        "reasoning": "direct_house_api",
    }
    started = tickets.begin_request(
        request_id=request_id,
        session_id=f"direct:house:{request_id}",
        context_reference={},
        user_id=user_id,
        agent_id="jarvis",
        source=source_interface,
        intent="home.set_switch",
        skill_id="skill.lights.core",
        route="direct_house_api",
        request_text=f"Set {switch_name} {payload.action}",
        classification=classification,
        force=True,
    )
    result = home.set_switch(
        switch_name=switch_name,
        action=payload.action,
        source_interface=source_interface,
        requested_by_user_id=user_id,
    )
    receipt = build_operation_receipt(
        intent="home.set_switch",
        entities={"switch_name": switch_name, "action": payload.action},
        context={
            "request_id": request_id,
            "source_interface": source_interface,
            "requested_by_user_id": user_id,
        },
        result=result,
        services={"home_service": home},
    )
    result_with_internal = dict(result)
    if receipt is not None:
        result_with_internal["_operation_receipt"] = receipt
    capture = tickets.capture_response(
        request_id=request_id,
        session_id=f"direct:house:{request_id}",
        context_reference=started.context_reference,
        user_id=user_id,
        agent_id="jarvis",
        source=source_interface,
        intent="home.set_switch",
        skill_id="skill.lights.core",
        route="direct_house_api",
        request_text=f"Set {switch_name} {payload.action}",
        classification=classification,
        result_with_internal=result_with_internal,
        dialog={"mode": "command_action", "turn_complete": True, "status": result.get("status")},
        assistant_text=str(result.get("message") or f"Set {switch_name} {payload.action}."),
    )
    public_result = tickets.strip_internal_fields(result_with_internal)
    response = dict(public_result) if isinstance(public_result, dict) else dict(result)
    response["request_id"] = request_id
    if capture.ticket is not None:
        response["ticket"] = {
            "ticket_id": capture.ticket.get("ticket_id"),
            "status": capture.ticket.get("status"),
            "review_due_at": capture.ticket.get("review_due_at"),
        }
    return response


@router.get("/switch-actions")
async def recent_switch_actions(limit: int = 100, home: HomeService = Depends(get_home_service)) -> dict:
    return {"actions": home.recent_actions(limit=limit)}
