from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from datetime import timedelta

from app.context.serialization import deserialize_session_context, serialize_session_context
from app.tickets.async_receipts import (
    EffectManifestReservation,
    TicketEffectManifestReservation,
    reduce_ticket_effects,
)
from app.tickets.eligibility import ticket_is_eligible
from app.tickets.repository import TicketRepository, content_hash
from app.tickets.types import TicketEntryType, TicketStatus, iso_utc, utc_now


ACTIVE_TICKET_ANNOTATION = "active_action_ticket"


@dataclass(frozen=True)
class TicketCaptureResult:
    request_id: str
    ticket: dict[str, Any] | None
    context_reference: dict[str, Any]


class ActionTicketService:
    def __init__(
        self,
        *,
        repository: TicketRepository,
        enabled: bool,
        review_delay_seconds: float,
        review_max_attempts: int,
        plane_enabled: bool = False,
        execution_watchdog_seconds: float = 300.0,
    ) -> None:
        self._repository = repository
        self._enabled = bool(enabled)
        self._review_delay_seconds = max(0.0, float(review_delay_seconds))
        self._review_max_attempts = max(1, int(review_max_attempts))
        self._plane_enabled = bool(plane_enabled)
        self._execution_watchdog_seconds = max(30.0, float(execution_watchdog_seconds))

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def repository(self) -> TicketRepository:
        return self._repository

    def replay_response(self, request_id: str) -> dict[str, Any] | None:
        """Reconstruct an already-seen request from durable effect truth only."""
        if not self._enabled:
            return None
        ticket = self._repository.get_ticket_by_request_id(str(request_id or "").strip())
        if ticket is None:
            return None
        ticket_id = str(ticket["ticket_id"])
        receipts = self._repository.list_receipts(ticket_id)
        latest = receipts[-1] if receipts else None
        intent = str(ticket.get("intent") or "")
        result = (
            dict(self.strip_internal_fields(latest.get("result") or {}))
            if isinstance(latest, dict) and isinstance(latest.get("result"), dict)
            else {}
        )
        if intent.startswith(("email.", "documents.", "private_notes.")):
            result = {}
        try:
            aggregate = reduce_ticket_effects(self._repository, ticket_id)
        except (KeyError, TypeError, ValueError):
            aggregate = {
                "status": "queued",
                "reason": "effect_reducer_conflict",
            }
        if aggregate["status"] in {"queued", "partial", "failed"}:
            result = {
                "status": aggregate["status"],
                "reason": aggregate["reason"],
                "message": "This replay reflects an incomplete or no-effect durable outcome.",
            }
        elif not result:
            result = {
                "status": (
                    aggregate["status"]
                    if aggregate["status"] != "no_manifest"
                    else str(ticket.get("status") or "processing")
                ),
                "message": "This request was already accepted; this replay reflects durable records.",
            }
        validators = {str(item.get("validator_name") or "") for item in receipts}
        if "home.sqlite_simulated" in validators:
            result["truth_scope"] = "simulated_state_only_not_physical_device_truth"
        elif intent.startswith("calendar.") and not receipts:
            result["truth_scope"] = "local_calendar_unverifiable"
        result["receipt_refs"] = [str(item["operation_id"]) for item in receipts]
        result["idempotent_replay"] = True
        return {
            "ticket": ticket,
            "result": result,
            "dialog": {"turn_complete": True, "mode": "durable_replay"},
            "assistant_text": "This replay was reconstructed from durable execution records.",
            "classification": {
                "intent": intent,
                "skill_id": ticket.get("skill_id"),
                "route": ticket.get("route"),
                "source": "durable_ticket",
            },
        }

    def effect_manifest_reservation(self) -> EffectManifestReservation:
        return TicketEffectManifestReservation(self._repository)

    def _enqueue_plane(self, ticket_id: str) -> None:
        if not self._plane_enabled:
            return
        ticket = self._repository.get_ticket(ticket_id)
        if ticket is None:
            return
        self._repository.enqueue_job(
            job_type="plane_sync",
            aggregate_id=ticket_id,
            idempotency_key=(
                f"plane-sync:{ticket_id}:{ticket.get('version')}:{ticket.get('status')}"
            ),
            payload={"ticket_id": ticket_id},
        )

    @staticmethod
    def _active_ticket(context_reference: dict[str, Any]) -> dict[str, Any] | None:
        state = deserialize_session_context(context_reference)
        value = state.context_annotations.get(ACTIVE_TICKET_ANNOTATION)
        return dict(value) if isinstance(value, dict) else None

    @staticmethod
    def _set_active_ticket(
        context_reference: dict[str, Any],
        *,
        ticket: dict[str, Any] | None,
    ) -> dict[str, Any]:
        state = deserialize_session_context(context_reference)
        if ticket is None:
            state.context_annotations.pop(ACTIVE_TICKET_ANNOTATION, None)
        else:
            state.context_annotations[ACTIVE_TICKET_ANNOTATION] = {
                "ticket_id": str(ticket.get("ticket_id") or ""),
                "origin_request_id": str(ticket.get("origin_request_id") or ""),
                "intent": str(ticket.get("intent") or ""),
                "bound_at": iso_utc(),
            }
        merged = dict(context_reference)
        merged.update(serialize_session_context(state))
        return merged

    @staticmethod
    def _title(request_text: str, intent: str) -> str:
        compact = " ".join(str(request_text or "").split())
        if compact:
            return compact[:240]
        return str(intent or "Jarvis action")[:240]

    def begin_request(
        self,
        *,
        request_id: str,
        session_id: str,
        context_reference: dict[str, Any],
        user_id: str,
        agent_id: str,
        source: str,
        intent: str,
        skill_id: str | None,
        route: str,
        request_text: str,
        classification: dict[str, Any],
        force: bool = False,
    ) -> TicketCaptureResult:
        """Durably record a known command before its domain side effect begins."""
        if not self._enabled:
            return TicketCaptureResult(request_id=request_id, ticket=None, context_reference=context_reference)
        normalized_intent = str(intent or "").strip().lower()
        if not force and normalized_intent in {
            "conversation.general",
            "unknown",
            "system.sleep",
            "system.wake",
        }:
            return TicketCaptureResult(request_id=request_id, ticket=None, context_reference=context_reference)
        if normalized_intent.startswith("email."):
            return TicketCaptureResult(request_id=request_id, ticket=None, context_reference=context_reference)
        if not ticket_is_eligible(
            intent=normalized_intent,
            route=route,
            result={},
            skill_id=skill_id,
        ):
            return TicketCaptureResult(
                request_id=request_id,
                ticket=None,
                context_reference=context_reference,
            )

        active = self._active_ticket(context_reference)
        active_ticket_id = str((active or {}).get("ticket_id") or "")
        ticket = self._repository.get_ticket(active_ticket_id) if active_ticket_id else None
        if ticket is None:
            ticket = self._repository.create_ticket(
                origin_request_id=request_id,
                session_id=session_id,
                user_id=user_id,
                agent_id=agent_id,
                source=source,
                intent=intent,
                skill_id=skill_id,
                route=route,
                title=self._title(request_text, intent),
            )
        ticket_id = str(ticket["ticket_id"])
        user_entry_type = (
            TicketEntryType.USER_REQUEST.value
            if str(ticket.get("origin_request_id") or "") == request_id
            else TicketEntryType.USER_CLARIFICATION.value
        )
        self._repository.append_entry(
            ticket_id=ticket_id,
            request_id=request_id,
            entry_type=user_entry_type,
            actor_type="user",
            actor_id=user_id,
            verbatim_text=request_text,
            structured_payload={"source": source, "session_id": session_id},
            dedupe_key=f"ticket:{ticket_id}:request:{request_id}:user",
        )
        self._repository.append_entry(
            ticket_id=ticket_id,
            request_id=request_id,
            entry_type=TicketEntryType.EXECUTION_STARTED.value,
            actor_type="jarvis",
            actor_id=agent_id,
            structured_payload={
                "intent": intent,
                "skill_id": skill_id,
                "route": route,
                "classification": classification,
            },
            dedupe_key=f"ticket:{ticket_id}:request:{request_id}:execution-started",
        )
        updated = self._repository.transition_ticket(
            ticket_id=ticket_id,
            status=TicketStatus.EXECUTING,
        )
        self._repository.enqueue_job(
            job_type="ticket_watchdog",
            aggregate_id=ticket_id,
            idempotency_key=f"ticket-watchdog:{ticket_id}:{request_id}",
            payload={"ticket_id": ticket_id, "request_id": request_id},
            available_at=iso_utc(utc_now() + timedelta(seconds=self._execution_watchdog_seconds)),
            max_attempts=1,
        )
        return TicketCaptureResult(
            request_id=request_id,
            ticket=updated or ticket,
            context_reference=self._set_active_ticket(context_reference, ticket=updated or ticket),
        )

    @staticmethod
    def _is_pending(dialog: dict[str, Any], result: dict[str, Any]) -> bool:
        if dialog.get("turn_complete") is False:
            return True
        return str(result.get("status") or "").strip().lower() in {
            "needs_clarification",
            "needs_input",
        }

    @staticmethod
    def _extract_receipts(result: dict[str, Any]) -> list[dict[str, Any]]:
        receipts: list[dict[str, Any]] = []

        def _walk(value: Any) -> None:
            if isinstance(value, dict):
                receipt = value.get("_operation_receipt")
                if isinstance(receipt, dict):
                    receipts.append(dict(receipt))
                for nested in value.values():
                    _walk(nested)
            elif isinstance(value, list):
                for nested in value:
                    _walk(nested)

        _walk(result)
        deduped: dict[str, dict[str, Any]] = {}
        for receipt in receipts:
            key = str(receipt.get("idempotency_key") or receipt.get("operation_id") or content_hash(receipt))
            deduped[key] = receipt
        return list(deduped.values())

    @staticmethod
    def strip_internal_fields(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: ActionTicketService.strip_internal_fields(item)
                for key, item in value.items()
                if not str(key).startswith("_operation_")
            }
        if isinstance(value, list):
            return [ActionTicketService.strip_internal_fields(item) for item in value]
        return value

    def capture_response(
        self,
        *,
        request_id: str,
        session_id: str,
        context_reference: dict[str, Any],
        user_id: str,
        agent_id: str,
        source: str,
        intent: str,
        skill_id: str | None,
        route: str,
        request_text: str,
        classification: dict[str, Any],
        result_with_internal: dict[str, Any],
        dialog: dict[str, Any],
        assistant_text: str,
    ) -> TicketCaptureResult:
        if not self._enabled:
            return TicketCaptureResult(request_id=request_id, ticket=None, context_reference=context_reference)

        public_result = self.strip_internal_fields(result_with_internal)
        if not isinstance(public_result, dict):
            public_result = {}
        active = self._active_ticket(context_reference)
        active_ticket_id = str((active or {}).get("ticket_id") or "").strip()
        ticket = self._repository.get_ticket(active_ticket_id) if active_ticket_id else None

        eligible = ticket_is_eligible(
            intent=intent,
            route=route,
            result=public_result,
            skill_id=skill_id,
        )
        if not eligible and ticket is None:
            return TicketCaptureResult(request_id=request_id, ticket=None, context_reference=context_reference)

        if ticket is None:
            ticket = self._repository.create_ticket(
                origin_request_id=request_id,
                session_id=session_id,
                user_id=user_id,
                agent_id=agent_id,
                source=source,
                intent=intent,
                skill_id=skill_id,
                route=route,
                title=self._title(request_text, intent),
            )

        ticket_id = str(ticket["ticket_id"])
        is_origin = str(ticket.get("origin_request_id") or "") == request_id
        user_entry_type = (
            TicketEntryType.USER_REQUEST.value if is_origin else TicketEntryType.USER_CLARIFICATION.value
        )
        self._repository.append_entry(
            ticket_id=ticket_id,
            request_id=request_id,
            entry_type=user_entry_type,
            actor_type="user",
            actor_id=user_id,
            verbatim_text=request_text,
            structured_payload={"source": source, "session_id": session_id},
            dedupe_key=f"ticket:{ticket_id}:request:{request_id}:user",
        )

        classification_type = TicketEntryType.ROUTING_DECISION.value
        self._repository.append_entry(
            ticket_id=ticket_id,
            request_id=request_id,
            entry_type=classification_type,
            actor_type="jarvis",
            actor_id=agent_id,
            structured_payload=classification,
            dedupe_key=f"ticket:{ticket_id}:request:{request_id}:classification",
        )
        pipeline = classification.get("pipeline")
        if isinstance(pipeline, dict):
            self._repository.append_entry(
                ticket_id=ticket_id,
                request_id=request_id,
                entry_type=TicketEntryType.ROUTING_DECISION.value,
                actor_type="jarvis",
                actor_id=agent_id,
                structured_payload=pipeline,
                dedupe_key=f"ticket:{ticket_id}:request:{request_id}:routing",
            )
        plan = public_result.get("plan")
        if isinstance(plan, dict):
            self._repository.append_entry(
                ticket_id=ticket_id,
                request_id=request_id,
                entry_type=TicketEntryType.MAIN_PLAN.value,
                actor_type="jarvis",
                actor_id=agent_id,
                structured_payload=plan,
                dedupe_key=f"ticket:{ticket_id}:request:{request_id}:plan",
            )

        pending = self._is_pending(dialog, public_result)
        assistant_entry_type = (
            TicketEntryType.ASSISTANT_CLARIFICATION.value
            if pending
            else TicketEntryType.ASSISTANT_RESPONSE.value
        )
        self._repository.append_entry(
            ticket_id=ticket_id,
            request_id=request_id,
            entry_type=assistant_entry_type,
            actor_type="assistant",
            actor_id=agent_id,
            verbatim_text=assistant_text,
            structured_payload={"dialog": dialog, "result": public_result},
            dedupe_key=f"ticket:{ticket_id}:request:{request_id}:assistant",
        )

        if pending:
            updated = self._repository.transition_ticket(
                ticket_id=ticket_id,
                status=TicketStatus.WAITING_CLARIFICATION,
            )
            self._enqueue_plane(ticket_id)
            next_context = self._set_active_ticket(context_reference, ticket=updated or ticket)
            return TicketCaptureResult(request_id=request_id, ticket=updated or ticket, context_reference=next_context)

        status = str(public_result.get("status") or "").strip().lower()
        if status == "cancelled":
            updated = self._repository.transition_ticket(
                ticket_id=ticket_id,
                status=TicketStatus.CANCELLED,
                completed_at=iso_utc(),
                terminal_reason="request_cancelled",
            )
            self._enqueue_plane(ticket_id)
            return TicketCaptureResult(
                request_id=request_id,
                ticket=updated or ticket,
                context_reference=self._set_active_ticket(context_reference, ticket=None),
            )

        receipts = self._extract_receipts(result_with_internal)
        self._repository.append_entry(
            ticket_id=ticket_id,
            request_id=request_id,
            entry_type=TicketEntryType.EXECUTION_COMPLETED.value,
            actor_type="jarvis",
            actor_id=agent_id,
            structured_payload={"status": status, "result": public_result},
            dedupe_key=f"ticket:{ticket_id}:request:{request_id}:execution-completed",
        )
        recorded_receipts = self._repository.list_receipts(ticket_id)
        known_operation_ids = {
            str(item.get("operation_id") or "") for item in recorded_receipts
        }
        for receipt in receipts:
            required = {
                "operation_id",
                "idempotency_key",
                "capability",
                "action",
                "resource_key",
                "status",
                "expected_effect",
                "validator_name",
                "validator_version",
                "resource_locator",
            }
            if not required.issubset(receipt):
                continue
            completion = self._repository.record_operation_completion_atomic(
                ticket_id=ticket_id,
                request_id=request_id,
                receipt=receipt,
            )
            recorded = dict(completion["receipt"])
            if str(recorded.get("operation_id") or "") not in known_operation_ids:
                recorded_receipts.append(recorded)
                known_operation_ids.add(str(recorded.get("operation_id") or ""))

        try:
            effect_aggregate = reduce_ticket_effects(self._repository, ticket_id)
        except (KeyError, TypeError, ValueError):
            effect_aggregate = {
                "status": "queued",
                "reason": "effect_reducer_conflict",
            }
        if effect_aggregate["status"] in {"queued", "partial", "failed"}:
            aggregate_status = str(effect_aggregate["status"])
            updated = self._repository.transition_ticket(
                ticket_id=ticket_id,
                status=(
                    TicketStatus.ESCALATED
                    if aggregate_status == "failed"
                    else TicketStatus.RECONCILIATION_REQUIRED
                ),
                completed_at=iso_utc(),
                terminal_reason=str(effect_aggregate["reason"]),
            )
        elif recorded_receipts:
            resource_keys = sorted({str(item["resource_key"]) for item in recorded_receipts})
            source_revision = content_hash(
                [
                    {
                        "operation_id": item["operation_id"],
                        "provider_revision": item.get("provider_revision"),
                        "expected_effect": item.get("expected_effect"),
                    }
                    for item in recorded_receipts
                ]
            )
            expected_hash = content_hash([item.get("expected_effect") for item in recorded_receipts])
            self._repository.transition_ticket(
                ticket_id=ticket_id,
                status=TicketStatus.EXECUTING,
                resource_key=resource_keys[0] if len(resource_keys) == 1 else f"multi:{ticket_id}",
                source_action_revision=source_revision,
                expected_effect_hash=expected_hash,
            )
            self._repository.schedule_verification(
                ticket_id=ticket_id,
                source_action_revision=source_revision,
                delay_seconds=self._review_delay_seconds,
                max_attempts=self._review_max_attempts,
            )
            updated = self._repository.get_ticket(ticket_id)
        else:
            updated = self._repository.transition_ticket(
                ticket_id=ticket_id,
                status=TicketStatus.UNVERIFIABLE,
                completed_at=iso_utc(),
                terminal_reason="no_source_verifier_receipt",
            )

        self._enqueue_plane(ticket_id)

        return TicketCaptureResult(
            request_id=request_id,
            ticket=updated or ticket,
            context_reference=self._set_active_ticket(context_reference, ticket=None),
        )
