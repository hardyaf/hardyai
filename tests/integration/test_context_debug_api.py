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


def test_session_context_snapshot_debug_export_endpoint_returns_context_layers():
    reset_runtime(hard_clear=True)
    client = TestClient(app)

    session_id = "context-debug-1"
    first = _ask(client, text="add milk to groceries", session_id=session_id)
    assert first["result"]["status"] == "ok"

    snapshot_response = client.get(f"/sessions/{session_id}/context")
    assert snapshot_response.status_code == 200
    payload = snapshot_response.json()

    session = payload.get("session")
    assert isinstance(session, dict)
    assert session.get("session_id") == session_id

    context_state = payload.get("context_state")
    assert isinstance(context_state, dict)
    assert isinstance(context_state.get("recent_turns"), list)
    assert isinstance(context_state.get("session_summary"), dict)
    assert isinstance(context_state.get("entity_registry"), dict)

    working_preview = payload.get("working_context_preview")
    assert isinstance(working_preview, dict)
    counts = working_preview.get("counts")
    assert isinstance(counts, dict)
    assert int(counts.get("recent_turns") or 0) >= 1

    trace_events = payload.get("context_trace_events")
    assert isinstance(trace_events, list)
    assert any(str(item.get("event_type") or "").startswith("context.") for item in trace_events if isinstance(item, dict))


def test_session_context_snapshot_debug_export_endpoint_returns_404_for_unknown_session():
    reset_runtime(hard_clear=True)
    client = TestClient(app)
    response = client.get("/sessions/does-not-exist/context")
    assert response.status_code == 404
