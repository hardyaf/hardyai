from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.runtime import reset_runtime


def _ask(client: TestClient, *, text: str, session_id: str) -> dict:
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


def test_main_only_api_keeps_session_continuity_across_safe_stops() -> None:
    reset_runtime(hard_clear=True)
    client = TestClient(app)
    session_id = "context-api-main-only"

    first = _ask(client, text="add milk to groceries", session_id=session_id)
    second = _ask(client, text="now add eggs", session_id=session_id)

    assert first["session_id"] == session_id
    assert second["session_id"] == session_id
    assert first["owner"] == "main_jarvis"
    assert second["owner"] == "main_jarvis"
    assert first["result"]["status"] == "safe_stop"
    assert second["result"]["status"] == "safe_stop"


def test_main_only_session_is_backward_readable_after_runtime_reset() -> None:
    reset_runtime(hard_clear=True)
    client = TestClient(app)
    session_id = "context-api-restart-main-only"

    _ask(client, text="show groceries", session_id=session_id)
    reset_runtime(hard_clear=False)
    resumed = _ask(client, text="what about eggs?", session_id=session_id)

    assert resumed["session_id"] == session_id
    assert resumed["owner"] == "main_jarvis"
    snapshot_response = client.get(f"/sessions/{session_id}/context")
    assert snapshot_response.status_code == 200
