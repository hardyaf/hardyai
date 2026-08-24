from __future__ import annotations

from typing import Any

from app.skills.execution_dispatcher import SkillExecutionDispatcher


class AuthorizedSkillExecutor:
    """Resolve and execute only registry-authorized skill records."""

    def __init__(self, *, skill_registry: Any | None, dispatcher: SkillExecutionDispatcher) -> None:
        self._skill_registry = skill_registry
        self._dispatcher = dispatcher

    @staticmethod
    def build_context(
        *,
        source_interface: str,
        requested_by_user_id: str,
        agent_id: str,
        request_context: dict[str, Any] | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "source_interface": source_interface,
            "requested_by_user_id": requested_by_user_id,
            "agent_id": agent_id,
            "request_id": str(request_id or ""),
        }
        if isinstance(request_context, dict):
            for key in (
                "discord_channel_id",
                "discord_guild_id",
                "external_user_id",
                "identity_bound",
                "skill_scopes",
            ):
                if key in request_context:
                    context[key] = request_context[key]
        return context

    def resolve(self, *, intent: str, user_id: str, agent_id: str) -> dict[str, Any] | None:
        if self._skill_registry is None:
            return None
        resolve = getattr(self._skill_registry, "resolve_skill", None)
        if not callable(resolve):
            return None
        skill = resolve(intent=intent, user_id=user_id, agent_id=agent_id)
        return skill if isinstance(skill, dict) else None

    def execute(
        self,
        *,
        intent: str,
        entities: dict[str, Any],
        source_interface: str,
        requested_by_user_id: str,
        agent_id: str,
        request_context: dict[str, Any] | None,
        request_id: str | None,
        resolved_skill: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        skill = resolved_skill or self.resolve(
            intent=intent,
            user_id=requested_by_user_id,
            agent_id=agent_id,
        )
        if skill is None:
            return {
                "status": "policy_denied",
                "message": "This skill is not currently available for this user and agent.",
                "intent": intent,
                "denial_reason": "skill_unavailable_or_unauthorized",
                "dispatch_mode": "registry_only",
            }

        result = self._dispatcher.execute(
            skill=skill,
            intent=intent,
            entities=entities,
            context=self.build_context(
                source_interface=source_interface,
                requested_by_user_id=requested_by_user_id,
                agent_id=agent_id,
                request_context=request_context,
                request_id=request_id,
            ),
        )
        if isinstance(result, dict):
            return result
        return {
            "status": "policy_denied",
            "message": "This skill is not currently executable.",
            "intent": intent,
            "denial_reason": "handler_unavailable",
            "dispatch_mode": "registry_only",
        }


class RuntimeCapabilityProjector:
    """Build the bounded, model-safe capability view for one request scope."""

    def __init__(
        self,
        *,
        skill_registry: Any | None,
        dispatcher: SkillExecutionDispatcher,
        main_action_intents: set[str],
        known_intents: set[str],
    ) -> None:
        self._skill_registry = skill_registry
        self._dispatcher = dispatcher
        self._main_action_intents = set(main_action_intents)
        self._known_intents = set(known_intents)

    def project(
        self,
        *,
        user_id: str,
        agent_id: str,
        source_interface: str,
        request_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if self._skill_registry is None:
            return []
        project = getattr(self._skill_registry, "runtime_capability_catalog", None)
        if not callable(project):
            return []
        try:
            base_catalog = project(user_id=user_id, agent_id=agent_id)
        except Exception:
            return []
        if not isinstance(base_catalog, list):
            return []

        execution_context = AuthorizedSkillExecutor.build_context(
            source_interface=source_interface,
            requested_by_user_id=user_id,
            agent_id=agent_id,
            request_context=request_context,
            request_id=None,
        )
        catalog: list[dict[str, Any]] = []
        for raw in base_catalog[:32]:
            if not isinstance(raw, dict):
                continue
            entry = dict(raw)
            documented_intents = [
                str(item or "").strip().casefold()
                for item in entry.get("intents") or []
                if str(item or "").strip()
            ]
            entry["intents"] = documented_intents
            entry["main_intents"] = [
                intent for intent in documented_intents if intent in self._main_action_intents
            ]
            entry["micro_intents"] = [
                str(item or "").strip().casefold()
                for item in entry.get("micro_intents") or []
                if str(item or "").strip().casefold() in self._known_intents
            ]
            skill = self._resolve_catalog_skill(
                entry=entry,
                user_id=user_id,
                agent_id=agent_id,
            )
            availability = self._dispatcher.describe_capability(
                skill=skill,
                context=execution_context,
            )
            self._merge_availability(
                entry=entry,
                availability=availability,
                documented_intents=documented_intents,
            )
            contracts = self._safe_contracts(
                availability=availability,
                executable_main=set(entry.get("main_intents") or []),
            )
            if contracts:
                entry["intent_contracts"] = contracts
            catalog.append(entry)
        return catalog

    def _resolve_catalog_skill(
        self,
        *,
        entry: dict[str, Any],
        user_id: str,
        agent_id: str,
    ) -> dict[str, Any] | None:
        for raw_intent in entry.get("intents") or []:
            candidate = self._skill_registry.resolve_skill(
                intent=str(raw_intent or ""),
                user_id=user_id,
                agent_id=agent_id,
            )
            if (
                isinstance(candidate, dict)
                and str(candidate.get("skill_id") or "").strip()
                == str(entry.get("skill_id") or "").strip()
            ):
                return candidate
        return None

    def _merge_availability(
        self,
        *,
        entry: dict[str, Any],
        availability: dict[str, Any],
        documented_intents: list[str],
    ) -> None:
        for key in ("configured", "authorized_here", "availability", "access_note", "main_intents"):
            if key not in availability:
                continue
            if key == "main_intents":
                entry[key] = [
                    str(item or "").strip().casefold()
                    for item in availability.get(key) or []
                    if str(item or "").strip().casefold() in self._main_action_intents
                    and str(item or "").strip().casefold() in documented_intents
                ]
            else:
                entry[key] = availability[key]

    @staticmethod
    def _safe_contracts(
        *,
        availability: dict[str, Any],
        executable_main: set[str],
    ) -> list[dict[str, Any]]:
        import re

        safe_contracts: list[dict[str, Any]] = []
        raw_contracts = availability.get("intent_contracts")
        if not isinstance(raw_contracts, list):
            return safe_contracts
        for raw_contract in raw_contracts[:32]:
            if not isinstance(raw_contract, dict):
                continue
            contract_intent = str(raw_contract.get("intent") or "").strip().casefold()
            if contract_intent not in executable_main:
                continue
            purpose = re.sub(r"\s+", " ", str(raw_contract.get("purpose") or "").strip())[:240]
            operation = str(raw_contract.get("operation") or "").strip().casefold()
            if operation not in {"read", "write"}:
                operation = "read"
            entity_fields: list[str] = []
            for raw_field in raw_contract.get("entity_fields") or []:
                field_name = re.sub(r"[^a-z0-9_]+", "", str(raw_field or "").strip().casefold())
                if field_name and field_name not in entity_fields:
                    entity_fields.append(field_name)
                if len(entity_fields) >= 12:
                    break
            if purpose:
                safe_contracts.append(
                    {
                        "intent": contract_intent,
                        "purpose": purpose,
                        "operation": operation,
                        "entity_fields": entity_fields,
                    }
                )
        return safe_contracts
