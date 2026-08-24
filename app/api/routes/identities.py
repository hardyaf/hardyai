from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.operator_auth import require_operator
from app.dependencies import (
    get_event_log,
    get_external_identity_service,
    get_skill_registry,
    get_ticket_repository,
)
from app.services.event_log import EventLogService
from app.services.identity_service import ExternalIdentityService
from app.skills.registry_service import SkillRegistryService
from app.tickets.repository import TicketRepository


router = APIRouter(
    prefix="/operator/identities",
    tags=["operator-identities"],
    dependencies=[Depends(require_operator)],
)


class IdentityBindingRequest(BaseModel):
    source: str = Field(min_length=1, max_length=40)
    external_user_id: str = Field(min_length=1, max_length=160)
    external_display_name: str | None = Field(default=None, max_length=160)
    user_id: str = Field(min_length=1, max_length=160)
    agent_id: str = Field(min_length=1, max_length=120)
    age_band: str | None = Field(default=None, max_length=40)
    presentation_profile: str = Field(default="default", max_length=80)
    policy_profile: str = Field(default="adult", max_length=80)
    active: bool = True


@router.get("")
async def list_identity_bindings(
    active_only: bool = False,
    repository: TicketRepository = Depends(get_ticket_repository),
) -> dict[str, Any]:
    return {"bindings": repository.list_identity_bindings(active_only=active_only)}


@router.put("")
async def upsert_identity_binding(
    payload: IdentityBindingRequest,
    identity_service: ExternalIdentityService = Depends(get_external_identity_service),
    event_log: EventLogService = Depends(get_event_log),
) -> dict[str, Any]:
    try:
        binding = identity_service.upsert(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    event_log.record(
        event_type="operator.identity_binding.updated",
        session_id="operator:identity",
        payload={
            "source": binding.get("source"),
            "external_user_id": binding.get("external_user_id"),
            "user_id": binding.get("user_id"),
            "agent_id": binding.get("agent_id"),
            "policy_profile": binding.get("policy_profile"),
            "active": binding.get("active"),
        },
    )
    return {"binding": binding}


class AgentProfileRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    wake_aliases: list[str] = Field(default_factory=list, max_length=20)
    personality_doc_path: str = Field(min_length=1, max_length=260)
    default_user_id: str | None = Field(default=None, max_length=160)
    active: bool = True


@router.get("/profiles")
async def list_agent_profiles(
    active_only: bool = False,
    skill_registry: SkillRegistryService = Depends(get_skill_registry),
) -> dict[str, Any]:
    return {"profiles": skill_registry.list_agent_profiles(active_only=active_only)}


@router.put("/profiles")
async def upsert_agent_profile(
    payload: AgentProfileRequest,
    skill_registry: SkillRegistryService = Depends(get_skill_registry),
    event_log: EventLogService = Depends(get_event_log),
) -> dict[str, Any]:
    try:
        profile = skill_registry.upsert_agent_profile(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    event_log.record(
        event_type="operator.agent_profile.updated",
        session_id="operator:identity",
        payload={
            "agent_id": profile.get("agent_id"),
            "personality_doc_path": profile.get("personality_doc_path"),
            "active": profile.get("active"),
        },
    )
    return {"profile": profile}
