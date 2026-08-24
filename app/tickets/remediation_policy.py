from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.tickets.types import ReviewRepair


@dataclass(frozen=True)
class RemediationPolicyDecision:
    allowed: bool
    reason: str
    repair: ReviewRepair | None = None


class RemediationPolicy:
    _AUTO_ALLOWED = {
        "lists.create_list",
        "lists.add_item",
    }

    def __init__(self, *, max_generation: int) -> None:
        self._max_generation = max(0, int(max_generation))

    def evaluate(
        self,
        *,
        ticket: dict[str, Any],
        proposed: ReviewRepair | None,
        expectations: list[dict[str, Any]],
    ) -> RemediationPolicyDecision:
        generation = int(ticket.get("remediation_generation") or 0)
        if generation >= self._max_generation:
            return RemediationPolicyDecision(False, "remediation_generation_cap_reached")

        repair = proposed or self._derive_safe_repair(ticket=ticket, expectations=expectations)
        if repair is None:
            return RemediationPolicyDecision(False, "no_typed_repair_available")
        if repair.capability not in self._AUTO_ALLOWED:
            return RemediationPolicyDecision(False, "capability_not_auto_allowed", repair)
        if repair.capability != str(ticket.get("intent") or ""):
            return RemediationPolicyDecision(False, "repair_capability_differs_from_original", repair)

        if repair.capability == "lists.create_list":
            if not str(repair.entities.get("list_name") or "").strip():
                return RemediationPolicyDecision(False, "missing_list_name", repair)
        elif repair.capability == "lists.add_item":
            if not str(repair.entities.get("list_name") or "").strip():
                return RemediationPolicyDecision(False, "missing_list_name", repair)
            if not str(repair.entities.get("item_text") or "").strip():
                return RemediationPolicyDecision(False, "missing_item_text", repair)
        return RemediationPolicyDecision(True, "allowlisted_reversible_repair", repair)

    @staticmethod
    def _derive_safe_repair(
        *,
        ticket: dict[str, Any],
        expectations: list[dict[str, Any]],
    ) -> ReviewRepair | None:
        intent = str(ticket.get("intent") or "")
        if len(expectations) != 1:
            return None
        expectation = expectations[0]
        locator = expectation.get("resource_locator")
        expected = expectation.get("expected_state")
        if not isinstance(locator, dict) or not isinstance(expected, dict):
            return None
        list_name = str(locator.get("list_name") or "").strip()
        if intent == "lists.create_list" and expected.get("exists") is True and list_name:
            return ReviewRepair(
                capability=intent,
                entities={"list_name": list_name},
                reason="Trusted source evidence shows the requested list is missing.",
            )
        if intent == "lists.add_item" and list_name:
            present = expected.get("items_present")
            if isinstance(present, list):
                items = [str(item).strip() for item in present if str(item).strip()]
                if items:
                    return ReviewRepair(
                        capability=intent,
                        entities={"list_name": list_name, "item_text": ", ".join(items)},
                        reason="Trusted source evidence shows requested list items are missing.",
                    )
        return None
