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


def test_generic_commitment_is_closed_and_has_no_intent_authority():
    action = normalize_main_turn_decision(
        {
            "mode": "execute_action",
            "confidence": 0.91,
            "reason_code": "plausible_action",
        },
        execution_mode="active",
    )

    assert action == {
        "mode": "execute_action",
        "confidence": 0.91,
        "reason_code": "plausible_action",
    }
    assert normalize_main_turn_decision(
        {**action, "intent": "lists.add_item"},
        execution_mode="active",
    ) is None
    assert normalize_main_turn_decision(
        {
            "mode": "clarify_action",
            "confidence": 0.8,
            "reason_code": "missing_referent",
            "question": "Which one?",
            "entities": {},
        },
        execution_mode="shadow",
    ) is None


def test_generic_commitment_conversation_and_clarification_are_exact():
    conversation = normalize_main_turn_decision(
        {
            "mode": "conversation",
            "confidence": 0.99,
            "reason_code": "informational",
            "message": "A complete answer.",
        },
        execution_mode="shadow",
    )
    clarification = normalize_main_turn_decision(
        {
            "mode": "clarify_action",
            "confidence": 0.7,
            "reason_code": "ambiguous_goal",
            "question": "What complete goal should I use?",
        },
        execution_mode="active",
    )

    assert conversation is not None and conversation["message"] == "A complete answer."
    assert clarification is not None and clarification["question"].startswith("What complete")
