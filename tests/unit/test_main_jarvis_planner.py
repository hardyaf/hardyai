from app.core.main_jarvis import MainJarvis


def test_main_jarvis_builds_bulk_light_plan():
    main = MainJarvis()
    response = main.respond(
        text="Can you turn all lights on",
        context={
            "micro_intent": "home.set_switch",
            "available_switches": [
                {"name": "office test light"},
                {"name": "kitchen light"},
                {"name": "living room lamp"},
            ],
        },
    )

    assert response["status"] == "planned"
    assert response["plan"]["plan_type"] == "home.bulk_set"
    assert response["plan"]["scope"] == "all_lights"
    assert response["plan"]["action"] == "on"
    commands = response["plan"]["commands"]
    assert len(commands) == 3
    assert commands[0]["command_text"].startswith("turn ")
    assert commands[0]["command_text"].endswith(" on")


def test_main_jarvis_repair_resolves_calendar_add_semantically():
    main = MainJarvis()
    repair = main.repair_action(
        text="Schedule opioid settlement fund disbursement committee for tomorrow on my calendar"
    )

    assert repair["status"] == "not_actionable"
    assert repair["source"] == "unavailable"


def test_main_jarvis_repair_requires_when_hint_for_calendar_add():
    main = MainJarvis()
    repair = main.repair_action(text="add dentist appointment to my calendar")

    assert repair["status"] == "not_actionable"
    assert repair["source"] == "unavailable"


def test_main_jarvis_repair_returns_not_actionable_for_general_chat():
    main = MainJarvis()
    repair = main.repair_action(text="how do we design a better household automation strategy")

    assert repair["status"] == "not_actionable"


def test_main_jarvis_repair_identifies_unsupported_thermostat_intent():
    main = MainJarvis()
    repair = main.repair_action(text="set the house heat to 68 degrees")

    assert repair["status"] == "not_actionable"
    assert repair["source"] == "unavailable"


def test_main_jarvis_repair_resolves_list_create():
    main = MainJarvis()
    repair = main.repair_action(text="create a project list")

    assert repair["status"] == "not_actionable"
    assert repair["source"] == "unavailable"


def test_main_jarvis_repair_resolves_list_create_called_name():
    main = MainJarvis()
    repair = main.repair_action(text="create a list called to-do list")

    assert repair["status"] == "not_actionable"
    assert repair["source"] == "unavailable"


def test_main_jarvis_respond_builds_plan_for_list_create_and_add():
    main = MainJarvis()
    response = main.respond(
        text="Jarvis lets create a grocery list and add bananas to it",
        context={"micro_intent": "conversation.general"},
    )

    assert response["status"] == "planned"
    assert response["plan"]["plan_type"] == "list.create_and_add"
    commands = response["plan"]["commands"]
    assert len(commands) == 2
    assert "create grocery list" in commands[0]["command_text"]
    assert "add bananas to grocery" in commands[1]["command_text"]
    assert commands[0]["intent"] == "lists.create_list"
    assert commands[0]["entities"] == {"list_name": "grocery"}
    assert commands[1]["intent"] == "lists.add_item"
    assert commands[1]["entities"] == {"list_name": "grocery", "item_text": "bananas"}


def test_main_jarvis_builds_six_step_plan_for_numbered_five_item_list_request():
    response = MainJarvis().respond(
        text=(
            "lets make a list called ICDP party to-do. On it lets add- "
            "1) Rocket Fundraiser (Jordan), 2) location testing (Jordan), "
            "food prep (Taylor), yard layout (Taylor), Get tables from kelly (Taylor)"
        ),
        context={"micro_intent": "conversation.general"},
    )

    assert response["status"] == "planned"
    assert response["plan"]["plan_type"] == "list.create_and_add"
    commands = response["plan"]["commands"]
    assert len(commands) == 6
    assert commands[0]["intent"] == "lists.create_list"
    assert [command["entities"]["item_text"] for command in commands[1:]] == [
        "Rocket Fundraiser (Jordan)",
        "location testing (Jordan)",
        "food prep (Taylor)",
        "yard layout (Taylor)",
        "Get tables from kelly (Taylor)",
    ]


def test_main_jarvis_respond_builds_plan_for_lets_make_list_phrase():
    main = MainJarvis()
    response = main.respond(
        text="lets make a costco list",
        context={"micro_intent": "unknown"},
    )

    assert response["status"] == "planned"
    assert response["plan"]["plan_type"] == "list.create"
    commands = response["plan"]["commands"]
    assert len(commands) == 1
    assert "create costco list" in commands[0]["command_text"]


def test_main_jarvis_respond_builds_plan_for_put_items_on_it_with_last_list_context():
    main = MainJarvis()
    response = main.respond(
        text="please put apples, tofu, jelly, and granola on it",
        context={
            "micro_intent": "unknown",
            "entity_hints": [
                {
                    "domain": "lists",
                    "entity_type": "list",
                    "display_name": "costco",
                }
            ],
        },
    )

    assert response["status"] == "planned"
    assert response["plan"]["plan_type"] == "list.add"
    commands = response["plan"]["commands"]
    assert len(commands) == 1
    assert "add apples, tofu, jelly, and granola to costco" in commands[0]["command_text"]


def test_main_jarvis_respond_builds_plan_for_delete_list_phrase():
    main = MainJarvis()
    response = main.respond(
        text="delete the costco list",
        context={"micro_intent": "unknown"},
    )

    assert response["status"] == "planned"
    assert response["plan"]["plan_type"] == "list.delete"
    commands = response["plan"]["commands"]
    assert len(commands) == 1
    assert "delete costco list" in commands[0]["command_text"]


def test_main_jarvis_respond_builds_plan_for_remove_item_phrase():
    main = MainJarvis()
    response = main.respond(
        text="remove apples from the costco list",
        context={"micro_intent": "unknown"},
    )

    assert response["status"] == "planned"
    assert response["plan"]["plan_type"] == "list.remove_item"
    commands = response["plan"]["commands"]
    assert len(commands) == 1
    assert "remove apples from costco" in commands[0]["command_text"]


def test_main_jarvis_respond_uses_conversation_backend_for_non_task_chat():
    class ConversationBackend:
        def respond(self, text: str, context=None):
            return "Absolutely. Start by separating your week into fixed vs flexible tasks."

    main = MainJarvis(conversation_backend=ConversationBackend())
    response = main.respond(
        text="how should I organize my household priorities this week",
        context={"micro_intent": "unknown"},
    )

    assert response["status"] == "conversation"
    assert response["conversation_source"] == "model"
    assert "fixed vs flexible" in response["message"]


def test_main_jarvis_returns_typed_action_commitment_instead_of_future_tense_prose():
    class ConversationBackend:
        def decide_turn(self, text: str, context=None):
            return {
                "mode": "execute_action",
                "intent": "email.list_recent",
                "confidence": 0.96,
                "reasoning": "the user requested inbox data",
                "entities": {"query": "all unread"},
                "missing_fields": [],
                "message": "I will pull those now.",
                "question": None,
            }

        def respond(self, text: str, context=None):
            raise AssertionError("typed production backends must not fall back to promise prose")

    response = MainJarvis(conversation_backend=ConversationBackend()).respond(
        text="all unread",
        context={"micro_intent": "conversation.general"},
    )

    assert response["status"] == "main_turn_decision"
    assert response["turn_decision"]["intent"] == "email.list_recent"


def test_main_jarvis_fails_closed_when_typed_decision_is_invalid():
    class ConversationBackend:
        def decide_turn(self, text: str, context=None):
            return {"mode": "conversation", "message": "Let me fetch that for you."}

        def respond(self, text: str, context=None):
            raise AssertionError("invalid typed decisions must not become untracked promises")

    response = MainJarvis(conversation_backend=ConversationBackend()).respond(
        text="summarize my emails",
        context={"micro_intent": "conversation.general"},
    )

    assert response["status"] == "conversation"
    assert response["conversation_source"] == "decision_unavailable"
    assert "fetch" not in response["message"].lower()


def test_main_jarvis_respond_falls_back_to_heuristic_conversation_when_backend_unavailable():
    class ConversationBackend:
        def respond(self, text: str, context=None):
            return None

    main = MainJarvis(conversation_backend=ConversationBackend())
    response = main.respond(
        text="can you help me with a quick dinner recipe",
        context={"micro_intent": "conversation.general"},
    )

    assert response["status"] == "conversation"
    assert response["conversation_source"] == "unavailable"
    assert "did not respond" in response["message"].lower()


def test_main_jarvis_heuristic_conversation_handles_identity_question():
    class ConversationBackend:
        def respond(self, text: str, context=None):
            return None

    main = MainJarvis(conversation_backend=ConversationBackend())
    response = main.respond(
        text="Who are you?",
        context={"micro_intent": "conversation.general", "agent_display_name": "Jarvis"},
    )

    assert response["status"] == "conversation"
    assert response["conversation_source"] == "unavailable"
    assert "did not respond" in response["message"].lower()


def test_main_jarvis_does_not_fallback_to_heuristic_conversation_when_disabled():
    class ConversationBackend:
        def respond(self, text: str, context=None):
            return None

    main = MainJarvis(conversation_backend=ConversationBackend())
    response = main.respond(
        text="who are you",
        context={"micro_intent": "conversation.general"},
    )

    assert response["status"] == "conversation"
    assert response["conversation_source"] == "unavailable"
    assert "did not respond" in response["message"].lower()


def test_main_jarvis_prefers_valid_backend_repair_payload():
    class ValidBackend:
        def repair_action(self, text: str, context=None):
            return {
                "status": "resolved_action",
                "intent": "lists.add_item",
                "confidence": 0.93,
                "reasoning": "backend_repair",
                "entities": {
                    "list_name": "groceries",
                    "item_text": "milk",
                },
            }

    main = MainJarvis(repair_backend=ValidBackend())
    repair = main.repair_action(text="please remember milk")

    assert repair["status"] == "resolved_action"
    assert repair["intent"] == "lists.add_item"
    assert repair["entities"]["item_text"] == "milk"
    assert repair["source"] == "backend"


def test_main_jarvis_falls_back_when_backend_repair_payload_is_invalid():
    class InvalidBackend:
        def repair_action(self, text: str, context=None):
            return {
                "status": "resolved_action",
                "intent": "calendar.create_meeting",
                "confidence": 0.99,
                "reasoning": "invalid_backend_intent",
                "entities": {"event_title": "bad payload"},
            }

    main = MainJarvis(repair_backend=InvalidBackend())
    repair = main.repair_action(
        text="schedule opioid settlement fund disbursement committee for tomorrow on my calendar"
    )

    assert repair["status"] == "not_actionable"
    assert repair["source"] == "unavailable"


def test_main_jarvis_model_only_mode_failopens_to_heuristic_when_backend_is_invalid():
    class InvalidBackend:
        def repair_action(self, text: str, context=None):
            return {
                "status": "resolved_action",
                "intent": "calendar.create_meeting",
                "confidence": 0.99,
                "reasoning": "invalid_backend_intent",
                "entities": {"event_title": "bad payload"},
            }

    main = MainJarvis(repair_backend=InvalidBackend())
    repair = main.repair_action(text="schedule dentist appointment on my calendar tomorrow")

    assert repair["status"] == "not_actionable"
    assert repair["source"] == "unavailable"


def test_main_jarvis_repair_returns_not_actionable_for_calendar_sync_requests():
    main = MainJarvis()
    repair = main.repair_action(text="can you sync my calendar again")

    assert repair["status"] == "not_actionable"
    assert repair["source"] == "unavailable"


def test_main_jarvis_repair_recovers_asr_list_add_with_deictic_target():
    main = MainJarvis()
    repair = main.repair_action(
        text="can you ride burrito shells to it",
        context={
            "entity_hints": [
                {
                    "domain": "lists",
                    "entity_type": "list",
                    "display_name": "groceries",
                }
            ]
        },
    )

    assert repair["status"] == "not_actionable"
    assert repair["source"] == "unavailable"


def test_main_jarvis_repair_requests_list_name_when_deictic_add_has_no_context():
    main = MainJarvis()
    repair = main.repair_action(text="add tofu to it")

    assert repair["status"] == "not_actionable"
    assert repair["source"] == "unavailable"
