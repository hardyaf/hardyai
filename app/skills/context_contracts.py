from __future__ import annotations

from typing import Any, Callable, Protocol

from app.context.reference_resolver import ReferenceResolver
from app.context.types import EntityRegistry


class SkillContextContract(Protocol):
    contract_id: str

    def supports_intent(self, *, intent: str) -> bool:
        ...

    def emit_context_updates(self, *, intent: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        ...

    def normalize_entities(self, *, intent: str, entities: dict[str, Any]) -> dict[str, Any]:
        ...

    def apply_text_constraints(
        self,
        *,
        intent: str,
        text: str,
        entities: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def clarification_supplemental_fields(self, *, intent: str) -> list[str]:
        ...

    def resolve_followup(
        self,
        *,
        decision: Any,
        registry: EntityRegistry,
        resolver: ReferenceResolver,
        required_fields_for_intent: Callable[[Any, dict[str, Any]], list[str]],
        has_blocking_ambiguity: Callable[[Any], bool],
    ) -> Any:
        ...

    def refine_missing_fields(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        missing_fields: list[str],
        resolver: ReferenceResolver,
    ) -> list[str]:
        ...

    def required_fields(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        resolver: ReferenceResolver,
    ) -> list[str] | None:
        ...

    def clarification_question(
        self,
        *,
        intent: str,
        field_name: str,
    ) -> str | None:
        ...

    def continue_pending_interaction(
        self,
        *,
        intent: str,
        text: str,
        missing_fields: list[str],
        current_entities: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def shape_tool_followup(
        self,
        *,
        intent: str,
        status: str,
        tool_result: dict[str, Any],
        entities: dict[str, Any],
        missing_fields: list[str],
        question: str | None,
        registry: EntityRegistry,
    ) -> dict[str, Any]:
        ...

    def legacy_main_handoff_hints(
        self,
        *,
        registry: EntityRegistry,
        context_reference: dict[str, Any],
        runtime_context: dict[str, Any] | None = None,
        intent: str | None = None,
        route: str | None = None,
    ) -> dict[str, Any]:
        ...

    def memory_handoff_hints(
        self,
        *,
        relevant_memory: list[dict[str, Any]],
        intent: str | None = None,
        request_text: str | None = None,
    ) -> dict[str, Any]:
        ...


def default_skill_context_contracts(*, email_agent_service: Any | None = None) -> list[SkillContextContract]:
    from app.skills.domains.calendar.context import CalendarContextContract
    from app.skills.domains.conversation.context import ConversationContextContract
    from app.skills.domains.lights.context import LightsContextContract
    from app.skills.domains.lists.context import ListsContextContract
    from app.skills.domains.email_agent.context import EmailAgentContextContract

    return [
        ListsContextContract(),
        LightsContextContract(),
        CalendarContextContract(),
        EmailAgentContextContract(email_agent_service=email_agent_service),
        ConversationContextContract(),
    ]
