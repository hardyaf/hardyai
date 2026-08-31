import pytest

from app.context.reference_resolver import ReferenceResolver
from app.context.types import EntityRegistry, TrackedEntity
from app.core.micro_jarvis import MicroDecision
from app.core.types import Intent, SessionOwner
from app.skills.context_contracts import ToolArgumentCanonicalizer, default_skill_context_contracts


def test_tool_argument_canonicalizer_protocol_is_domain_neutral() -> None:
    class IdentityCanonicalizer:
        def canonicalize_tool_arguments(
            self,
            *,
            tool_id: str,
            validated_arguments: dict,
            request_context: dict,
        ) -> dict:
            del tool_id, request_context
            return dict(validated_arguments)

    canonicalizer: ToolArgumentCanonicalizer = IdentityCanonicalizer()
    assert canonicalizer.canonicalize_tool_arguments(
        tool_id="fixture.read",
        validated_arguments={"value": "one"},
        request_context={},
    ) == {"value": "one"}


def test_default_skill_context_contracts_cover_core_domains():
    contracts = default_skill_context_contracts()
    contract_ids = {str(getattr(contract, "contract_id", "")).strip().lower() for contract in contracts}
    assert {"lists", "lights", "calendar", "conversation", "email"} <= contract_ids


def test_documents_contract_binds_current_discord_attachment_to_read_action():
    contract = next(
        item
        for item in default_skill_context_contracts(documents_enabled=True)
        if getattr(item, "contract_id", "") == "documents"
    )
    context = {
        "principal_kind": "discord_adapter",
        "discord_channel_id": "200",
        "document_attachment_ids": ["doc-1"],
        "current_document_attachment_ids": ["doc-1"],
    }
    decision = MicroDecision(
        intent=Intent.UNKNOWN,
        confidence=0.0,
        entities={},
        ambiguity_flags=["micro_bypassed_unprefixed_discord"],
        recommended_owner=SessionOwner.MAIN,
    )

    bound = contract.bind_request_decision(
        decision=decision,
        request_context=context,
        working_context={},
        text="What does this say?",
    )

    assert bound.intent == Intent.DOCUMENTS_GET
    assert bound.entities == {"document_id": "doc-1"}
    assert bound.recommended_owner == SessionOwner.MAIN
    assert contract.request_interrupts_pending(
        request_context=context,
        text="What does this say?",
        pending_intent="conversation.general",
    )


def test_documents_contract_binds_semantic_followup_but_not_unrelated_recent_turn():
    contract = next(
        item
        for item in default_skill_context_contracts(documents_enabled=True)
        if getattr(item, "contract_id", "") == "documents"
    )
    context = {
        "principal_kind": "discord_adapter",
        "discord_channel_id": "200",
        "document_attachment_ids": ["doc-1"],
    }

    def decision():
        return MicroDecision(
            intent=Intent.UNKNOWN,
            confidence=0.0,
            entities={},
            ambiguity_flags=[],
            recommended_owner=SessionOwner.MAIN,
        )

    followup = contract.bind_request_decision(
        decision=decision(),
        request_context=context,
        working_context={},
        text="You tell me what the text in that image says",
    )
    unrelated = contract.bind_request_decision(
        decision=decision(),
        request_context=context,
        working_context={},
        text="How is the weather tomorrow?",
    )

    assert followup.intent == Intent.DOCUMENTS_GET
    assert followup.entities["document_id"] == "doc-1"
    assert unrelated.intent == Intent.UNKNOWN


def test_documents_contract_normalizes_correction_fields_and_requirements():
    contract = next(
        item
        for item in default_skill_context_contracts(documents_enabled=True)
        if getattr(item, "contract_id", "") == "documents"
    )

    entities = contract.normalize_entities(
        intent="documents.correct_field",
        entities={"document": "doc-1", "field": "company", "new_value": "Field Works LLC"},
    )

    assert entities["document_id"] == "doc-1"
    assert entities["field_name"] == "organization"
    assert entities["corrected_value"] == "Field Works LLC"
    assert contract.required_fields(
        intent="documents.correct_field",
        entities=entities,
        resolver=ReferenceResolver(),
    ) == []
    assert contract.required_fields(
        intent="documents.confirm_fields",
        entities={},
        resolver=ReferenceResolver(),
    ) == ["document_id"]
    assert contract.required_fields(
        intent="documents.escalate_ocr",
        entities={"document_id": "doc-1"},
        resolver=ReferenceResolver(),
    ) == []


def test_documents_contract_binds_typed_ocr_escalation_to_recent_discord_attachment():
    contract = next(
        item
        for item in default_skill_context_contracts(documents_enabled=True)
        if getattr(item, "contract_id", "") == "documents"
    )
    decision = MicroDecision(
        intent=Intent.DOCUMENTS_ESCALATE_OCR,
        confidence=0.91,
        entities={},
        ambiguity_flags=["resolved_via_main_repair"],
        recommended_owner=SessionOwner.MAIN,
        reasoning="negative_ocr_feedback",
    )

    bound = contract.bind_request_decision(
        decision=decision,
        request_context={
            "principal_kind": "discord_adapter",
            "discord_channel_id": "200",
            "document_attachment_ids": ["doc-1"],
        },
        working_context={},
        text="it wasn't right",
    )

    assert bound.entities == {"document_id": "doc-1"}
    assert "trusted_discord_attachment_binding" in bound.ambiguity_flags

    incomplete_correction = contract.bind_request_decision(
        decision=MicroDecision(
            intent=Intent.DOCUMENTS_CORRECT_FIELD,
            confidence=0.91,
            entities={},
            ambiguity_flags=["main_turn_commitment"],
            recommended_owner=SessionOwner.MAIN,
        ),
        request_context={
            "principal_kind": "discord_adapter",
            "discord_channel_id": "200",
            "document_attachment_ids": ["doc-1"],
        },
        working_context={},
        text="it wasn't right",
    )
    assert incomplete_correction.intent == Intent.DOCUMENTS_ESCALATE_OCR
    assert incomplete_correction.entities == {"document_id": "doc-1"}

    exact_correction = contract.bind_request_decision(
        decision=MicroDecision(
            intent=Intent.DOCUMENTS_CORRECT_FIELD,
            confidence=0.91,
            entities={"field_name": "organization", "corrected_value": "Field Works"},
            ambiguity_flags=["main_turn_commitment"],
            recommended_owner=SessionOwner.MAIN,
        ),
        request_context={
            "principal_kind": "discord_adapter",
            "discord_channel_id": "200",
            "document_attachment_ids": ["doc-1"],
        },
        working_context={},
        text="the company is Field Works",
    )
    assert exact_correction.intent == Intent.DOCUMENTS_CORRECT_FIELD
    assert exact_correction.entities == {
        "document_id": "doc-1",
        "field_name": "organization",
        "corrected_value": "Field Works",
    }


def test_documents_contract_projects_result_shape_as_semantic_entity_context():
    contract = next(
        item
        for item in default_skill_context_contracts(documents_enabled=True)
        if getattr(item, "contract_id", "") == "documents"
    )

    enriched = contract.enrich_working_context(
        request_context={
            "document_attachment_ids": ["doc-1"],
            "document_result_contexts": [
                {
                    "schema_version": 1,
                    "document_id": "doc-1",
                    "document_class": "business_card",
                    "processing_state": "needs_review",
                    "field_names": ["website", "email"],
                }
            ],
        },
        working_context={"entity_hints": []},
    )

    hint = enriched["entity_hints"][0]
    assert hint["display_name"] == "recent business card OCR result"
    assert "website" in hint["aliases"]
    assert "website field" in hint["aliases"]
    assert hint["resolution_hints"] == {
        "document_id": "doc-1",
        "source": "discord_document_result",
    }
    assert "incorrect.example" not in repr(enriched)


def test_email_contract_emits_only_stable_reference_metadata():
    contract = next(
        item for item in default_skill_context_contracts()
        if getattr(item, "contract_id", "") == "email"
    )
    updates = contract.emit_context_updates(
        intent="email.list_recent",
        result={
            "message": "sensitive summary must not be copied",
            "email_context_entities": [
                {
                    "domain": "email",
                    "entity_type": "message",
                    "entity_id": "gmail-id",
                    "display_name": "E1",
                    "resolution_hints": {"gmail_thread_id": "thread-id"},
                }
            ],
        },
    )

    assert updates[0]["display_name"] == "E1"
    assert "summary" not in str(updates).casefold()


def test_email_contract_restores_only_safe_domain_anchor_across_session_rotation():
    class FakeEmailService:
        def working_context_hint(self, *, context):
            assert context["requested_by_user_id"] == "jordan"
            return {
                "skill_id": "skill.email.agent",
                "context_kind": "email_reference_set",
                "last_email_reference_set_id": "ref-set-1",
                "last_email_result_count": 2,
            }

    contract = next(
        item
        for item in default_skill_context_contracts(email_agent_service=FakeEmailService())
        if getattr(item, "contract_id", "") == "email"
    )
    enriched = contract.enrich_working_context(
        request_context={"requested_by_user_id": "jordan"},
        working_context={"entity_hints": [], "recent_turns": []},
    )
    blocked_by_newer_domain = contract.enrich_working_context(
        request_context={"requested_by_user_id": "jordan"},
        working_context={
            "entity_hints": [{"domain": "lists", "entity_type": "list"}],
            "recent_turns": [],
        },
    )

    assert enriched["active_skill_context"]["skill_id"] == "skill.email.agent"
    assert "gmail_message_id" not in str(enriched)
    assert blocked_by_newer_domain == {}


def test_conversation_contract_continues_pending_topic_subject():
    contracts = default_skill_context_contracts()
    conversation_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "conversation")
    updates = conversation_contract.continue_pending_interaction(
        intent="conversation.general",
        text="narwhal",
        missing_fields=["topic_subject"],
        current_entities={"conversation_question": "Which animal do you mean?"},
    )
    assert updates.get("topic_subject") == "narwhal"


def test_conversation_contract_continues_pending_confirmation():
    contracts = default_skill_context_contracts()
    conversation_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "conversation")
    updates = conversation_contract.continue_pending_interaction(
        intent="conversation.general",
        text="yes",
        missing_fields=["confirmation"],
        current_entities={"conversation_question": "Do you want me to continue?"},
    )
    assert updates.get("confirmation") == "yes"


def test_lists_contract_resolves_deictic_followup_from_registry():
    contracts = default_skill_context_contracts()
    lists_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "lists")
    resolver = ReferenceResolver()
    registry = EntityRegistry(
        entities=[
            TrackedEntity(
                domain="lists",
                entity_type="list",
                display_name="groceries",
                aliases=["grocery list"],
                salience=0.91,
            )
        ]
    )
    decision = MicroDecision(
        intent=Intent.LIST_GET_ITEMS,
        confidence=0.42,
        entities={"list_name": "that list"},
        ambiguity_flags=["deictic_list_reference"],
        recommended_owner=SessionOwner.MAIN,
        reasoning="backend_guess",
    )

    updated = lists_contract.resolve_followup(
        decision=decision,
        registry=registry,
        resolver=resolver,
        required_fields_for_intent=lambda intent, entities: [],
        has_blocking_ambiguity=lambda value: False,
    )
    assert updated.entities["list_name"] == "groceries"
    assert "deictic_list_reference" not in updated.ambiguity_flags
    assert "list_reference_resolved_from_context" in updated.ambiguity_flags
    assert updated.recommended_owner == SessionOwner.MICRO
    assert updated.confidence >= 0.89


def test_lists_contract_continues_pending_interaction_from_suggestions():
    contracts = default_skill_context_contracts()
    lists_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "lists")

    updates = lists_contract.continue_pending_interaction(
        intent="lists.get_items",
        text="yeah my easter prep list",
        missing_fields=["list_name"],
        current_entities={
            "list_suggestions": ["easter prep", "groceries"],
            "available_lists": ["groceries", "to-do", "easter prep"],
        },
    )
    assert updates.get("list_name") == "easter prep"


def test_lists_contract_continues_pending_interaction_for_item_and_completion_mode():
    contracts = default_skill_context_contracts()
    lists_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "lists")

    updates = lists_contract.continue_pending_interaction(
        intent="lists.mark_item_done",
        text="bananas",
        missing_fields=["item_text", "completion_mode"],
        current_entities={
            "item_suggestions": ["bananas", "milk"],
            "available_items": ["bananas", "eggs"],
        },
    )
    assert updates.get("item_text") == "bananas"


def test_lists_contract_shapes_tool_followup_for_unknown_list():
    contracts = default_skill_context_contracts()
    lists_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "lists")
    registry = EntityRegistry(
        entities=[
            TrackedEntity(
                domain="lists",
                entity_type="list",
                display_name="groceries",
                aliases=["grocery list"],
                salience=0.92,
            )
        ]
    )
    shaped = lists_contract.shape_tool_followup(
        intent="lists.get_items",
        status="unknown_list",
        tool_result={
            "available_lists": ["groceries", "to-do"],
            "suggestions": ["groceries"],
        },
        entities={},
        missing_fields=[],
        question=None,
        registry=registry,
    )
    assert "list_name" in shaped.get("missing_fields", [])
    entities = shaped.get("entities", {})
    assert entities.get("list_suggestions") == ["groceries"]
    assert entities.get("last_list_name") == "groceries"
    assert "Did you mean" in str(shaped.get("question") or "")


def test_lists_contract_refines_missing_fields_for_deictic_reference():
    contracts = default_skill_context_contracts()
    lists_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "lists")
    missing = lists_contract.refine_missing_fields(
        intent="lists.add_item",
        entities={"list_name": "that list", "item_text": "milk"},
        missing_fields=[],
        resolver=ReferenceResolver(),
    )
    assert "list_name" in missing


def test_lists_contract_refines_missing_fields_for_unknown_available_list():
    contracts = default_skill_context_contracts()
    lists_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "lists")
    missing = lists_contract.refine_missing_fields(
        intent="lists.get_items",
        entities={
            "list_name": "blue",
            "available_lists": ["groceries", "to-do"],
        },
        missing_fields=[],
        resolver=ReferenceResolver(),
    )
    assert "list_name" in missing


def test_lists_contract_legacy_main_handoff_hints_fall_back_to_context_reference():
    contracts = default_skill_context_contracts()
    lists_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "lists")
    hints = lists_contract.legacy_main_handoff_hints(
        registry=EntityRegistry(),
        context_reference={"last_list_name": "groceries"},
    )
    assert hints.get("last_list_name") == "groceries"


def test_lights_contract_legacy_main_handoff_hints_use_registry_first():
    contracts = default_skill_context_contracts()
    lights_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "lights")
    hints = lights_contract.legacy_main_handoff_hints(
        registry=EntityRegistry(
            entities=[
                TrackedEntity(
                    domain="home",
                    entity_type="switch",
                    display_name="kitchen light",
                    salience=0.93,
                )
            ]
        ),
        context_reference={"last_switch_name": "porch light"},
        runtime_context={
            "available_switches": [
                {"name": "kitchen light", "state": "off"},
                {"name": "porch light", "state": "on"},
            ]
        },
    )
    assert hints.get("last_switch_name") == "kitchen light"
    assert isinstance(hints.get("available_switches"), list)
    assert hints.get("available_switches")


def test_lights_contract_continues_unknown_switch_with_unique_available_alias():
    contracts = default_skill_context_contracts()
    lights_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "lights")

    updates = lights_contract.continue_pending_interaction(
        intent="home.set_switch",
        text="I think you have it called office",
        missing_fields=["switch_name"],
        current_entities={
            "action": "on",
            "available_switches": [
                "office test light",
                "kitchen light",
                "living room lamp",
            ],
        },
    )

    assert updates == {"switch_name": "office test light"}


def test_lights_contract_does_not_guess_from_generic_switch_word():
    contracts = default_skill_context_contracts()
    lights_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "lights")

    updates = lights_contract.continue_pending_interaction(
        intent="home.set_switch",
        text="use the light",
        missing_fields=["switch_name"],
        current_entities={
            "action": "on",
            "available_switches": ["office test light", "kitchen light"],
        },
    )

    assert updates == {}


def test_lights_contract_negated_bulk_phrase_resolves_only_named_switch():
    contracts = default_skill_context_contracts()
    lights_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "lights")

    updates = lights_contract.continue_pending_interaction(
        intent="home.set_switch",
        text="not all of them, use office",
        missing_fields=["switch_name"],
        current_entities={
            "action": "on",
            "available_switches": ["office test light", "kitchen light"],
        },
    )

    assert updates == {"switch_name": "office test light"}


def test_lights_contract_keeps_clarifying_when_alias_matches_multiple_switches():
    contracts = default_skill_context_contracts()
    lights_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "lights")

    updates = lights_contract.continue_pending_interaction(
        intent="home.set_switch",
        text="use office",
        missing_fields=["switch_name"],
        current_entities={
            "action": "on",
            "available_switches": ["office test light", "office ceiling light"],
        },
    )

    assert updates == {}


@pytest.mark.parametrize(
    "text",
    [
        "not office",
        "anything but office",
        "don’t use office",
        "dont use office",
        "office, but turn it off",
    ],
)
def test_lights_contract_keeps_clarifying_for_negation_or_action_conflict(text):
    contracts = default_skill_context_contracts()
    lights_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "lights")

    updates = lights_contract.continue_pending_interaction(
        intent="home.set_switch",
        text=text,
        missing_fields=["switch_name"],
        current_entities={
            "action": "on",
            "available_switches": ["office test light", "kitchen light"],
        },
    )

    assert updates == {}


def test_calendar_contract_legacy_main_handoff_hints_fall_back_to_context_reference():
    contracts = default_skill_context_contracts()
    calendar_contract = next(contract for contract in contracts if getattr(contract, "contract_id", "") == "calendar")
    hints = calendar_contract.legacy_main_handoff_hints(
        registry=EntityRegistry(),
        context_reference={"last_calendar_person": "Jordan"},
    )
    assert hints.get("last_calendar_person") == "Jordan"


def test_calendar_contract_resolves_deictic_event_update_from_registry():
    contract = next(
        item for item in default_skill_context_contracts() if getattr(item, "contract_id", "") == "calendar"
    )
    registry = EntityRegistry(
        entities=[
            TrackedEntity(
                domain="calendar",
                entity_type="event",
                entity_id="event-1",
                display_name="Dinner with Arcese family",
                aliases=["Dinner with Arcese family"],
                salience=0.98,
                resolution_hints={"event_id": "event-1", "calendar_id": "personal@example.com"},
            )
        ]
    )
    decision = MicroDecision(
        intent=Intent.CALENDAR_UPDATE_EVENT,
        confidence=0.94,
        entities={"event_reference": "that", "all_day": True},
        ambiguity_flags=["deictic_event_reference"],
        recommended_owner=SessionOwner.MAIN,
    )

    resolved = contract.resolve_followup(
        decision=decision,
        registry=registry,
        resolver=ReferenceResolver(),
        required_fields_for_intent=lambda intent, entities: [],
        has_blocking_ambiguity=lambda candidate: False,
    )

    assert resolved.entities["event_reference"] == "Dinner with Arcese family"
    assert resolved.entities["event_id"] == "event-1"
    assert resolved.entities["calendar_id"] == "personal@example.com"
    assert "deictic_event_reference" not in resolved.ambiguity_flags


def test_calendar_contract_carries_latest_event_from_durable_memory():
    contract = next(
        item for item in default_skill_context_contracts() if getattr(item, "contract_id", "") == "calendar"
    )

    hints = contract.memory_handoff_hints(
        intent="calendar.update_event",
        request_text="make that all day",
        relevant_memory=[
            {
                "intent": "calendar.add_event",
                "request_text": "add Dinner with Arcese family to my calendar on August 28 at 5pm",
                "response_text": 'Added "Dinner with Arcese family" (August 28 at 5pm).',
            }
        ],
    )

    assert hints == {
        "last_event_reference": "Dinner with Arcese family",
        "last_calendar_action": "calendar.add_event",
    }


def test_calendar_contract_parses_named_event_and_resolves_memory_handoff():
    contract = next(
        item for item in default_skill_context_contracts() if getattr(item, "contract_id", "") == "calendar"
    )
    hints = contract.memory_handoff_hints(
        intent=None,
        request_text="please make that an all day event actually",
        relevant_memory=[
            {
                "intent": "calendar.add_event",
                "request_text": "Please create a calandar event for Sept 19 called ICDP party",
                "response_summary": "needs_clarification",
            }
        ],
    )
    decision = MicroDecision(
        intent=Intent.CALENDAR_UPDATE_EVENT,
        confidence=0.94,
        entities={"event_reference": "that", "all_day": True},
        ambiguity_flags=["deictic_event_reference"],
        recommended_owner=SessionOwner.MAIN,
    )

    resolved = contract.resolve_handoff_followup(
        decision=decision,
        active_skill_context=hints,
        resolver=ReferenceResolver(),
    )

    assert hints["last_event_reference"] == "ICDP party"
    assert resolved.entities["event_reference"] == "ICDP party"
    assert "deictic_event_reference" not in resolved.ambiguity_flags
    assert "event_reference_resolved_from_memory_handoff" in resolved.ambiguity_flags


def test_calendar_contract_uses_confirmed_standalone_update_reference():
    contract = next(
        item for item in default_skill_context_contracts() if getattr(item, "contract_id", "") == "calendar"
    )

    hints = contract.memory_handoff_hints(
        intent=None,
        request_text="make that all day",
        relevant_memory=[
            {
                "intent": "calendar.update_event",
                "request_text": "ICDP party",
                "response_summary": "ok",
            }
        ],
    )

    assert hints["last_event_reference"] == "ICDP party"
