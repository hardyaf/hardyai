from __future__ import annotations

from typing import Any

from app.tickets.repository import content_hash
from app.tickets.types import ReviewVerdict, SourceObservation, iso_utc


class SimulatedHomeSourceVerifier:
    name = "home.sqlite_simulated"
    version = "1"

    def __init__(self, *, home_service: Any) -> None:
        self._home_service = home_service

    def observe(
        self,
        *,
        resource_locator: dict[str, Any],
        expected_state: dict[str, Any],
        operation_receipt: dict[str, Any],
    ) -> SourceObservation:
        rows = self._home_service.list_switches()
        states = {
            str(item.get("name") or "").strip().lower(): str(item.get("state") or "").strip().lower()
            for item in rows
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        wanted = expected_state.get("switch_states")
        if not isinstance(wanted, dict):
            wanted = {}
        correct = bool(wanted) and all(
            states.get(str(name).strip().lower()) == str(state).strip().lower()
            for name, state in wanted.items()
        )
        revision = content_hash(rows)
        changed = revision != str(operation_receipt.get("provider_revision") or "")
        verdict = ReviewVerdict.CORRECT if correct else ReviewVerdict.INCORRECT
        limitations = ["simulated_state_only_not_physical_device_truth"]
        if bool(expected_state.get("read_snapshot")) and changed and not correct:
            verdict = ReviewVerdict.INCONCLUSIVE
            limitations.append("read_snapshot_changed_before_delayed_verification")
        return SourceObservation(
            verifier_name=self.name,
            verifier_version=self.version,
            resource_key=str(operation_receipt.get("resource_key") or ""),
            exists=True,
            normalized_state={"switch_states": states, "simulated": True},
            deterministic_verdict=verdict,
            observed_at=iso_utc(),
            provider_revision=revision,
            later_change_detected=changed,
            limitations=tuple(limitations),
        )
