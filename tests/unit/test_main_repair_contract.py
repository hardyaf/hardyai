from app.core.main_repair_contract import normalize_repair_payload


def test_normalize_repair_payload_accepts_valid_resolved_action():
    payload = normalize_repair_payload(
        {
            "status": "resolved_action",
            "intent": "calendar.add_event",
            "confidence": 0.84,
            "reasoning": "repair_model",
            "entities": {"event_title": "committee meeting", "when_hint": "tomorrow"},
            "missing_fields": [],
            "source": "backend",
        }
    )
    assert payload is not None
    assert payload["status"] == "resolved_action"
    assert payload["intent"] == "calendar.add_event"
    assert payload["confidence"] == 0.84
    assert payload["entities"]["event_title"] == "committee meeting"
    assert payload["source"] == "backend"


def test_normalize_repair_payload_rejects_invalid_intent():
    payload = normalize_repair_payload(
        {
            "status": "resolved_action",
            "intent": "calendar.create_meeting",
            "confidence": 0.8,
            "reasoning": "bad_intent",
            "entities": {},
        }
    )
    assert payload is None


def test_normalize_repair_payload_accepts_main_owned_email_intent():
    payload = normalize_repair_payload(
        {
            "status": "resolved_action",
            "intent": "email.list_recent",
            "confidence": 0.91,
            "reasoning": "collection_email_summary",
            "entities": {"query": "summarize today's emails"},
            "missing_fields": [],
            "source": "backend",
        }
    )

    assert payload is not None
    assert payload["intent"] == "email.list_recent"
    assert payload["entities"]["query"] == "summarize today's emails"


def test_normalize_repair_payload_requires_missing_fields_for_clarification():
    payload = normalize_repair_payload(
        {
            "status": "needs_clarification",
            "intent": "calendar.add_event",
            "confidence": 0.6,
            "reasoning": "missing_data",
            "message": "Need title",
            "entities": {},
        }
    )
    assert payload is None


def test_normalize_repair_payload_normalizes_not_actionable_shape():
    payload = normalize_repair_payload(
        {
            "status": "not_actionable",
            "reasoning": "no_match",
            "entities": {"should": "be dropped"},
            "intent": "lists.add_item",
            "missing_fields": ["x"],
            "question": "ignored",
            "inferred_intent": "home.set_thermostat",
            "inferred_entities": {"target_temperature_f": 68},
        }
    )
    assert payload is not None
    assert payload["status"] == "not_actionable"
    assert payload["intent"] is None
    assert payload["entities"] == {}
    assert payload["missing_fields"] == []
    assert payload["question"] is None
    assert payload["inferred_intent"] == "home.set_thermostat"
    assert payload["inferred_entities"] == {"target_temperature_f": 68}


def test_normalize_repair_payload_maps_calendar_alias_fields():
    payload = normalize_repair_payload(
        {
            "status": "resolved_action",
            "intent": "calendar.add_event",
            "confidence": 0.86,
            "reasoning": "alias_fields_from_model",
            "entities": {
                "event_name": "lunch",
                "start_time": "2 o'clock today",
                "invitees": ["Jordan", "Taylor"],
            },
            "missing_fields": [],
        }
    )

    assert payload is not None
    assert payload["intent"] == "calendar.add_event"
    assert payload["entities"]["event_title"] == "lunch"
    assert payload["entities"]["when_hint"] == "2 o'clock today"
    assert payload["entities"]["invitee_names"] == ["Jordan", "Taylor"]


def test_normalize_repair_payload_maps_calendar_update_aliases_and_bool():
    payload = normalize_repair_payload(
        {
            "status": "resolved_action",
            "intent": "calendar.update_event",
            "confidence": 0.9,
            "reasoning": "calendar_followup",
            "entities": {
                "event_title": "Dinner",
                "new_time": "August 28",
                "all_day": "yes",
                "google_event_id": "event-1",
            },
            "missing_fields": [],
        }
    )

    assert payload is not None
    assert payload["entities"]["event_reference"] == "Dinner"
    assert payload["entities"]["new_when_hint"] == "August 28"
    assert payload["entities"]["all_day"] is True
    assert payload["entities"]["event_id"] == "event-1"
