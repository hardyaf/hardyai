from __future__ import annotations

from fastapi import Request

from app.container import ApplicationContainer
from app.core.action_execution import ActionExecutionService
from app.core.router import JarvisRouter
from app.core.state_machine import RuntimePowerController
from app.services.event_log import EventLogService
from app.services.memory_service import MemoryService
from app.services.turn_service import TurnService
from app.skills.registry_service import SkillRegistryService
from app.tools.home_service import HomeService
from app.tickets.repository import TicketRepository
from app.tickets.service import ActionTicketService
from app.services.identity_service import ExternalIdentityService
from app.skills.domains.private_notes.service import PrivateNotesDigestService
from app.skills.domains.calendar_inbox.service import CalendarInboxService
from app.skills.domains.email_agent.service import EmailAgentService


def get_container(request: Request) -> ApplicationContainer:
    container = getattr(request.app.state, "container", None)
    if not isinstance(container, ApplicationContainer):
        raise RuntimeError("Application container has not been configured.")
    return container


def get_router(request: Request) -> JarvisRouter:
    return get_container(request).router


def get_action_execution_service(request: Request) -> ActionExecutionService:
    return get_container(request).action_execution_service


def get_turn_service(request: Request) -> TurnService:
    return get_container(request).turn_service


def get_event_log(request: Request) -> EventLogService:
    return get_container(request).event_log


def get_memory_service(request: Request) -> MemoryService:
    return get_container(request).memory_service


def get_home_service(request: Request) -> HomeService:
    return get_container(request).home_service


def get_skill_registry(request: Request) -> SkillRegistryService:
    return get_container(request).skill_registry


def get_ticket_repository(request: Request) -> TicketRepository:
    return get_container(request).ticket_repository


def get_action_ticket_service(request: Request) -> ActionTicketService:
    return get_container(request).action_ticket_service


def get_external_identity_service(request: Request) -> ExternalIdentityService:
    return get_container(request).external_identity_service


def get_private_notes_service(request: Request) -> PrivateNotesDigestService:
    return get_container(request).private_notes_service


def get_calendar_inbox_service(request: Request) -> CalendarInboxService | None:
    return get_container(request).calendar_inbox_service


def get_email_agent_service(request: Request) -> EmailAgentService | None:
    return get_container(request).email_agent_service


def get_runtime_power(request: Request) -> RuntimePowerController:
    return get_container(request).runtime_power
