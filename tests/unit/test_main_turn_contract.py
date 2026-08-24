from app.core.main_turn_contract import normalize_main_turn_decision


def test_normalizes_conversation_without_action_payload():
    decision = normalize_main_turn_decision(
        {
            "mode": "conversation",
            "intent": "email.list_recent",
            "confidence": 0.91,
            "reasoning": "the user is describing a past configuration issue",
            "entities": {"query": "ignored"},
            "missing_fields": ["ignored"],
            "message": "Understood. We can try again tomorrow.",
            "question": "ignored?",
        }
    )

    assert decision is not None
    assert decision["mode"] == "conversation"
    assert decision["intent"] is None
    assert decision["entities"] == {}
    assert decision["missing_fields"] == []
    assert decision["question"] is None


def test_normalizes_bound_action_clarification():
    decision = normalize_main_turn_decision(
        {
            "mode": "clarify_action",
            "intent": "email.list_recent",
            "confidence": 0.93,
            "reasoning": "email summary requested but scope is missing",
            "entities": {},
            "missing_fields": ["query", "query"],
            "message": "I can summarize them.",
            "question": "Which messages should I include?",
        }
    )

    assert decision is not None
    assert decision["intent"] == "email.list_recent"
    assert decision["missing_fields"] == ["query"]


def test_rejects_operational_action_without_an_action_mode():
    assert (
        normalize_main_turn_decision(
            {
                "mode": "execute_action",
                "intent": "email.list_recent",
                "confidence": 0.9,
                "reasoning": "ready",
                "entities": {},
                "missing_fields": ["query"],
                "message": "I will fetch that now.",
            }
        )
        is None
    )


def test_rejects_unknown_action_intent():
    assert (
        normalize_main_turn_decision(
            {
                "mode": "execute_action",
                "intent": "email.send_reply",
                "confidence": 0.99,
                "reasoning": "unsupported",
                "entities": {},
                "missing_fields": [],
            }
        )
        is None
    )
