from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import operator_auth
from app.main import app


def _production_auth_settings() -> SimpleNamespace:
    return SimpleNamespace(
        app_env="production",
        operator_api_key="test-operator-key",
        operator_session_ttl_seconds=3600,
    )


def test_sensitive_routes_require_auth_independent_of_ticket_flag(monkeypatch):
    monkeypatch.setattr(operator_auth, "settings", _production_auth_settings())
    with TestClient(app) as client:
        assert client.post("/ask", json={"text": "hello"}).status_code == 401
        assert client.get("/events").status_code == 401
        assert client.get("/memory/recent").status_code == 401
        assert client.get("/dashboard/status").status_code == 401
        assert client.get("/house/switches").status_code == 401
        assert client.get("/tickets").status_code == 401


def test_operator_cannot_forge_discord_or_policy_context(monkeypatch):
    monkeypatch.setattr(operator_auth, "settings", _production_auth_settings())
    headers = {"X-Jarvis-Operator-Key": "test-operator-key"}
    with TestClient(app) as client:
        response = client.post(
            "/ask",
            headers=headers,
            json={
                "text": "hello there",
                "user_id": "another-user",
                "source": "discord",
                "context": {
                    "external_user_id": "999999999999999999",
                    "identity_bound": True,
                    "policy_profile": "adult",
                    "skill_scopes": ["skill.email.agent"],
                    "micro_command_explicit": True,
                },
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["source"] == "dashboard"

        events = client.get("/events", headers=headers).json()["events"]
        received = next(row for row in reversed(events) if row["event_type"] == "input.received")
        assert received["payload"]["source"] == "dashboard"
        assert received["payload"]["identity_bound"] is False


def test_operator_cookie_requires_csrf_for_mutation(monkeypatch):
    monkeypatch.setattr(operator_auth, "settings", _production_auth_settings())
    with TestClient(app) as client:
        login = client.post(
            "/operator/session",
            headers={"X-Jarvis-Operator-Key": "test-operator-key"},
        )
        assert login.status_code == 200
        csrf_token = login.json()["csrf_token"]
        assert client.get("/house/switches").status_code == 200
        assert (
            client.post(
                "/house/switches/office test light",
                json={"action": "on"},
            ).status_code
            == 403
        )
        allowed = client.post(
            "/house/switches/office test light",
            headers={"X-CSRF-Token": csrf_token},
            json={"action": "on"},
        )
        assert allowed.status_code == 200


def test_production_configuration_requires_operator_key(monkeypatch):
    monkeypatch.setattr(
        operator_auth,
        "settings",
        SimpleNamespace(app_env="production", operator_api_key=""),
    )
    try:
        operator_auth.validate_security_configuration()
    except RuntimeError as exc:
        assert "JARVIS_OPERATOR_API_KEY" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("production auth validation did not fail closed")
