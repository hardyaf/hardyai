from __future__ import annotations

import hashlib
from typing import Any

from app.config import settings
from app.skills.execution_dispatcher import SkillExecutionDispatcher
from app.skills.tool_contracts import (
    ToolCallEnvelope,
    ToolContractError,
    ToolDescriptor,
    canonical_json,
)


class AuthorizedSkillExecutor:
    """Resolve and execute only registry-authorized skill records."""

    def __init__(
        self,
        *,
        skill_registry: Any | None,
        dispatcher: SkillExecutionDispatcher,
        execution_mode: str | None = None,
        enabled_domains: tuple[str, ...] | None = None,
        enabled_operations: tuple[str, ...] | None = None,
        max_selected_skills: int | None = None,
    ) -> None:
        self._skill_registry = skill_registry
        self._dispatcher = dispatcher
        self._execution_mode = str(
            execution_mode if execution_mode is not None else settings.main_tool_execution_mode
        ).strip().casefold()
        self._enabled_domains = frozenset(
            str(item or "").strip().casefold()
            for item in (
                enabled_domains
                if enabled_domains is not None
                else settings.main_tool_enabled_domains
            )
            if str(item or "").strip()
        )
        self._enabled_operations = frozenset(
            str(item or "").strip().casefold()
            for item in (
                enabled_operations
                if enabled_operations is not None
                else settings.main_tool_enabled_operations
            )
            if str(item or "").strip()
        )
        self._max_selected_skills = int(
            max_selected_skills
            if max_selected_skills is not None
            else settings.main_tool_max_selected_skills
        )

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
            "source": source_interface,
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
                "principal_kind",
                "principal_subject",
                "session_id",
                "session_channel",
            ):
                if key in request_context:
                    context[key] = request_context[key]
            raw_dependencies = request_context.get("available_runtime_dependencies")
            if isinstance(raw_dependencies, (list, tuple)):
                context["available_runtime_dependencies"] = [
                    str(item).strip().casefold()
                    for item in raw_dependencies[:8]
                    if isinstance(item, str) and str(item).strip()
                ]
            for key in ("document_attachment_ids", "current_document_attachment_ids"):
                raw_ids = request_context.get(key)
                if not isinstance(raw_ids, list):
                    continue
                document_ids = [
                    str(item).strip()
                    for item in raw_ids[:4]
                    if isinstance(item, str) and str(item).strip()
                ]
                if document_ids:
                    context[key] = document_ids
        return context

    def resolve(self, *, intent: str, user_id: str, agent_id: str) -> dict[str, Any] | None:
        if self._skill_registry is None:
            return None
        resolve = getattr(self._skill_registry, "resolve_skill", None)
        if not callable(resolve):
            return None
        skill = resolve(intent=intent, user_id=user_id, agent_id=agent_id)
        return skill if isinstance(skill, dict) else None

    @staticmethod
    def _tool_denied(reason: str) -> dict[str, Any]:
        return {
            "status": "policy_denied",
            "message": "This tool is not currently available in this request context.",
            "denial_reason": str(reason or "tool_unavailable").strip().casefold(),
            "dispatch_mode": "typed_tools",
        }

    def _descriptor_enabled(
        self,
        descriptor: ToolDescriptor,
        *,
        request_context: dict[str, Any],
        allow_shadow: bool,
    ) -> bool:
        allowed_modes = {"active", "shadow"} if allow_shadow else {"active"}
        if self._execution_mode not in allowed_modes:
            return False
        domain = descriptor.tool_id.partition(".")[0]
        if domain not in self._enabled_domains or descriptor.tool_id not in self._enabled_operations:
            return False
        available_dependencies = request_context.get("available_runtime_dependencies")
        if descriptor.runtime_dependencies:
            if not isinstance(available_dependencies, (list, tuple, set, frozenset)):
                return False
            normalized_dependencies = {
                str(item or "").strip().casefold()
                for item in available_dependencies
                if str(item or "").strip()
            }
            if not set(descriptor.runtime_dependencies).issubset(normalized_dependencies):
                return False
        return descriptor.interactive

    def discovery_cards(
        self,
        *,
        user_id: str,
        agent_id: str,
        source_interface: str,
        request_context: dict[str, Any] | None,
        max_skills: int = 32,
    ) -> list[dict[str, Any]]:
        if self._skill_registry is None:
            return []
        discover = getattr(self._skill_registry, "discovery_cards", None)
        if not callable(discover):
            return []
        execution_context = self.build_context(
            source_interface=source_interface,
            requested_by_user_id=user_id,
            agent_id=agent_id,
            request_context=request_context,
            request_id=None,
        )

        def availability_resolver(
            skill: dict[str, Any],
            scoped_context: dict[str, Any],
        ) -> dict[str, Any]:
            return self._dispatcher.describe_capability(
                skill=skill,
                context=scoped_context,
            )

        try:
            cards = discover(
                user_id=user_id,
                agent_id=agent_id,
                request_context=execution_context,
                availability_resolver=availability_resolver,
                max_skills=max_skills,
            )
        except Exception:
            return []
        return cards if isinstance(cards, list) else []

    def effective_tools(
        self,
        selected_skill_ids: list[str] | tuple[str, ...],
        request_context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if self._skill_registry is None or self._execution_mode not in {"shadow", "active"}:
            return []
        if not isinstance(selected_skill_ids, (list, tuple)):
            return []
        selected = tuple(str(item or "").strip().casefold() for item in selected_skill_ids)
        if (
            not selected
            or len(selected) > self._max_selected_skills
            or len(selected) != len(set(selected))
            or any(not item for item in selected)
        ):
            return []
        user_id = str(
            request_context.get("requested_by_user_id") or request_context.get("user_id") or ""
        ).strip()
        agent_id = str(request_context.get("agent_id") or "").strip()
        source_interface = str(
            request_context.get("source_interface") or request_context.get("source") or ""
        ).strip()
        if not user_id or not agent_id or not source_interface:
            return []
        execution_context = self.build_context(
            source_interface=source_interface,
            requested_by_user_id=user_id,
            agent_id=agent_id,
            request_context=request_context,
            request_id=None,
        )
        cards = self.discovery_cards(
            user_id=user_id,
            agent_id=agent_id,
            source_interface=source_interface,
            request_context=request_context,
            max_skills=64,
        )
        authorized_skill_ids = {
            str(item.get("skill_id") or "").strip().casefold()
            for item in cards
            if isinstance(item, dict)
        }
        if not set(selected).issubset(authorized_skill_ids):
            return []
        list_skills = getattr(self._skill_registry, "list_skills", None)
        descriptor_loader = getattr(self._skill_registry, "tool_descriptors_for_skill", None)
        if not callable(list_skills) or not callable(descriptor_loader):
            return []
        skills = {
            str(item.get("skill_id") or "").strip().casefold(): item
            for item in list_skills(active_only=True)
            if isinstance(item, dict)
        }
        projections: list[dict[str, Any]] = []
        for skill_id in selected:
            skill = skills.get(skill_id)
            if not isinstance(skill, dict):
                return []
            availability = self._dispatcher.describe_capability(
                skill=skill,
                context=execution_context,
            )
            if not isinstance(availability, dict) or not (
                availability.get("configured") is True
                and availability.get("authorized_here") is True
            ):
                return []
            note = str(availability.get("access_note") or "Available in the current request context.")
            for descriptor in descriptor_loader(skill):
                if self._descriptor_enabled(
                    descriptor,
                    request_context=execution_context,
                    allow_shadow=True,
                ):
                    projections.append(descriptor.to_model_projection(availability_note=note))
        return projections

    @staticmethod
    def _authorization_snapshot_ref(
        *,
        descriptor: ToolDescriptor,
        context: dict[str, Any],
    ) -> str:
        material = {
            "tool_id": descriptor.tool_id,
            "contract_version": descriptor.contract_version,
            "user_id": str(context.get("requested_by_user_id") or ""),
            "agent_id": str(context.get("agent_id") or ""),
            "source_interface": str(context.get("source_interface") or ""),
            "channel_scope": str(
                context.get("discord_channel_id")
                or context.get("session_channel")
                or context.get("source_interface")
                or ""
            ),
        }
        return "authz_v1_" + hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()

    def execute_tool(
        self,
        *,
        tool_id: str,
        contract_version: int,
        arguments: dict[str, Any],
        source_interface: str,
        requested_by_user_id: str,
        agent_id: str,
        request_context: dict[str, Any] | None,
        request_id: str,
        call_ordinal: int,
    ) -> dict[str, Any]:
        if self._skill_registry is None or self._execution_mode != "active":
            return self._tool_denied("typed_execution_inactive")
        resolve_tool = getattr(self._skill_registry, "resolve_tool", None)
        if not callable(resolve_tool):
            return self._tool_denied("tool_registry_unavailable")
        context = self.build_context(
            source_interface=source_interface,
            requested_by_user_id=requested_by_user_id,
            agent_id=agent_id,
            request_context=request_context,
            request_id=request_id,
        )
        try:
            resolved = resolve_tool(
                tool_id=tool_id,
                user_id=requested_by_user_id,
                agent_id=agent_id,
            )
        except ToolContractError as exc:
            return self._tool_denied(exc.code)
        if not isinstance(resolved, tuple) or len(resolved) != 2:
            return self._tool_denied("tool_unknown_or_unauthorized")
        skill, descriptor = resolved
        if not isinstance(skill, dict) or not isinstance(descriptor, ToolDescriptor):
            return self._tool_denied("tool_descriptor_invalid")
        if descriptor.contract_version != contract_version:
            return self._tool_denied("tool_contract_stale")
        if not self._descriptor_enabled(descriptor, request_context=context, allow_shadow=False):
            return self._tool_denied("tool_rollout_disabled")
        availability = self._dispatcher.describe_capability(skill=skill, context=context)
        if not isinstance(availability, dict) or not (
            availability.get("configured") is True
            and availability.get("authorized_here") is True
        ):
            return self._tool_denied("tool_not_authorized_here")
        try:
            validated_arguments = descriptor.validate_arguments(arguments)
            canonical_arguments = self._dispatcher.canonicalize_tool_arguments(
                skill=skill,
                descriptor=descriptor,
                arguments=validated_arguments,
                context=context,
            )
            canonical_arguments = descriptor.validate_arguments(canonical_arguments)
        except (ToolContractError, TypeError, ValueError) as exc:
            code = exc.code if isinstance(exc, ToolContractError) else "tool_arguments_invalid"
            return self._tool_denied(code)

        try:
            current = resolve_tool(
                tool_id=descriptor.tool_id,
                user_id=requested_by_user_id,
                agent_id=agent_id,
            )
        except ToolContractError as exc:
            return self._tool_denied(exc.code)
        if not isinstance(current, tuple) or len(current) != 2:
            return self._tool_denied("tool_became_unavailable")
        current_skill, current_descriptor = current
        if not isinstance(current_descriptor, ToolDescriptor) or canonical_json(
            current_descriptor.to_storage_dict()
        ) != canonical_json(descriptor.to_storage_dict()):
            return self._tool_denied("tool_contract_stale")
        current_availability = self._dispatcher.describe_capability(
            skill=current_skill,
            context=context,
        )
        if not isinstance(current_availability, dict) or not (
            current_availability.get("configured") is True
            and current_availability.get("authorized_here") is True
        ):
            return self._tool_denied("tool_authorization_changed")

        session_id = str(context.get("session_id") or "").strip()
        if not session_id:
            return self._tool_denied("session_binding_missing")
        channel_scope = str(
            context.get("discord_channel_id")
            or context.get("session_channel")
            or source_interface
        ).strip()
        try:
            envelope = ToolCallEnvelope.create(
                root_request_id=request_id,
                call_ordinal=call_ordinal,
                session_id=session_id,
                principal_kind=str(context.get("principal_kind") or "user"),
                principal_subject=str(
                    context.get("principal_subject") or requested_by_user_id
                ),
                user_id=requested_by_user_id,
                agent_id=agent_id,
                source_interface=source_interface,
                channel_scope=channel_scope,
                skill_id=str(current_skill.get("skill_id") or ""),
                descriptor=current_descriptor,
                authorization_snapshot_ref=self._authorization_snapshot_ref(
                    descriptor=current_descriptor,
                    context=context,
                ),
                validated_arguments=canonical_arguments,
            )
        except ToolContractError as exc:
            return self._tool_denied(exc.code)
        result = self._dispatcher.execute_tool(envelope)
        if not isinstance(result, dict):
            return self._tool_denied("tool_handler_unavailable")
        return result

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
