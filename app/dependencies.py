from __future__ import annotations

from app.core.router import JarvisRouter
from app.runtime import (
    action_ticket_service,
    event_log,
    home_service,
    memory_service,
    router,
    skill_registry,
    ticket_repository,
    turn_service,
    external_identity_service,
    private_notes_service,
    calendar_inbox_service,
    email_agent_service,
)
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


def get_router() -> JarvisRouter:
    return router


def get_turn_service() -> TurnService:
    return turn_service


def get_event_log() -> EventLogService:
    return event_log


def get_memory_service() -> MemoryService:
    return memory_service


def get_home_service() -> HomeService:
    return home_service


def get_skill_registry() -> SkillRegistryService:
    return skill_registry


def get_ticket_repository() -> TicketRepository:
    return ticket_repository


def get_action_ticket_service() -> ActionTicketService:
    return action_ticket_service


def get_external_identity_service() -> ExternalIdentityService:
    return external_identity_service


def get_private_notes_service() -> PrivateNotesDigestService:
    return private_notes_service


def get_calendar_inbox_service() -> CalendarInboxService | None:
    return calendar_inbox_service


def get_email_agent_service() -> EmailAgentService | None:
    return email_agent_service
