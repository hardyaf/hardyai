from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.runtime import reset_runtime


def test_house_switch_endpoints_and_logs():
    reset_runtime(hard_clear=True)
    client = TestClient(app)

    list_resp = client.get("/house/switches")
    assert list_resp.status_code == 200
    switches = list_resp.json()["switches"]
    assert switches, "Expected default house switches to be pre-seeded."

    set_resp = client.post(
        "/house/switches/office test light",
        json={"action": "on"},
    )
    assert set_resp.status_code == 200
    assert set_resp.json()["status"] == "ok"
    assert set_resp.json()["delivery"]["session"]["status"] == "committed"
    assert set_resp.json()["delivery"]["ticket"]["status"] == "committed"

    list_again = client.get("/house/switches")
    assert list_again.status_code == 200
    office = next(item for item in list_again.json()["switches"] if item["name"] == "office test light")
    assert office["state"] == "on"

    logs_resp = client.get("/house/switch-actions?limit=20")
    assert logs_resp.status_code == 200
    actions = logs_resp.json()["actions"]
    assert actions
    assert actions[0]["switch_name"] == "office test light"
    assert actions[0]["state_after"] == "on"


def test_ask_switch_command_resolves_to_existing_named_switch():
    reset_runtime(hard_clear=True)
    client = TestClient(app)

    ask_resp = client.post(
        "/ask",
        json={
            "text": "turn on the office light",
            "session_id": "house-ask-1",
            "user_id": "jordan",
            "source": "dashboard",
            "context": {},
        },
    )
    assert ask_resp.status_code == 200
    payload = ask_resp.json()
    assert payload["intent"] == "home.set_switch"
    assert payload["result"]["switch_name"] == "office test light"
    assert payload["result"]["matched_existing"] is True
    assert payload["delivery"]["memory"]["status"] in {"queued", "committed"}

    switches = client.get("/house/switches").json()["switches"]
    names = [item["name"] for item in switches]
    assert "office test light" in names
    assert "office light" not in names


def test_ask_unknown_switch_does_not_create_new_row():
    reset_runtime(hard_clear=True)
    client = TestClient(app)

    ask_resp = client.post(
        "/ask",
        json={
            "text": "turn on garage floodlight",
            "session_id": "house-ask-2",
            "user_id": "jordan",
            "source": "dashboard",
            "context": {},
        },
    )
    assert ask_resp.status_code == 200
    payload = ask_resp.json()
    assert payload["intent"] == "home.set_switch"
    assert payload["result"]["status"] == "unknown_switch"

    switches = client.get("/house/switches").json()["switches"]
    names = [item["name"] for item in switches]
    assert "garage floodlight" not in names


def test_ask_unknown_switch_allows_followup_recovery_in_same_session():
    reset_runtime(hard_clear=True)
    client = TestClient(app)

    first_resp = client.post(
        "/ask",
        json={
            "text": "turn garage floodlight on",
            "session_id": "house-ask-2b",
            "user_id": "jordan",
            "source": "dashboard",
            "context": {},
        },
    )
    assert first_resp.status_code == 200
    first_payload = first_resp.json()
    assert first_payload["intent"] == "home.set_switch"
    assert first_payload["result"]["status"] == "unknown_switch"
    assert first_payload["dialog"]["mode"] == "conversation_pending"
    assert "switch_name" in first_payload["result"]["missing_fields"]

    second_resp = client.post(
        "/ask",
        json={
            "text": "I think you have it called office",
            "session_id": "house-ask-2b",
            "user_id": "jordan",
            "source": "dashboard",
            "context": {},
        },
    )
    assert second_resp.status_code == 200
    second_payload = second_resp.json()
    assert second_payload["intent"] == "home.set_switch"
    assert second_payload["result"]["status"] == "ok"
    assert second_payload["result"]["switch_name"] == "office test light"
    assert second_payload["result"]["action"] == "on"


def test_ask_turn_all_lights_on_updates_all_known_switches():
    reset_runtime(hard_clear=True)
    client = TestClient(app)

    ask_resp = client.post(
        "/ask",
        json={
            "text": "Can you turn all lights on",
            "session_id": "house-ask-3",
            "user_id": "jordan",
            "source": "dashboard",
            "context": {},
        },
    )
    assert ask_resp.status_code == 200
    payload = ask_resp.json()
    assert payload["intent"] == "home.set_switch"
    assert payload["route"] == "main_jarvis"
    assert payload["result"]["status"] == "executed"
    assert payload["result"]["plan"]["plan_type"] == "home.bulk_set"
    assert payload["result"]["plan"]["scope"] == "all_lights"
    assert payload["result"]["execution"]["status"] == "ok"
    assert payload["result"]["execution"]["success_count"] >= 1

    switches = client.get("/house/switches").json()["switches"]
    assert switches
    assert all(item["state"] == "on" for item in switches)
