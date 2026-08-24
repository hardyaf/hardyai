from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.google.gmail_gateway import (
    GMAIL_READONLY_SCOPE,
    GmailHistoryExpiredError,
    GoogleGmailReadOnlyGateway,
    load_google_credentials,
)


class Request:
    def __init__(self, value=None, error: Exception | None = None):
        self.value = value
        self.error = error

    def execute(self):
        if self.error is not None:
            raise self.error
        return self.value


class HistoryResource:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.kwargs = None

    def list(self, **kwargs):
        self.kwargs = kwargs
        return Request(self.value, self.error)


class UsersResource:
    def __init__(self, *, profile, history):
        self.profile_value = profile
        self.history_resource = history

    def getProfile(self, **kwargs):
        return Request(self.profile_value)

    def history(self):
        return self.history_resource


class GmailService:
    def __init__(self, *, profile, history):
        self.users_resource = UsersResource(profile=profile, history=history)

    def users(self):
        return self.users_resource


def test_shared_google_token_refresh_preserves_configured_calendar_scopes(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    credential = object()

    class CalendarLive:
        def _load_permissions(self):
            return {
                "oauth": {
                    "token_store_path": "data/google_tokens.json",
                    "scopes": [
                        "https://www.googleapis.com/auth/calendar.readonly",
                        "https://www.googleapis.com/auth/calendar.events",
                        GMAIL_READONLY_SCOPE,
                    ],
                }
            }

        def _resolve_path(self, value, *, prefer_existing):
            captured["resolved_value"] = value
            captured["prefer_existing"] = prefer_existing
            return tmp_path / "google_tokens.json"

        @staticmethod
        def _load_token_store(path):
            return {"house": {"refresh_token": "hidden"}}

        @staticmethod
        def _load_or_authorize_credentials(**kwargs):
            captured["scopes"] = kwargs["scopes"]
            return credential, kwargs["token_store"], False

        @staticmethod
        def _save_token_store(path, token_store):
            raise AssertionError("An unchanged token should not be rewritten.")

    monkeypatch.setattr(
        "app.services.google.gmail_gateway.enable_native_google_tls_trust",
        lambda: None,
    )

    result = load_google_credentials(
        calendar_live=CalendarLive(),
        account_key="house",
        scopes=[GMAIL_READONLY_SCOPE],
        allow_interactive=False,
    )

    assert result is credential
    assert captured["scopes"] == [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
        GMAIL_READONLY_SCOPE,
    ]


def test_gateway_validates_profile_and_deduplicates_history_messages():
    history = HistoryResource(
        value={
            "historyId": "11",
            "nextPageToken": "next",
            "history": [
                {"messagesAdded": [{"message": {"id": "m1", "threadId": "t1"}}]},
                {"messagesAdded": [{"message": {"id": "m1", "threadId": "t1"}}]},
            ],
        }
    )
    gateway = GoogleGmailReadOnlyGateway(
        expected_profile_email="jarvis.house@example.com",
        gmail_service=GmailService(
            profile={"emailAddress": "jarvis.house@example.com", "historyId": "10"},
            history=history,
        ),
    )

    assert gateway.profile().history_id == "10"
    page = gateway.list_history(start_history_id="10", page_token=None, limit=999)
    assert [item.message_id for item in page.messages] == ["m1"]
    assert page.next_page_token == "next"
    assert history.kwargs["maxResults"] == 100


def test_gateway_maps_expired_history_cursor_and_rejects_wrong_profile():
    expired = RuntimeError("gone")
    expired.resp = SimpleNamespace(status=404)
    gateway = GoogleGmailReadOnlyGateway(
        expected_profile_email="jarvis.house@example.com",
        gmail_service=GmailService(
            profile={"emailAddress": "wrong@example.com", "historyId": "10"},
            history=HistoryResource(error=expired),
        ),
    )

    with pytest.raises(RuntimeError, match="does not match"):
        gateway.profile()
    with pytest.raises(GmailHistoryExpiredError):
        gateway.list_history(start_history_id="10", page_token=None, limit=10)
