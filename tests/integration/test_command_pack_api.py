from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.runtime import reset_runtime


def _ask(client: TestClient, *, text: str, session_id: str = "pack-main-only") -> dict:
    response = client.post(
        "/ask",
        json={
            "text": text,
            "session_id": session_id,
            "user_id": "jordan",
            "source": "web",
            "context": {},
        },
    )
    assert response.status_code == 200
    return response.json()


def test_direct_command_pack_caller_enters_main_and_fails_closed_without_model() -> None:
    reset_runtime(hard_clear=True)
    client = TestClient(app)

    result = _ask(client, text="add milk to groceries")

    assert result["owner"] == "main_jarvis"
    assert result["route"] == "main_tool_loop"
    assert result["result"]["status"] == "safe_stop"
    assert result["result"]["stop_reason"] == "main_tool_model_unavailable"
    assert "classification" in result


def test_command_pack_preserves_deterministic_sleep_and_wake_controls() -> None:
    reset_runtime(hard_clear=True)
    client = TestClient(app)

    sleep = _ask(client, text="jarvis go to sleep", session_id="pack-power")
    blocked = _ask(client, text="add apples to groceries", session_id="pack-power")
    wake = _ask(client, text="wake up jarvis", session_id="pack-power")

    assert sleep["power_state"] == "ASLEEP"
    assert sleep["result"]["status"] == "sleeping"
    assert blocked["route"] == "sleep_guard"
    assert wake["power_state"] == "AWAKE"
    assert wake["result"]["status"] == "awake"
