from __future__ import annotations

from typing import Any

from app.tickets.types import ReviewVerdict, SourceObservation, iso_utc


class GoogleCalendarSourceVerifier:
    name = "calendar.google"
    version = "1"

    def __init__(self, *, calendar_service: Any) -> None:
        self._calendar_service = calendar_service

    def observe(
        self,
        *,
        resource_locator: dict[str, Any],
        expected_state: dict[str, Any],
        operation_receipt: dict[str, Any],
    ) -> SourceObservation:
        resource_key = str(operation_receipt.get("resource_key") or "")
        result = self._calendar_service.source_event_by_id(
            calendar_id=str(resource_locator.get("calendar_id") or ""),
            event_id=str(resource_locator.get("event_id") or ""),
        )
        if result.get("status") != "ok":
            error_code = str(result.get("error_code") or "provider_unavailable")
            not_found = error_code == "not_found"
            return SourceObservation(
                verifier_name=self.name,
                verifier_version=self.version,
                resource_key=resource_key,
                exists=False if not_found else None,
                normalized_state={},
                deterministic_verdict=(
                    ReviewVerdict.CORRECT
                    if not_found and expected_state.get("exists") is False
                    else (ReviewVerdict.INCORRECT if not_found else ReviewVerdict.INCONCLUSIVE)
                ),
                observed_at=iso_utc(),
                limitations=(error_code,),
                error_code=error_code,
            )

        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        correct = bool(expected_state.get("exists", True))
        for field in ("title", "start_at", "end_at"):
            expected = str(expected_state.get(field) or "").strip()
            if expected and str(event.get(field) or "").strip() != expected:
                correct = False
        expected_attendees = sorted(str(item).casefold() for item in expected_state.get("attendee_emails") or [])
        actual_attendees = sorted(str(item).casefold() for item in event.get("attendee_emails") or [])
        if expected_attendees and expected_attendees != actual_attendees:
            correct = False
        provider_revision = str(event.get("google_event_etag") or "") or None
        original_revision = str(operation_receipt.get("provider_revision") or "") or None
        later_change = bool(provider_revision and original_revision and provider_revision != original_revision)
        return SourceObservation(
            verifier_name=self.name,
            verifier_version=self.version,
            resource_key=resource_key,
            exists=True,
            normalized_state=dict(event),
            deterministic_verdict=ReviewVerdict.CORRECT if correct else ReviewVerdict.INCORRECT,
            observed_at=iso_utc(),
            provider_revision=provider_revision,
            later_change_detected=later_change,
            limitations=("provider_revision_changed_since_execution",) if later_change else (),
        )
