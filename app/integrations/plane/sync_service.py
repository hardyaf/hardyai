from __future__ import annotations

from html import escape
from typing import Any

from app.integrations.plane.protocols import WorkBoardClient
from app.tickets.repository import TicketRepository
from app.tickets.types import TicketEntryType


_STATE_ALIASES: dict[str, tuple[str, ...]] = {
    "captured": ("backlog", "triage"),
    "waiting_clarification": ("backlog", "triage"),
    "executing": ("started", "in progress"),
    "verification_pending": ("verification pending", "in review", "started"),
    "verifying": ("verifying", "in review", "started"),
    "verified": ("verified", "done", "completed"),
    "superseded": ("superseded", "cancelled", "canceled"),
    "remediation_queued": ("remediation queued", "in progress", "started"),
    "unverifiable": ("unverifiable", "in review", "backlog"),
    "reconciliation_required": ("reconciliation required", "in review", "backlog"),
    "escalated": ("escalated", "in review", "backlog"),
    "cancelled": ("cancelled", "canceled"),
}


class PlaneSyncService:
    def __init__(
        self,
        *,
        repository: TicketRepository,
        client: WorkBoardClient,
        sync_raw_transcript: bool = False,
    ) -> None:
        self._repository = repository
        self._client = client
        self._sync_raw_transcript = bool(sync_raw_transcript)

    def _resolve_state_id(self, ticket_status: str) -> str | None:
        states = self._client.list_states()
        by_name = {
            str(item.get("name") or "").strip().casefold(): str(item.get("id") or "")
            for item in states
            if item.get("name") and item.get("id")
        }
        for alias in _STATE_ALIASES.get(ticket_status, (ticket_status.replace("_", " "),)):
            if by_name.get(alias.casefold()):
                return by_name[alias.casefold()]
        return None

    def _description(self, ticket: dict[str, Any]) -> str:
        parts = [
            f"<p><strong>Jarvis ticket:</strong> {escape(str(ticket['ticket_id']))}</p>",
            f"<p><strong>Status:</strong> {escape(str(ticket.get('status') or ''))}</p>",
            f"<p><strong>Capability:</strong> {escape(str(ticket.get('intent') or 'unknown'))}</p>",
            f"<p><strong>Source:</strong> {escape(str(ticket.get('source') or 'unknown'))}</p>",
        ]
        if ticket.get("terminal_reason"):
            parts.append(
                f"<p><strong>Outcome:</strong> {escape(str(ticket['terminal_reason']))}</p>"
            )
        if self._sync_raw_transcript:
            entries = self._repository.list_entries(str(ticket["ticket_id"]))
            transcript = [
                str(item.get("verbatim_text") or "")
                for item in entries
                if item.get("verbatim_text")
            ]
            if transcript:
                joined = "<br>".join(escape(item) for item in transcript)
                parts.append(f"<details><summary>Transcript</summary><p>{joined}</p></details>")
        return "".join(parts)

    def sync_ticket(self, ticket_id: str) -> dict[str, Any]:
        ticket = self._repository.get_ticket(ticket_id)
        if ticket is None:
            return {"status": "ignored", "reason": "ticket_missing"}

        self._repository.update_plane_mapping(
            ticket_id=ticket_id,
            plane_work_item_id=None,
            sync_status="syncing",
        )
        plane_id = str(ticket.get("plane_work_item_id") or "").strip()
        if not plane_id:
            found = self._client.find_work_item_by_external_id(ticket_id)
            if found:
                plane_id = str(found.get("id") or "")

        state_id = self._resolve_state_id(str(ticket.get("status") or ""))
        payload: dict[str, Any] = {
            "name": str(ticket.get("title") or "Jarvis action")[:255],
            "description_html": self._description(ticket),
            "description_stripped": (
                f"Jarvis ticket {ticket_id}; status {ticket.get('status')}; "
                f"capability {ticket.get('intent') or 'unknown'}"
            ),
            "priority": "high"
            if str(ticket.get("status") or "") in {"escalated", "reconciliation_required"}
            else "none",
        }
        if state_id:
            payload["state"] = state_id

        if plane_id:
            item = self._client.update_work_item(plane_id, payload)
        else:
            payload.update({"external_source": "jarvis", "external_id": ticket_id})
            parent_id = str(ticket.get("parent_ticket_id") or "")
            parent = self._repository.get_ticket(parent_id) if parent_id else None
            if parent and parent.get("plane_work_item_id"):
                payload["parent"] = str(parent["plane_work_item_id"])
            item = self._client.create_work_item(payload)
            plane_id = str(item.get("id") or "")
            if not plane_id:
                # Resolve an ambiguous create response before allowing a retry.
                found = self._client.find_work_item_by_external_id(ticket_id)
                plane_id = str((found or {}).get("id") or "")
            if not plane_id:
                raise RuntimeError("Plane create returned no work-item ID")

        updated = self._repository.update_plane_mapping(
            ticket_id=ticket_id,
            plane_work_item_id=plane_id,
            sync_status="synced",
        )
        self._repository.append_entry(
            ticket_id=ticket_id,
            request_id=f"plane:{ticket_id}:{ticket.get('version')}",
            entry_type=TicketEntryType.PLANE_SYNC_RESULT.value,
            actor_type="integration",
            actor_id="plane",
            structured_payload={
                "status": "synced",
                "plane_work_item_id": plane_id,
                "ticket_status": ticket.get("status"),
            },
            dedupe_key=f"ticket:{ticket_id}:plane:{ticket.get('version')}:{ticket.get('status')}",
        )
        return {"status": "synced", "ticket": updated, "work_item": item}

