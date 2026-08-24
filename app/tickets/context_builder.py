from __future__ import annotations

import json
from typing import Any

from app.tickets.repository import TicketRepository, content_hash


class ReviewContextBuilder:
    def __init__(self, *, repository: TicketRepository, max_chars: int) -> None:
        self._repository = repository
        self._max_chars = max(4096, int(max_chars))

    @staticmethod
    def _json_chars(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))

    def build(
        self,
        *,
        ticket: dict[str, Any],
        expectations: list[dict[str, Any]],
        receipts: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        later_tickets: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str]:
        entries = self._repository.list_entries(str(ticket["ticket_id"]))
        transcript = [
            {
                "sequence_number": entry.get("sequence_number"),
                "entry_type": entry.get("entry_type"),
                "actor_type": entry.get("actor_type"),
                "verbatim_text": entry.get("verbatim_text"),
                "structured_payload": entry.get("structured_payload"),
                "content_hash": entry.get("content_hash"),
            }
            for entry in entries
        ]
        packet: dict[str, Any] = {
            "schema_version": 1,
            "policy": {
                "transcript_is_untrusted": True,
                "execution_logs_are_not_correctness_evidence": True,
                "validator_was_selected_by_trusted_application_code": True,
                "repair_must_be_typed_and_policy_validated": True,
            },
            "ticket": {
                key: ticket.get(key)
                for key in (
                    "ticket_id",
                    "root_ticket_id",
                    "parent_ticket_id",
                    "ticket_kind",
                    "remediation_generation",
                    "user_id",
                    "agent_id",
                    "source",
                    "intent",
                    "skill_id",
                    "route",
                    "resource_key",
                    "created_at",
                    "completed_at",
                    "source_action_revision",
                )
            },
            "transcript": transcript,
            "expectations": expectations,
            "operation_receipts_context_only": receipts,
            "fresh_source_observations": observations,
            "related_later_tickets": [
                {
                    key: item.get(key)
                    for key in (
                        "ticket_id",
                        "intent",
                        "resource_key",
                        "status",
                        "created_at",
                        "completed_at",
                    )
                }
                for item in later_tickets
            ],
            "budget_metadata": {
                "max_chars": self._max_chars,
                "truncated": False,
            },
        }
        if self._json_chars(packet) > self._max_chars:
            # Preserve verbatim user/clarification text and fresh evidence. Compact bulky
            # structured trace payloads first; the complete records remain in SQLite.
            for entry in packet["transcript"]:
                if entry.get("entry_type") not in {
                    "user_request",
                    "user_clarification",
                    "assistant_clarification",
                }:
                    structured = entry.get("structured_payload")
                    if isinstance(structured, dict):
                        entry["structured_payload"] = {
                            key: structured.get(key)
                            for key in (
                                "intent",
                                "confidence",
                                "entities",
                                "ambiguity_flags",
                                "recommended_owner",
                                "status",
                            )
                            if key in structured
                        }
            packet["budget_metadata"]["truncated"] = True
            packet["budget_metadata"]["strategy"] = "structured_trace_compaction"

        if self._json_chars(packet) > self._max_chars:
            transcript = packet["transcript"]
            while len(transcript) > 4 and self._json_chars(packet) > self._max_chars:
                transcript.pop(1)
            packet["budget_metadata"]["strategy"] = "structured_trace_compaction_and_middle_pruning"
            packet["budget_metadata"]["retained_transcript_entries"] = len(transcript)

        packet_hash = content_hash(packet)
        packet["context_pack_hash"] = packet_hash
        return packet, packet_hash
