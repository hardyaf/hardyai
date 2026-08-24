from app.core.micro_jarvis import MicroJarvis
from app.core.types import Intent, SessionOwner


def test_micro_routes_recent_email_request_to_main_owned_email_skill():
    decision = MicroJarvis().interpret("what important email came in today?")

    assert decision.intent == Intent.EMAIL_LIST_RECENT
    assert decision.recommended_owner == SessionOwner.MAIN


def test_email_heuristic_overrides_high_confidence_generic_model_classification():
    class GenericBackend:
        def classify(self, text, context=None):
            return {
                "intent": "conversation.general",
                "confidence": 0.92,
                "entities": {},
                "reasoning": "generic_model",
            }

    decision = MicroJarvis(backend=GenericBackend()).interpret(
        "hi, can you summarize the emails recieved today"
    )

    assert decision.intent == Intent.EMAIL_LIST_RECENT
    assert decision.recommended_owner == SessionOwner.MAIN


def test_email_collection_heuristic_overrides_single_summary_model_classification():
    class SingleSummaryBackend:
        def classify(self, text, context=None):
            return {
                "intent": "email.summarize",
                "confidence": 0.93,
                "entities": {},
                "reasoning": "model_selected_focused_summary",
            }

    decision = MicroJarvis(backend=SingleSummaryBackend()).interpret(
        "hi, can you summarize the emails recieved today"
    )

    assert decision.intent == Intent.EMAIL_LIST_RECENT
    assert decision.entities["query"] == "hi, can you summarize the emails recieved today"


def test_email_collection_followup_lists_all_scoped_results():
    decision = MicroJarvis().interpret(
        "just anything from my addresses from today",
        context={
            "entity_hints": [
                {"domain": "email", "entity_type": "message", "entity_id": "opaque"}
            ]
        },
    )

    assert decision.intent == Intent.EMAIL_LIST_RECENT
    assert "reference" not in decision.entities


def test_explicit_email_spam_instruction_routes_to_manual_write_intent():
    decision = MicroJarvis().interpret("E2 is spam")

    assert decision.intent == Intent.EMAIL_MARK_SPAM
    assert decision.entities["reference"] == "E2"
    assert decision.entities["query"] == "E2 is spam"
    assert "manual_provider_write" in decision.ambiguity_flags


def test_explicit_email_spam_instruction_preserves_multiple_named_references():
    decision = MicroJarvis().interpret("mark E1 and E3 as spam")

    assert decision.intent == Intent.EMAIL_MARK_SPAM
    assert decision.entities["references"] == ["E1", "E3"]
    assert "reference" not in decision.entities


def test_negative_spam_statement_never_routes_to_spam_write():
    decision = MicroJarvis().interpret("E2 is not spam")

    assert decision.intent != Intent.EMAIL_MARK_SPAM


def test_durable_email_anchor_routes_new_ones_followup_after_session_rotation():
    decision = MicroJarvis().interpret(
        "Can you summarize new ones now",
        context={
            "working_context": {
                "active_skill_context": {
                    "skill_id": "skill.email.agent",
                    "context_kind": "email_reference_set",
                }
            }
        },
    )

    assert decision.intent == Intent.EMAIL_LIST_RECENT
    assert decision.entities["query"] == "summarize new ones now"


def test_email_needs_reply_and_complete_all_are_explicit_dispositions():
    context = {
        "working_context": {
            "active_skill_context": {
                "skill_id": "skill.email.agent",
                "context_kind": "email_reference_set",
            }
        }
    }

    needs_reply = MicroJarvis().interpret("mark this as needs reply", context=context)
    complete_all = MicroJarvis().interpret(
        "mark those all as read and complete",
        context=context,
    )

    assert needs_reply.intent == Intent.EMAIL_MARK_NEEDS_REPLY
    assert needs_reply.entities["reference"] == "that"
    assert complete_all.intent == Intent.EMAIL_MARK_COMPLETE
    assert complete_all.entities["reference_scope"] == "all_current"
    assert "manual_provider_write" in complete_all.ambiguity_flags


def test_micro_routes_stable_email_reference_promotion():
    decision = MicroJarvis().interpret("put E2 on the household to-do list")

    assert decision.intent == Intent.EMAIL_PROMOTE_TO_LIST
    assert decision.entities["reference"] == "E2"
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_resolves_deictic_email_followup_from_scoped_entity_hint():
    decision = MicroJarvis().interpret(
        "tell me more about that",
        context={
            "entity_hints": [
                {
                    "domain": "email",
                    "entity_type": "message",
                    "entity_id": "opaque-message-id",
                }
            ]
        },
    )

    assert decision.intent == Intent.EMAIL_DISCUSS
    assert decision.entities["reference"] == "that"
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_prefers_calendar_over_list_for_calendar_target():
    micro = MicroJarvis()
    decision = micro.interpret("add dentist appointment to my calendar")

    assert decision.intent == Intent.CALENDAR_ADD_EVENT
    assert decision.entities["event_title"] == "dentist appointment"
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_parses_list_add_when_target_is_not_calendar():
    micro = MicroJarvis()
    decision = micro.interpret("add milk to groceries")

    assert decision.intent == Intent.LIST_ADD_ITEM
    assert decision.entities["item_text"] == "milk"
    assert decision.entities["list_name"] == "groceries"


def test_micro_parses_list_create_command():
    micro = MicroJarvis()
    decision = micro.interpret("create a grocery list")

    assert decision.intent == Intent.LIST_CREATE_LIST
    assert decision.entities["list_name"] == "grocery"
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_parses_lets_make_list_phrase():
    micro = MicroJarvis()
    decision = micro.interpret("lets make a costco list")

    assert decision.intent == Intent.LIST_CREATE_LIST
    assert decision.entities["list_name"] == "costco"
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_parses_put_items_on_it_phrase():
    micro = MicroJarvis()
    decision = micro.interpret("please put apples, tofu, jelly, and granola on it")

    assert decision.intent == Intent.LIST_ADD_ITEM
    assert decision.entities["item_text"] == "apples, tofu, jelly, and granola"
    assert decision.entities["list_name"] == "it"
    assert "deictic_list_reference" in decision.ambiguity_flags


def test_micro_routes_deictic_list_add_to_main():
    micro = MicroJarvis()
    decision = micro.interpret("add tofu to it")

    assert decision.intent == Intent.LIST_ADD_ITEM
    assert decision.entities["list_name"] == "it"
    assert "deictic_list_reference" in decision.ambiguity_flags
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_parses_list_add_with_on_target():
    micro = MicroJarvis()
    decision = micro.interpret("add pick up dog poop on it")

    assert decision.intent == Intent.LIST_ADD_ITEM
    assert decision.entities["item_text"] == "pick up dog poop"
    assert decision.entities["list_name"] == "it"
    assert "deictic_list_reference" in decision.ambiguity_flags
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_routes_compound_list_create_and_add_to_main():
    micro = MicroJarvis()
    decision = micro.interpret("jarvis lets create a grocery list and add bananas to it")

    assert decision.intent == Intent.CONVERSATIONAL
    assert "compound_list_create_add" in decision.ambiguity_flags
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_parses_numbered_owner_tagged_compound_list_request():
    text = (
        "lets make a list called ICDP party to-do. On it lets add- "
        "1) Rocket Fundraiser (Jordan), 2) location testing (Jordan), "
        "food prep (Taylor), yard layout (Taylor), Get tables from kelly (Taylor)"
    )

    decision = MicroJarvis().interpret(text)

    assert decision.intent == Intent.CONVERSATIONAL
    assert decision.entities == {
        "list_name": "ICDP party to-do",
        "items": [
            "Rocket Fundraiser (Jordan)",
            "location testing (Jordan)",
            "food prep (Taylor)",
            "yard layout (Taylor)",
            "Get tables from kelly (Taylor)",
        ],
    }
    assert "compound_list_create_add" in decision.ambiguity_flags
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_falls_back_to_heuristics_when_backend_payload_is_invalid():
    class InvalidBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "calendar.create_event",  # not an allowed intent
                "confidence": 1.0,
                "entities": {"event_title": "invalid"},
                "ambiguity_flags": [],
                "reasoning": "invalid_payload",
            }

    micro = MicroJarvis(backend=InvalidBackend())
    decision = micro.interpret("add dentist appointment to my calendar")

    assert decision.intent == Intent.CALENDAR_ADD_EVENT
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_parses_switch_command_with_trailing_politeness():
    micro = MicroJarvis()
    decision = micro.interpret("Jarvis, can you turn the office test light on please?")

    assert decision.intent == Intent.HOME_SET_SWITCH
    assert decision.entities["switch_name"] == "office test light"
    assert decision.entities["action"] == "on"


def test_micro_parses_turn_all_lights_on_as_fast_switch_scope():
    micro = MicroJarvis()
    decision = micro.interpret("Can you turn all lights on")

    assert decision.intent == Intent.HOME_SET_SWITCH
    assert decision.entities["switch_name"] == "all lights"
    assert decision.entities["action"] == "on"
    assert decision.entities["scope"] == "all"
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_overrides_unknown_backend_with_heuristic_for_lights():
    class UnknownBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "unknown",
                "confidence": 0.95,
                "entities": {},
                "ambiguity_flags": [],
                "reasoning": "model_uncertain",
            }

    micro = MicroJarvis(backend=UnknownBackend())
    decision = micro.interpret("turn all lights off")

    assert decision.intent == Intent.HOME_SET_SWITCH
    assert decision.entities["scope"] == "all"
    assert decision.entities["action"] == "off"


def test_micro_guardrail_overrides_misclassified_backend_for_explicit_list_create():
    class Backend:
        def classify(self, text: str, context=None):
            return {
                "intent": "lists.add_item",
                "confidence": 0.95,
                "entities": {"list_name": "costco"},
                "ambiguity_flags": ["deictic"],
                "reasoning": "backend_misclass",
            }

    micro = MicroJarvis(backend=Backend())
    decision = micro.interpret("lets make a costco list")

    assert decision.intent == Intent.LIST_CREATE_LIST
    assert decision.entities["list_name"] == "costco"
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_parses_list_show_command():
    micro = MicroJarvis()
    decision = micro.interpret("show me groceries")

    assert decision.intent == Intent.LIST_GET_ITEMS
    assert decision.entities["list_name"] == "groceries"
    assert decision.recommended_owner == SessionOwner.MICRO


def test_micro_parses_list_get_with_hi_jarvis_prefix():
    micro = MicroJarvis()
    decision = micro.interpret("Hi Jarvis what's on my grocery list")

    assert decision.intent == Intent.LIST_GET_ITEMS
    assert decision.entities["list_name"] == "grocery"
    assert decision.recommended_owner == SessionOwner.MICRO


def test_micro_parses_delete_list_command_and_routes_to_main():
    micro = MicroJarvis()
    decision = micro.interpret("delete the costco list")

    assert decision.intent == Intent.LIST_DELETE_LIST
    assert decision.entities["list_name"] == "costco"
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_parses_remove_item_command_and_routes_to_main():
    micro = MicroJarvis()
    decision = micro.interpret("remove apples from the costco list")

    assert decision.intent == Intent.LIST_REMOVE_ITEM
    assert decision.entities["item_text"] == "apples"
    assert decision.entities["list_name"] == "costco"
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_parses_calendar_view_with_week_window():
    micro = MicroJarvis()
    decision = micro.interpret("what's on my calendar this week")

    assert decision.intent == Intent.CALENDAR_VIEW
    assert decision.entities["window"] == "weekly"
    assert decision.entities["person_name"] is None
    assert decision.recommended_owner == SessionOwner.MICRO


def test_micro_normalizes_calendar_common_misspelling_in_view_query():
    micro = MicroJarvis()
    decision = micro.interpret("what is on my calandar for today")

    assert decision.intent == Intent.CALENDAR_VIEW
    assert decision.recommended_owner == SessionOwner.MICRO


def test_micro_parses_calendar_add_event_phrase_with_when_hint():
    micro = MicroJarvis()
    decision = micro.interpret(
        "Jarvis can you add an event on my calendar tomorrow for opioid settlement fund disbursement committee"
    )

    assert decision.intent == Intent.CALENDAR_ADD_EVENT
    assert decision.entities["event_title"] == "opioid settlement fund disbursement committee"
    assert "person_name" not in decision.entities
    assert decision.entities["when_hint"] == "tomorrow"
    assert decision.recommended_owner == SessionOwner.MAIN


def test_micro_model_only_mode_returns_unknown_without_backend_payload():
    micro = MicroJarvis(heuristic_fallback_enabled=False)
    decision = micro.interpret("turn office light on")

    assert decision.intent == Intent.UNKNOWN
    assert "model_only" in decision.ambiguity_flags
    assert decision.reasoning == "model_only_no_decision"


def test_micro_model_only_mode_failopens_when_backend_returns_no_decision():
    class InvalidBackend:
        def classify(self, text: str, context=None):
            return {
                "intent": "calendar.create_event",
                "confidence": 0.9,
                "entities": {},
                "ambiguity_flags": [],
                "reasoning": "invalid_payload",
            }

    micro = MicroJarvis(backend=InvalidBackend(), heuristic_fallback_enabled=False)
    decision = micro.interpret("turn office test light on")

    assert decision.intent == Intent.HOME_SET_SWITCH
    assert decision.entities["switch_name"] == "office test light"
    assert decision.entities["action"] == "on"
    assert decision.reasoning.startswith("heuristic_failopen_after_model_no_result:")


def test_micro_normalizes_all_lights_action_from_backend_shape():
    class Backend:
        def classify(self, text: str, context=None):
            return {
                "intent": "home.set_switch",
                "confidence": 1.0,
                "entities": {"action": "turn_off_all_lights"},
                "ambiguity_flags": [],
                "reasoning": "backend_shape",
            }

    micro = MicroJarvis(backend=Backend())
    decision = micro.interpret("turn off all the lights")

    assert decision.intent == Intent.HOME_SET_SWITCH
    assert decision.entities["action"] == "off"
    assert decision.entities["switch_name"] == "all lights"
    assert decision.entities["scope"] == "all"


def test_micro_normalizes_calendar_alias_fields_from_backend():
    class Backend:
        def classify(self, text: str, context=None):
            return {
                "intent": "calendar.add_event",
                "confidence": 0.92,
                "entities": {
                    "event_name": "lunch",
                    "start_time": "2 o'clock today",
                    "invitees": ["Jordan", "Taylor"],
                },
                "ambiguity_flags": [],
                "reasoning": "backend_aliases",
            }

    micro = MicroJarvis(backend=Backend())
    decision = micro.interpret("can you add lunch at two o'clock today to my calendar")

    assert decision.intent == Intent.CALENDAR_ADD_EVENT
    assert decision.entities["event_title"] == "lunch"
    assert decision.entities["when_hint"] == "2 o'clock today"
    assert decision.entities["invitee_names"] == ["Jordan", "Taylor"]


def test_micro_routes_high_confidence_short_list_get_to_micro():
    class Backend:
        def classify(self, text: str, context=None):
            return {
                "intent": "lists.get_items",
                "confidence": 1.0,
                "entities": {"list_name": "grocery"},
                "ambiguity_flags": ["short"],
                "reasoning": "short_query_inferred_grocery",
            }

    micro = MicroJarvis(backend=Backend())
    decision = micro.interpret("What's on my grocery list Jarvis")

    assert decision.intent == Intent.LIST_GET_ITEMS
    assert decision.entities["list_name"] == "grocery"
    assert decision.recommended_owner == SessionOwner.MICRO


def test_micro_routes_deictic_all_day_calendar_update_to_main():
    micro = MicroJarvis()

    decision = micro.interpret("please make that an all day event actually")

    assert decision.intent == Intent.CALENDAR_UPDATE_EVENT
    assert decision.entities == {"event_reference": "that", "all_day": True}
    assert "deictic_event_reference" in decision.ambiguity_flags
    assert decision.recommended_owner == SessionOwner.MAIN


def test_calendar_mutation_guardrail_overrides_conversational_backend_result():
    class Backend:
        def classify(self, text: str, context=None):
            return {
                "intent": "conversation.general",
                "confidence": 0.98,
                "entities": {},
                "ambiguity_flags": [],
                "reasoning": "model_missed_action",
            }

    decision = MicroJarvis(backend=Backend()).interpret("make that an all-day event")

    assert decision.intent == Intent.CALENDAR_UPDATE_EVENT
    assert decision.entities["all_day"] is True
    assert decision.recommended_owner == SessionOwner.MAIN
