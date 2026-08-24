from app.core.assistant_response import build_assistant_payload


def test_assistant_payload_combines_message_and_question():
    payload = build_assistant_payload(
        intent="calendar.add_event",
        route="main_jarvis_repair",
        result={
            "status": "needs_clarification",
            "message": "I can add a calendar event, but I still need the event title.",
            "question": "What should I name the calendar event?",
        },
        dialog={
            "mode": "conversation_pending",
            "turn_complete": False,
            "pending_intent": "calendar.add_event",
            "awaiting_fields": ["event_title"],
        },
    )

    assert payload["text"] == (
        "I can add a calendar event, but I still need the event title.\n"
        "What should I name the calendar event?"
    )
    assert payload["mode"] == "conversation_pending"
    assert payload["turn_complete"] is False


def test_assistant_payload_uses_fallback_summary_for_list_add():
    payload = build_assistant_payload(
        intent="lists.add_item",
        route="micro_tool",
        result={
            "status": "ok",
            "list_name": "groceries",
            "item_text": "milk",
        },
        dialog={"mode": "command_action", "turn_complete": True},
    )

    assert payload["text"] == 'Added "milk" to groceries.'


def test_assistant_payload_uses_fallback_summary_for_list_create():
    payload = build_assistant_payload(
        intent="lists.create_list",
        route="micro_tool",
        result={
            "status": "ok",
            "list_name": "project",
            "created": True,
        },
        dialog={"mode": "command_action", "turn_complete": True},
    )

    assert payload["text"] == "Created `project`."


def test_assistant_payload_calendar_add_mentions_local_only_when_not_synced():
    payload = build_assistant_payload(
        intent="calendar.add_event",
        route="main_jarvis_repair",
        result={
            "status": "ok",
            "event": {
                "event_title": "dinner",
                "when_hint": "today at 5pm",
            },
            "sync_status": "not_synced_to_google",
        },
        dialog={"mode": "command_action", "turn_complete": True},
    )

    assert payload["text"] == (
        'Added "dinner" (today at 5pm). '
        "Saved locally on the house calendar (not synced to Google yet)."
    )


def test_assistant_payload_summarizes_calendar_all_day_update():
    payload = build_assistant_payload(
        intent="calendar.update_event",
        route="main_jarvis_repair",
        result={"status": "ok", "event": {"event_title": "Dinner", "all_day": True}},
        dialog={"mode": "command_action", "turn_complete": True},
    )

    assert payload["text"] == 'Updated "Dinner" to an all-day event.'


def test_assistant_payload_summarizes_calendar_delete():
    payload = build_assistant_payload(
        intent="calendar.delete_event",
        route="main_jarvis_repair",
        result={"status": "ok", "event": {"event_title": "Dinner"}},
        dialog={"mode": "command_action", "turn_complete": True},
    )

    assert payload["text"] == 'Deleted "Dinner" from the calendar.'
