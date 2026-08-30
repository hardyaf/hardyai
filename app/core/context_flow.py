from __future__ import annotations

from typing import Any

from app.core.micro_jarvis import MicroDecision
from app.core.session_store import SessionRecord


class ContextFlow:
    """Own working-context assembly, handoff hints, and operator context exports."""

    def __init__(self, router_ports: Any) -> None:
        self._router = router_ports

    def _bind_request_decision(
        self,
        *,
        session: SessionRecord,
        decision: MicroDecision,
        request_context: dict[str, Any],
        working_context: dict[str, Any],
        text: str,
    ) -> MicroDecision:
        """Apply trusted transport context after either Micro or Main chose an intent."""

        router = self._router
        for contract in router._skill_context_contracts:
            bind_hook = getattr(contract, "bind_request_decision", None)
            if not callable(bind_hook):
                continue
            try:
                bound = bind_hook(
                    decision=decision,
                    request_context=request_context,
                    working_context=working_context,
                    text=text,
                )
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                router._event_log.record(
                    event_type="context.contract.bind_request_decision.failed",
                    session_id=session.session_id,
                    payload={
                        "contract_id": getattr(contract, "contract_id", "unknown"),
                        "error": type(exc).__name__,
                    },
                )
                continue
            if isinstance(bound, MicroDecision):
                decision = bound
        return decision

    def _resolve_followup_entities(self, session: SessionRecord, decision: MicroDecision) -> MicroDecision:
        router = self._router
        registry = router._entity_registry_manager.get_registry(session=session)
        intent_value = decision.intent.value
        for contract in router._skill_context_contracts:
            if not contract.supports_intent(intent=intent_value):
                continue
            try:
                decision = contract.resolve_followup(
                    decision=decision,
                    registry=registry,
                    resolver=router._reference_resolver,
                    required_fields_for_intent=router._required_fields_for_intent,
                    has_blocking_ambiguity=router._has_blocking_ambiguity,
                )
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                router._event_log.record(
                    event_type="context.contract.resolve_followup.failed",
                    session_id=session.session_id,
                    payload={
                        "contract_id": str(getattr(contract, "contract_id", "") or ""),
                        "intent": intent_value,
                        "error": str(exc),
                    },
                )
        return decision

    def _legacy_main_handoff_context(
        self,
        *,
        session: SessionRecord,
        intent: str | None = None,
        route: str | None = None,
    ) -> dict[str, Any]:
        router = self._router
        registry = router._entity_registry_manager.get_registry(session=session)
        context_reference = dict(session.context_reference)
        runtime_context = router._runtime_main_handoff_context()
        hints: dict[str, Any] = {}
        for contract in router._skill_context_contracts:
            try:
                contract_hints = contract.legacy_main_handoff_hints(
                    registry=registry,
                    context_reference=context_reference,
                    runtime_context=runtime_context,
                    intent=intent,
                    route=route,
                )
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                router._event_log.record(
                    event_type="context.contract.legacy_main_handoff_hints.failed",
                    session_id=session.session_id,
                    payload={
                        "contract_id": str(getattr(contract, "contract_id", "") or ""),
                        "error": str(exc),
                    },
                )
                continue
            if not isinstance(contract_hints, dict):
                continue
            for key, value in contract_hints.items():
                field_name = str(key).strip()
                if not field_name:
                    continue
                if value is None:
                    continue
                hints[field_name] = value
        return hints

    def _runtime_main_handoff_context(self) -> dict[str, Any]:
        router = self._router
        return {
            "available_switches": router._home_service.list_switches(),
        }

    def _build_working_context_packet(
        self,
        *,
        session: SessionRecord,
        user_id: str,
        request_text: str,
        route_hint: str,
        intent_hint: str | None,
    ):
        router = self._router
        context_reference = session.context_reference
        session_summary = context_reference.get("session_summary")
        pending_interaction = context_reference.get("pending_interaction")
        channel_runtime = context_reference.get("channel_session")
        supplemental_sections: list[str] = []
        token_session = context_reference.get("main_agent_token_session")
        if isinstance(token_session, dict):
            summaries = token_session.get("turn_summaries")
            if isinstance(summaries, list):
                supplemental_sections.extend(str(item) for item in summaries[:3] if str(item).strip())
        state_snapshot = session.context_state()
        raw_recent_turns_count = len(state_snapshot.recent_turns)
        raw_entity_count = len(state_snapshot.entity_registry.entities)

        budget_snapshot = router._main_agent_context_budget.snapshot(
            goal_text=request_text,
            context={
                "route_hint": route_hint,
                "intent_hint": intent_hint,
                "session_summary": session_summary if isinstance(session_summary, dict) else {},
                "pending_interaction": pending_interaction if isinstance(pending_interaction, dict) else {},
            },
            supplemental_sections=supplemental_sections,
        )
        raw_memory_rows = router._relevant_memory_context(
            user_id=user_id,
            session_id=session.session_id,
        )
        active_skill_context = {
            "route_hint": route_hint,
            "intent_hint": intent_hint,
        }
        active_skill_context.update(
            router._skill_memory_handoff_context(
                relevant_memory=raw_memory_rows,
                intent=intent_hint,
                request_text=request_text,
            )
        )
        runtime_channel_context = dict(channel_runtime) if isinstance(channel_runtime, dict) else {}
        available_switches = router._home_service.list_switches()
        if isinstance(available_switches, list) and available_switches:
            runtime_channel_context["available_switches"] = available_switches
        packet = router._context_builder.build_packet(
            session=session,
            relevant_memory=raw_memory_rows,
            active_skill_context=active_skill_context,
            channel_runtime=runtime_channel_context or None,
            budget_metadata=budget_snapshot.to_dict(),
        )
        router._event_log.record(
            event_type="context.packet.built",
            session_id=session.session_id,
            payload={
                "route_hint": route_hint,
                "intent_hint": intent_hint,
                "recent_turns_count": len(packet.recent_turns),
                "entity_hints_count": len(packet.entity_hints),
                "memory_count": len(packet.relevant_memory),
                "has_pending_interaction": packet.pending_interaction is not None,
                "summary_chars": len(packet.session_summary.summary_text or ""),
                "raw_recent_turns_count": raw_recent_turns_count,
                "raw_entity_registry_count": raw_entity_count,
                "raw_memory_count": len(raw_memory_rows),
                "dropped_recent_turns_count": max(0, raw_recent_turns_count - len(packet.recent_turns)),
                "dropped_entity_hints_count": max(0, raw_entity_count - len(packet.entity_hints)),
                "dropped_memory_count": max(0, len(raw_memory_rows) - len(packet.relevant_memory)),
                "budget_trimmed": bool(packet.budget_metadata.get("trimmed")),
                "budget_used_chars": int(packet.budget_metadata.get("used_chars") or 0),
                "budget_max_chars": int(packet.budget_metadata.get("max_chars") or 0),
            },
        )
        return packet

    def _skill_memory_handoff_context(
        self,
        *,
        relevant_memory: list[dict[str, Any]],
        intent: str | None,
        request_text: str,
    ) -> dict[str, Any]:
        router = self._router
        hints: dict[str, Any] = {}
        for contract in router._skill_context_contracts:
            hook = getattr(contract, "memory_handoff_hints", None)
            if not callable(hook):
                continue
            try:
                contract_hints = hook(
                    relevant_memory=relevant_memory,
                    intent=intent,
                    request_text=request_text,
                )
            except Exception:
                continue
            if not isinstance(contract_hints, dict):
                continue
            for key, value in contract_hints.items():
                field_name = str(key or "").strip()
                if field_name and value is not None:
                    hints[field_name] = value
        return hints

    def _resolve_handoff_followup_entities(
        self,
        *,
        session: SessionRecord,
        decision: MicroDecision,
        working_context: dict[str, Any],
    ) -> MicroDecision:
        router = self._router
        active_skill_context = working_context.get("active_skill_context")
        if not isinstance(active_skill_context, dict):
            return decision
        for contract in router._skill_context_contracts:
            if not contract.supports_intent(intent=decision.intent.value):
                continue
            hook = getattr(contract, "resolve_handoff_followup", None)
            if not callable(hook):
                continue
            try:
                decision = hook(
                    decision=decision,
                    active_skill_context=dict(active_skill_context),
                    resolver=router._reference_resolver,
                )
            except Exception as exc:  # pragma: no cover - defensive contract isolation
                router._event_log.record(
                    event_type="context.contract.resolve_handoff_followup.failed",
                    session_id=session.session_id,
                    payload={
                        "contract_id": str(getattr(contract, "contract_id", "") or ""),
                        "intent": decision.intent.value,
                        "error": str(exc),
                    },
                )
        return decision

    def _relevant_memory_context(
        self,
        *,
        user_id: str,
        session_id: str,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        router = self._router
        if router._memory_service is None:
            return []
        try:
            rows = router._memory_service.recent(limit=max(12, int(limit) * 4))
        except Exception:  # pragma: no cover - defensive fallback
            return []
        matched: list[dict[str, Any]] = []
        target_user_id = str(user_id or "").strip().lower()
        target_session_id = str(session_id or "").strip()
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            row_user_id = str(row.get("user_id") or "").strip().lower()
            row_session_id = str(row.get("session_id") or "").strip()
            if row_session_id != target_session_id and row_user_id != target_user_id:
                continue
            matched.append(row)
            if len(matched) >= max(1, int(limit)):
                break
        matched.reverse()
        return matched

    def export_session_context_snapshot(
        self,
        *,
        session_id: str,
        include_legacy: bool = True,
        include_working_context: bool = True,
        include_recent_events: bool = True,
        recent_events_limit: int = 120,
    ) -> dict[str, Any] | None:
        router = self._router
        session = router._session_store.get(session_id)
        if session is None:
            return None

        state = session.context_state()
        snapshot: dict[str, Any] = {
            "session": {
                "session_id": session.session_id,
                "user_id": session.user_id,
                "source": session.source,
                "owner": session.owner.value,
                "state": session.state.value,
                "last_activity_timestamp": session.last_activity_timestamp,
            },
            "context_state": state.to_dict(),
            "context_summary": {
                "recent_turns_count": len(state.recent_turns),
                "pending_interaction_active": state.pending_interaction is not None,
                "entity_registry_count": len(state.entity_registry.entities),
                "session_summary_chars": len(str(state.session_summary.summary_text or "")),
                "context_annotations_keys": sorted(str(key) for key in state.context_annotations.keys()),
            },
        }
        if include_legacy:
            snapshot["legacy_context_view"] = session.legacy_context_view()
        if include_working_context:
            packet = router._context_builder.build_packet(
                session=session,
                relevant_memory=router._relevant_memory_context(
                    user_id=session.user_id,
                    session_id=session.session_id,
                ),
                active_skill_context={
                    "route_hint": "debug_snapshot_export",
                    "intent_hint": None,
                },
                channel_runtime=state.channel_runtime,
                budget_metadata={"source": "debug_snapshot_export"},
            )
            packet_dict = packet.to_dict()
            snapshot["working_context_preview"] = {
                "counts": {
                    "recent_turns": len(packet.recent_turns),
                    "entity_hints": len(packet.entity_hints),
                    "relevant_memory": len(packet.relevant_memory),
                },
                "pending_interaction": packet_dict.get("pending_interaction"),
                "recent_turns": packet_dict.get("recent_turns"),
                "session_summary": packet_dict.get("session_summary"),
                "entity_hints": packet_dict.get("entity_hints"),
                "relevant_memory": packet_dict.get("relevant_memory"),
                "active_skill_context": packet_dict.get("active_skill_context"),
                "channel_runtime": packet_dict.get("channel_runtime"),
                "budget_metadata": packet_dict.get("budget_metadata"),
            }
        if include_recent_events:
            bounded_limit = max(20, min(int(recent_events_limit), 500))
            snapshot["context_trace_events"] = router._recent_context_trace_events(
                session_id=session.session_id,
                limit=bounded_limit,
            )

        router._event_log.record(
            event_type="context.snapshot.exported",
            session_id=session.session_id,
            payload={
                "include_legacy": bool(include_legacy),
                "include_working_context": bool(include_working_context),
                "include_recent_events": bool(include_recent_events),
                "recent_events_limit": int(recent_events_limit),
            },
        )
        return snapshot
