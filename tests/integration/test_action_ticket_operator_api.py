from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import operator_auth
from app.main import app


def test_ticket_and_raw_trace_endpoints_require_operator_key(monkeypatch):
    monkeypatch.setattr(
        operator_auth,
        "settings",
        SimpleNamespace(action_tickets_enabled=True, operator_api_key="test-operator-key"),
    )
    client = TestClient(app)

    assert client.get("/tickets").status_code == 401
    assert client.get("/events").status_code == 401
    headers = {"X-Jarvis-Operator-Key": "test-operator-key"}
    assert client.get("/tickets", headers=headers).status_code == 200
    assert client.get("/events", headers=headers).status_code == 200

