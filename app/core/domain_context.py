from __future__ import annotations

from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.context.reference_resolver import ReferenceResolver
from app.core.session_store import SessionRecord
from app.core.types import Intent
from app.services.event_log import EventLogService
from app.skills.context_contracts import SkillContextContract


class DomainContextService:
    """Applies skill-owned context rules without embedding domain policy in routing."""

    def __init__(
        self,
        *,
        contracts: Iterable[SkillContextContract],
        reference_resolver: ReferenceResolver,
        event_log: EventLogService,
        email_timezone: str | None = None,
        calendar_timezone_resolver: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> None:
        self._contracts = list(contracts)
        self._reference_resolver = reference_resolver
        self._event_log = event_log
        self._email_timezone = self._validated_timezone(email_timezone)
        self._calendar_timezone_resolver = calendar_timezone_resolver

    def set_contracts(self, contracts: Iterable[SkillContextContract]) -> None:
        self._contracts = list(contracts)

    def resolve_tool_timezone(
        self,
        *,
        tool_id: str,
        request_context: dict[str, Any],
    ) -> str | None:
        """Resolve a server-owned domain timezone without trusting model/transport values."""

        domain = str(tool_id or "").strip().casefold().partition(".")[0]
        if domain == "email":
            return self._email_timezone
        if domain == "calendar":
            if not callable(self._calendar_timezone_resolver):
                return None
            try:
                return self._validated_timezone(
                    self._calendar_timezone_resolver(dict(request_context))
                )
            except Exception:  # pragma: no cover - defensive domain resolver isolation
                return None
        return "UTC"

    @staticmethod
    def _validated_timezone(value: str | None) -> str | None:
        cleaned = str(value or "").strip()
        if not cleaned:
            return None
        try:
            ZoneInfo(cleaned)
        except (ZoneInfoNotFoundError, ValueError):
            return None
        return cleaned

    def normalize_entities(self, *, intent: Intent, entities: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(entities)
        for contract in self._supporting_contracts(intent=intent):
            try:
                candidate = contract.normalize_entities(intent=intent.value, entities=dict(normalized))
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                self._record_contract_failure(
                    contract=contract,
                    hook="normalize_entities",
                    intent=intent,
                    error=type(exc).__name__,
                )
                continue
            if isinstance(candidate, dict):
                normalized = candidate
        return normalized

    def apply_text_constraints(
        self,
        *,
        intent: Intent,
        text: str,
        entities: dict[str, Any],
    ) -> dict[str, Any]:
        constrained = dict(entities)
        for contract in self._supporting_contracts(intent=intent):
            try:
                candidate = contract.apply_text_constraints(
                    intent=intent.value,
                    text=text,
                    entities=dict(constrained),
                )
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                self._record_contract_failure(
                    contract=contract,
                    hook="apply_text_constraints",
                    intent=intent,
                    error=type(exc).__name__,
                )
                continue
            if isinstance(candidate, dict):
                constrained = candidate
        return constrained

    def clarification_supplemental_fields(self, *, intent: Intent) -> list[str]:
        fields: list[str] = []
        for contract in self._supporting_contracts(intent=intent):
            try:
                candidates = contract.clarification_supplemental_fields(intent=intent.value)
            except Exception:  # pragma: no cover - defensive contract isolation
                continue
            if not isinstance(candidates, list):
                continue
            fields = self.merge_missing_fields(fields, candidates)
        return fields

    def required_fields(self, *, intent: Intent, entities: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        saw_required_hook = False
        contracts = self._supporting_contracts(intent=intent)
        for contract in contracts:
            try:
                contract_required = contract.required_fields(
                    intent=intent.value,
                    entities=dict(entities),
                    resolver=self._reference_resolver,
                )
            except Exception:  # pragma: no cover - defensive contract isolation
                continue
            if not isinstance(contract_required, list):
                continue
            saw_required_hook = True
            missing = self.merge_missing_fields(missing, contract_required)

        if not saw_required_hook:
            missing = []

        missing = self.normalize_missing_fields(missing)
        for contract in contracts:
            try:
                refined = contract.refine_missing_fields(
                    intent=intent.value,
                    entities=dict(entities),
                    missing_fields=list(missing),
                    resolver=self._reference_resolver,
                )
            except Exception:  # pragma: no cover - defensive contract isolation
                continue
            if isinstance(refined, list):
                missing = self.normalize_missing_fields(refined)
        return self.normalize_missing_fields(missing)

    def clarification_question(self, *, intent: Intent, field_name: str) -> str:
        cleaned_field = str(field_name or "").strip() or "that field"
        for contract in self._supporting_contracts(intent=intent):
            try:
                candidate = contract.clarification_question(
                    intent=intent.value,
                    field_name=cleaned_field,
                )
            except Exception:  # pragma: no cover - defensive contract isolation
                continue
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return f"What should I use for `{cleaned_field}`?"

    def extract_pending_updates(
        self,
        *,
        session: SessionRecord,
        intent: Intent,
        text: str,
        missing_fields: list[str],
        current_entities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not text.strip() or not missing_fields:
            return {}
        updates: dict[str, Any] = {}
        entities = current_entities if isinstance(current_entities, dict) else {}
        for contract in self._supporting_contracts(intent=intent):
            try:
                contract_updates = contract.continue_pending_interaction(
                    intent=intent.value,
                    text=text,
                    missing_fields=list(missing_fields),
                    current_entities=dict(entities),
                )
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                self._event_log.record(
                    event_type="context.contract.continue_pending_interaction.failed",
                    session_id=session.session_id,
                    payload={
                        "contract_id": str(getattr(contract, "contract_id", "") or ""),
                        "intent": intent.value,
                        "error": str(exc),
                    },
                )
                continue
            if isinstance(contract_updates, dict):
                updates.update({str(key): value for key, value in contract_updates.items() if value is not None})
        return updates

    @staticmethod
    def merge_missing_fields(base: list[str], candidate: list[str]) -> list[str]:
        merged = [str(item).strip() for item in base if str(item).strip()]
        seen = {item.lower() for item in merged}
        for raw in candidate:
            field_name = str(raw).strip()
            if field_name and field_name.lower() not in seen:
                seen.add(field_name.lower())
                merged.append(field_name)
        return merged

    @staticmethod
    def normalize_missing_fields(value: list[str]) -> list[str]:
        return DomainContextService.merge_missing_fields([], value)

    def _supporting_contracts(self, *, intent: Intent) -> list[SkillContextContract]:
        return [contract for contract in self._contracts if contract.supports_intent(intent=intent.value)]

    def _record_contract_failure(
        self,
        *,
        contract: SkillContextContract,
        hook: str,
        intent: Intent,
        error: str,
    ) -> None:
        self._event_log.record(
            event_type=f"context.contract.{hook}.failed",
            session_id="system:entity-normalization",
            payload={
                "contract_id": str(getattr(contract, "contract_id", "") or ""),
                "intent": intent.value,
                "error": error,
            },
        )
