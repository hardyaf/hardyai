from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.runtime import reset_runtime


def _ask(
    client: TestClient,
    *,
    text: str,
    session_id: str,
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


def test_api_list_yes_continues_pending_disambiguation():
    reset_runtime(hard_clear=True)
    client = TestClient(app)
    session_id = "context-api-list-yes"

    created = _ask(client, text="create easter prep list", session_id=session_id)
    assert created["intent"] == "lists.create_list"
    assert created["result"]["status"] == "ok"

    ambiguous = _ask(client, text="what is on my easter list", session_id=session_id)
    assert ambiguous["intent"] == "lists.get_items"
    assert ambiguous["route"] == "main_jarvis_repair"
    assert ambiguous["result"]["status"] == "unknown_list"
    assert "list_name" in ambiguous["result"]["missing_fields"]
    assert ambiguous["state"] == "AWAITING_CONFIRMATION"

    followup = _ask(client, text="yes", session_id=session_id)
    assert followup["intent"] == "lists.get_items"
    assert followup["route"] == "main_jarvis_repair"
    assert followup["result"]["status"] == "ok"
    assert followup["result"]["list_name"] == "easter prep"


def test_api_switch_deictic_turn_it_off_uses_prior_switch():
    reset_runtime(hard_clear=True)
    client = TestClient(app)
    session_id = "context-api-switch-deictic"

    first = _ask(client, text="turn office test light on", session_id=session_id)
    assert first["intent"] == "home.set_switch"
    assert first["route"] == "micro_tool"
    assert first["result"]["status"] == "ok"
    assert first["result"]["switch_name"] == "office test light"
    assert first["result"]["action"] == "on"

    followup = _ask(client, text="turn it off", session_id=session_id)
    assert followup["intent"] == "home.set_switch"
    assert followup["route"] == "micro_tool"
    assert followup["result"]["status"] == "ok"
    assert followup["result"]["switch_name"] == "office test light"
    assert followup["result"]["action"] == "off"


def test_api_long_session_keeps_summary_bounded():
    reset_runtime(hard_clear=True)
    client = TestClient(app)
    session_id = "context-api-summary-bounded"

    created = _ask(client, text="create groceries list", session_id=session_id)
    assert created["result"]["status"] == "ok"

    large_item = "x" * 900
    for index in range(1, 7):
        added = _ask(
            client,
            text=f"add {large_item}{index} to groceries",
            session_id=session_id,
        )
        assert added["intent"] == "lists.add_item"
        assert added["result"]["status"] == "ok"

    snapshot_response = client.get(f"/sessions/{session_id}/context")
    assert snapshot_response.status_code == 200
    payload = snapshot_response.json()
    context_state = payload.get("context_state")
    assert isinstance(context_state, dict)

    session_summary = context_state.get("session_summary")
    assert isinstance(session_summary, dict)
    summary_text = str(session_summary.get("summary_text") or "")
    assert summary_text.strip()
    assert len(summary_text) <= 900
    assert "lists.add_item:ok" in list(session_summary.get("resolved_decisions") or [])

    recent_turns = context_state.get("recent_turns")
    assert isinstance(recent_turns, list)
    assert len(recent_turns) <= 24


def test_api_pending_interaction_survives_runtime_reset_and_continues():
    reset_runtime(hard_clear=True)
    client = TestClient(app)
    session_id = "context-api-restart-pending"

    created = _ask(client, text="create easter prep list", session_id=session_id)
    assert created["result"]["status"] == "ok"

    first = _ask(client, text="what is on my easter list", session_id=session_id)
    assert first["intent"] == "lists.get_items"
    assert first["route"] == "main_jarvis_repair"
    assert first["result"]["status"] == "unknown_list"
    assert first["state"] == "AWAITING_CONFIRMATION"

    reset_runtime(hard_clear=False)

    followup = _ask(client, text="yes", session_id=session_id)
    assert followup["intent"] == "lists.get_items"
    assert followup["route"] == "main_jarvis_repair"
    assert followup["result"]["status"] == "ok"
    assert followup["result"]["list_name"] == "easter prep"
    assert followup["state"] == "IDLE"
