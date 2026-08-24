from __future__ import annotations

from typing import Any

from app.tickets.types import ReviewVerdict, SourceObservation, iso_utc


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


class ListsSourceVerifier:
    name = "lists.sqlite"
    version = "1"

    def __init__(self, *, lists_service: Any) -> None:
        self._lists_service = lists_service

    def observe(
        self,
        *,
        resource_locator: dict[str, Any],
        expected_state: dict[str, Any],
        operation_receipt: dict[str, Any],
    ) -> SourceObservation:
        owner_user_id = str(resource_locator.get("owner_user_id") or "all").strip() or "all"
        list_name = str(resource_locator.get("list_name") or "").strip()
        resource_key = str(operation_receipt.get("resource_key") or "").strip()
        if not list_name:
            return SourceObservation(
                verifier_name=self.name,
                verifier_version=self.version,
                resource_key=resource_key,
                exists=None,
                normalized_state={},
                deterministic_verdict=ReviewVerdict.INCONCLUSIVE,
                observed_at=iso_utc(),
                limitations=("missing_list_name_locator",),
                error_code="invalid_resource_locator",
            )

        snapshot = self._lists_service.source_snapshot(
            list_name=list_name,
            owner_user_id=owner_user_id,
        )
        actual_exists = bool(snapshot.get("exists"))
        expected_exists = expected_state.get("exists")
        correct = True
        limitations: list[str] = []

        if isinstance(expected_exists, bool) and actual_exists != expected_exists:
            correct = False

        item_entries = snapshot.get("item_entries")
        if not isinstance(item_entries, list):
            item_entries = []
        actual_names = [
            _normalized(item.get("item_name"))
            for item in item_entries
            if isinstance(item, dict) and _normalized(item.get("item_name"))
        ]

        expected_snapshot = expected_state.get("snapshot_items")
        if isinstance(expected_snapshot, list):
            wanted = [_normalized(item) for item in expected_snapshot if _normalized(item)]
            if actual_names != wanted:
                correct = False

        expected_present = expected_state.get("items_present")
        if isinstance(expected_present, list):
            for item in expected_present:
                if _normalized(item) not in actual_names:
                    correct = False

        expected_absent = expected_state.get("items_absent")
        if isinstance(expected_absent, list):
            for item in expected_absent:
                if _normalized(item) in actual_names:
                    correct = False

        checked_item_id = str(expected_state.get("checked_item_id") or "").strip()
        checked_item_name = _normalized(expected_state.get("checked_item_name"))
        if checked_item_id or checked_item_name:
            matched = None
            for entry in item_entries:
                if not isinstance(entry, dict):
                    continue
                if checked_item_id and str(entry.get("item_id") or "") == checked_item_id:
                    matched = entry
                    break
                if not checked_item_id and checked_item_name == _normalized(entry.get("item_name")):
                    matched = entry
                    break
            if matched is None or not bool(matched.get("checked")):
                correct = False

        provider_revision = str(snapshot.get("source_revision") or "").strip() or None
        execution_revision = str(operation_receipt.get("provider_revision") or "").strip() or None
        later_change = bool(provider_revision and execution_revision and provider_revision != execution_revision)
        if later_change:
            limitations.append("source_revision_changed_since_execution")

        return SourceObservation(
            verifier_name=self.name,
            verifier_version=self.version,
            resource_key=resource_key,
            exists=actual_exists,
            normalized_state=dict(snapshot),
            deterministic_verdict=(ReviewVerdict.CORRECT if correct else ReviewVerdict.INCORRECT),
            observed_at=iso_utc(),
            provider_revision=provider_revision,
            later_change_detected=later_change,
            limitations=tuple(limitations),
        )
