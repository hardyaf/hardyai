from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.runtime import reset_runtime


def _ask(
    client: TestClient,
    *,
    text: str,
    session_id: str = "pack-1",
    user_id: str = "jordan",
    source: str = "web",
) -> dict:
    response = client.post(
        "/ask",
        json={
            "text": text,
            "session_id": session_id,
            "user_id": user_id,
            "source": source,
            "context": {},
        },
    )
    assert response.status_code == 200
    return response.json()


def _assert_calendar_add_result(
    payload: dict,
    *,
    expected_title: str,
    expected_when_hint: str,
) -> None:
    assert payload["intent"] == "calendar.add_event"
    assert payload["owner"] == "main_jarvis"
    assert payload["route"] in {"main_jarvis_repair", "main_jarvis"}
    result = payload["result"]
    status = str(result.get("status") or "").strip().lower()
    if status == "ok":
        assert result["event"]["event_title"] == expected_title
        assert result["event"]["when_hint"] == expected_when_hint
        return
    assert status == "error"
    assert result.get("source") == "google_live"
    assert "google calendar write failed" in str(result.get("message") or "").lower()


def test_command_pack_api_flow_and_handoffs():
    reset_runtime(hard_clear=True)
    client = TestClient(app)

    bootstrap_groceries = _ask(client, text="create groceries list")
    assert bootstrap_groceries["intent"] == "lists.create_list"
    assert bootstrap_groceries["result"]["status"] == "ok"

    list_add = _ask(client, text="add milk to groceries")
    assert list_add["intent"] == "lists.add_item"
    assert list_add["route"] == "micro_tool"
    assert list_add["result"]["status"] == "ok"
    assert "confidence" in list_add["classification"]
    assert list_add["result"]["list_name"] == "groceries"

    list_alias = _ask(client, text="add tofu to my grocery list")
    assert list_alias["intent"] == "lists.add_item"
    assert list_alias["route"] == "micro_tool"
    assert list_alias["result"]["status"] == "ok"
    assert list_alias["result"]["list_name"] == "groceries"

    list_view = _ask(client, text="show me groceries")
    assert list_view["intent"] == "lists.get_items"
    assert list_view["route"] == "micro_tool"
    assert list_view["result"]["status"] == "ok"
    assert list_view["result"]["list_name"] == "groceries"
    assert list_view["result"]["count"] == 2

    list_add_pronoun = _ask(client, text="add tofu to it")
    assert list_add_pronoun["intent"] == "lists.add_item"
    assert list_add_pronoun["route"] == "micro_tool"
    assert list_add_pronoun["result"]["status"] == "ok"
    assert list_add_pronoun["result"]["list_name"] == "groceries"

    create_list = _ask(client, text="create project list")
    assert create_list["intent"] == "lists.create_list"
    assert create_list["route"] == "main_jarvis"
    assert create_list["owner"] == "main_jarvis"
    assert create_list["result"]["status"] == "ok"
    assert create_list["result"]["executed_by"] == "main_fast_fallback"
    assert create_list["result"]["list_name"] == "project"
    assert create_list["result"]["created"] is True

    list_add_project = _ask(client, text="add roadmap to project")
    assert list_add_project["intent"] == "lists.add_item"
    assert list_add_project["route"] == "micro_tool"
    assert list_add_project["result"]["status"] == "ok"
    assert list_add_project["result"]["list_name"] == "project"

    # Core regression: this should stay calendar, not list.
    calendar_add = _ask(client, text="add dentist appointment to my calendar")
    assert calendar_add["intent"] == "calendar.add_event"
    assert calendar_add["route"] == "main_jarvis_repair"
    assert calendar_add["owner"] == "main_jarvis"
    assert calendar_add["result"]["status"] == "needs_clarification"
    assert "when_hint" in calendar_add["result"]["missing_fields"]

    calendar_add_followup = _ask(client, text="tomorrow at 9am")
    _assert_calendar_add_result(
        calendar_add_followup,
        expected_title="dentist appointment",
        expected_when_hint="tomorrow at 9am",
    )

    calendar_add_natural = _ask(
        client,
        text=(
            "Jarvis can you add an event on my calendar tomorrow for "
            "opioid settlement fund disbursement committee"
        ),
    )
    _assert_calendar_add_result(
        calendar_add_natural,
        expected_title="opioid settlement fund disbursement committee",
        expected_when_hint="tomorrow",
    )

    calendar_add_repaired = _ask(
        client,
        text="Schedule opioid settlement fund disbursement committee for tomorrow on my calendar",
    )
    if calendar_add_repaired["intent"] == "calendar.add_event":
        _assert_calendar_add_result(
            calendar_add_repaired,
            expected_title="opioid settlement fund disbursement committee",
            expected_when_hint="tomorrow",
        )
        if calendar_add_repaired["result"]["status"] == "ok":
            assert calendar_add_repaired["result"]["repaired_by"] == "main_jarvis"
            assert calendar_add_repaired["result"]["repair_source"] == "heuristic"
    else:
        # Environment-dependent path when no semantic repair backend is available.
        assert calendar_add_repaired["route"] == "main_jarvis"
        assert calendar_add_repaired["intent"] in {"unknown", "conversation.general"}
        assert calendar_add_repaired["result"]["status"] in {"conversation", "not_actionable"}

    calendar_view = _ask(client, text="what's on my calendar today")
    assert calendar_view["intent"] == "calendar.view"
    assert calendar_view["route"] == "micro_tool"
    calendar_view_status = str(calendar_view["result"].get("status") or "").strip().lower()
    if calendar_view_status == "ok":
        if calendar_view["result"].get("source") == "local_stub":
            assert calendar_view["result"]["event_count"] >= 1
            assert "dentist appointment" in calendar_view["result"]["summary"].lower()
        else:
            assert calendar_view["result"].get("source") == "google_live"
            assert "calendar view" in str(calendar_view["result"].get("summary", "")).lower()
    else:
        assert calendar_view_status == "error"
        assert calendar_view["result"].get("source") == "google_live"
        message = str(calendar_view["result"].get("message") or "").lower()
        assert "google calendar view failed" in message or "no calendar people bindings" in message

    switch_on = _ask(client, text="turn office test light on")
    assert switch_on["intent"] == "home.set_switch"
    assert switch_on["route"] == "micro_tool"
    assert switch_on["result"]["status"] == "ok"
    assert switch_on["result"]["switch_name"] == "office test light"
    assert switch_on["result"]["action"] == "on"

    convo = _ask(client, text="help me plan dinners this week", session_id="pack-2")
    assert convo["route"] == "main_jarvis"
    assert convo["owner"] == "main_jarvis"
    assert convo["state"] == "CONVERSATIONAL"

    handoff = _ask(client, text="add eggs to groceries", session_id="pack-2")
    assert handoff["route"] == "micro_tool"
    assert handoff["owner"] == "micro_jarvis"
    assert handoff["intent"] == "lists.add_item"
    assert handoff["result"]["status"] == "ok"

    compound = _ask(
        client,
        text="Jarvis lets create a weekend list and add bananas to it",
        session_id="pack-4",
    )
    assert compound["route"] == "main_jarvis"
    assert compound["owner"] == "main_jarvis"
    assert compound["result"]["status"] == "executed"
    assert compound["result"]["execution"]["status"] == "ok"
    assert compound["result"]["execution"]["requested_count"] == 2
    assert compound["result"]["execution"]["success_count"] == 2

    multi_item_compound = _ask(
        client,
        text=(
            "Lets make a list called Birthday Presents for Casey. On it lets put "
            "pokemon reveal decks, a drone simulator, rockband drumset emulator"
        ),
        session_id="pack-compound-multi",
    )
    assert multi_item_compound["route"] == "main_jarvis"
    assert multi_item_compound["result"]["status"] == "executed"
    execution = multi_item_compound["result"]["execution"]
    assert execution["status"] == "ok"
    assert execution["requested_count"] == 4
    assert execution["success_count"] == 4
    assert multi_item_compound["result"]["message"] == (
        "Created `Birthday Presents for Casey` and added 3 item(s)."
    )

    created_items = _ask(
        client,
        text="show Birthday Presents for Casey",
        session_id="pack-compound-multi",
    )
    assert created_items["result"]["items"] == [
        "pokemon reveal decks",
        "a drone simulator",
        "rockband drumset emulator",
    ]

    sleep = _ask(client, text="jarvis go to sleep", session_id="pack-3")
    assert sleep["power_state"] == "ASLEEP"
    assert sleep["result"]["status"] == "sleeping"

    blocked = _ask(client, text="add apples to groceries", session_id="pack-3")
    assert blocked["route"] == "sleep_guard"
    assert blocked["result"]["status"] == "sleeping"

    wake = _ask(client, text="wake up jarvis", session_id="pack-3")
    assert wake["power_state"] == "AWAKE"
    assert wake["result"]["status"] == "awake"

    resumed = _ask(client, text="add apples to groceries", session_id="pack-3")
    assert resumed["route"] == "micro_tool"
    assert resumed["result"]["status"] == "ok"

    events_resp = client.get("/events")
    assert events_resp.status_code == 200
    events = events_resp.json()["events"]
    event_types = [entry["event_type"] for entry in events]
    assert "handoff.main_to_micro" in event_types
