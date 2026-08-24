from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_dashboard_and_voice_test_routes_serve_ui():
    client = TestClient(app)

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "Jarvis House Dashboard" in dashboard.text
    assert "Voice Test Mode" in dashboard.text
    assert "Discord Entry Channel" in dashboard.text
    assert "Start Mic" in dashboard.text
    assert "btn-icon" in dashboard.text
    assert "innerHTML" not in dashboard.text
    assert "insertAdjacentHTML" not in dashboard.text
    assert "Content-Security-Policy" in dashboard.headers
    assert "'unsafe-inline'" not in dashboard.headers["Content-Security-Policy"]
    assert dashboard.headers["X-Content-Type-Options"] == "nosniff"
    assert dashboard.headers["X-Frame-Options"] == "DENY"

    voice = client.get("/voice-test")
    assert voice.status_code == 200
    assert "Run Voice Simulation" in voice.text

    status_ui = client.get("/status-dashboard")
    assert status_ui.status_code == 200
    assert "Jarvis Status Dashboard" in status_ui.text
    assert "innerHTML" not in status_ui.text

    status_json = client.get("/dashboard/status")
    assert status_json.status_code == 200
    payload = status_json.json()
    assert "power_state" in payload
    assert "model_runtime" in payload
    assert "active_model_lane" in payload
    assert "effective_owner" in payload
    assert "poll_interval_seconds" in payload
    assert "latest_context_packet_event" in payload
    assert "latest_pending_transition_event" in payload
    assert "latest_entity_registry_event" in payload
    assert "latest_summary_update_event" in payload
