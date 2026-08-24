from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.core.action_execution import ActionExecutionService
from app.core.router import JarvisRouter
from app.core.state_machine import RuntimePowerController
from app.services.durable_write_service import DurableWriteService
from app.services.event_log import EventLogService
from app.services.identity_service import ExternalIdentityService
from app.services.memory_service import MemoryService
from app.services.turn_service import TurnService
from app.skills.domains.calendar_inbox.service import CalendarInboxService
from app.skills.domains.email_agent.service import EmailAgentService
from app.skills.domains.private_notes.service import PrivateNotesDigestService
from app.skills.registry_service import SkillRegistryService
from app.tickets.repository import TicketRepository
from app.tickets.service import ActionTicketService
from app.tools.home_service import HomeService


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Explicit application service graph exposed to HTTP and lifecycle adapters."""

    settings: Settings
    router: JarvisRouter
    action_execution_service: ActionExecutionService
    turn_service: TurnService
    event_log: EventLogService
    memory_service: MemoryService
    home_service: HomeService
    skill_registry: SkillRegistryService
    ticket_repository: TicketRepository
    action_ticket_service: ActionTicketService
    external_identity_service: ExternalIdentityService
    private_notes_service: PrivateNotesDigestService
    calendar_inbox_service: CalendarInboxService | None
    email_agent_service: EmailAgentService | None
    durable_write_service: DurableWriteService
    runtime_power: RuntimePowerController

    @classmethod
    def from_default_runtime(cls) -> "ApplicationContainer":
        # Compatibility composition root while construction moves out of app.runtime.
        from app import runtime

        return cls(
            settings=runtime.settings,
            router=runtime.router,
            action_execution_service=runtime.action_execution_service,
            turn_service=runtime.turn_service,
            event_log=runtime.event_log,
            memory_service=runtime.memory_service,
            home_service=runtime.home_service,
            skill_registry=runtime.skill_registry,
            ticket_repository=runtime.ticket_repository,
            action_ticket_service=runtime.action_ticket_service,
            external_identity_service=runtime.external_identity_service,
            private_notes_service=runtime.private_notes_service,
            calendar_inbox_service=runtime.calendar_inbox_service,
            email_agent_service=runtime.email_agent_service,
            durable_write_service=runtime.durable_write_service,
            runtime_power=runtime.runtime_power,
        )
