from app.core.micro_backend import parse_backend_payload


def test_parse_backend_payload_accepts_valid_shape():
    payload = parse_backend_payload(
        {
            "intent": "calendar.add_event",
            "confidence": 0.91,
            "entities": {"event_title": "dentist appointment"},
            "ambiguity_flags": [],
            "reasoning": "model_backend",
        }
    )
    assert payload is not None
    assert payload["intent"] == "calendar.add_event"
    assert payload["confidence"] == 0.91


def test_parse_backend_payload_rejects_invalid_intent():
    payload = parse_backend_payload(
        {
            "intent": "calendar.create_meeting",
            "confidence": 0.91,
            "entities": {},
            "ambiguity_flags": [],
            "reasoning": "bad",
        }
    )
    assert payload is None

