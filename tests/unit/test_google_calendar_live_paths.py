from pathlib import Path
from datetime import date, time

from app.services.google.calendar_live import CalendarBinding, GoogleCalendarLiveService


class _FakeEventsResource:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        return self.response


class _FakeCalendarApi:
    def __init__(self, response):
        self.resource = _FakeEventsResource(response)

    def events(self):
        return self.resource


def _query_service(monkeypatch, *, config, response):
    service = GoogleCalendarLiveService("unused.yaml")
    api = _FakeCalendarApi(response)
    monkeypatch.setattr(service, "_load_permissions", lambda: config)
    monkeypatch.setattr(service, "_load_token_store", lambda _path: {})
    monkeypatch.setattr(
        service,
        "_load_or_authorize_credentials",
        lambda **kwargs: (object(), kwargs["token_store"], False),
    )
    monkeypatch.setattr(service, "_build_calendar_service", lambda _credentials: api)
    return service, api


def test_calendar_update_helpers_preserve_date_for_all_day_conversion():
    event = {
        "start": {"dateTime": "2026-08-28T17:00:00-04:00"},
        "end": {"dateTime": "2026-08-28T18:00:00-04:00"},
    }

    resolved = GoogleCalendarLiveService._date_for_all_day_update(
        when_hint="",
        event=event,
        timezone_name="America/New_York",
    )

    assert resolved == date(2026, 8, 28)
    assert GoogleCalendarLiveService._all_day_duration_days(event) == 1


def test_calendar_update_time_parser_rejects_ambiguous_bare_time():
    assert GoogleCalendarLiveService._parse_time_hint("5:00") is None
    assert GoogleCalendarLiveService._parse_time_hint("05:00") == time(5, 0)
    assert GoogleCalendarLiveService._parse_time_hint("5:00 pm") == time(17, 0)


def test_google_http_error_status_supports_google_response_shape():
    class Response:
        status = 404

    class GoogleStyleError(Exception):
        resp = Response()

    assert GoogleCalendarLiveService._exception_status_code(GoogleStyleError()) == 404


def test_resolve_path_supports_old_repo_relative_permissions_prefix():
    fixtures_root = Path(__file__).resolve().parents[1] / "fixtures" / "google_path_test" / "jarvis_poc"
    permissions_dir = fixtures_root / "permissions"
    permissions_file = permissions_dir / "google_permissions.yaml"
    creds_file = permissions_dir / "example_google_credentials.json"

    service = GoogleCalendarLiveService(str(permissions_file))
    resolved = service._resolve_path("permissions/example_google_credentials.json", prefer_existing=True)

    assert resolved.resolve() == creds_file.resolve()


def test_resolve_path_supports_permissions_file_directory_relative_path():
    fixtures_root = Path(__file__).resolve().parents[1] / "fixtures" / "google_path_test" / "jarvis_poc"
    permissions_dir = fixtures_root / "permissions"
    permissions_file = permissions_dir / "google_permissions.yaml"
    creds_file = permissions_dir / "client.json"

    service = GoogleCalendarLiveService(str(permissions_file))
    resolved = service._resolve_path("client.json", prefer_existing=True)

    assert resolved.resolve() == creds_file.resolve()


def test_default_person_name_prefers_house_then_default():
    assert (
        GoogleCalendarLiveService._default_person_name(
            {
                "house_calendar": {"person_name": "House"},
                "house_person_name": "Jarvis",
                "default_person_name": "Jordan",
            }
        )
        == "House"
    )
    assert (
        GoogleCalendarLiveService._default_person_name(
            {
                "house_person_name": "Jarvis",
                "default_person_name": "Jordan",
            }
        )
        == "Jarvis"
    )
    assert GoogleCalendarLiveService._default_person_name({"default_person_name": "Jordan"}) == "Jordan"
    assert GoogleCalendarLiveService._default_person_name({}) is None


def test_query_timezone_reads_only_valid_protected_iana_name(monkeypatch):
    service = GoogleCalendarLiveService("unused.yaml")
    monkeypatch.setattr(
        service,
        "_load_permissions",
        lambda: {"calendar": {"default_timezone": "America/New_York"}},
    )
    assert service.query_timezone() == "America/New_York"

    monkeypatch.setattr(
        service,
        "_load_permissions",
        lambda: {"calendar": {"default_timezone": "not/a-timezone"}},
    )
    assert service.query_timezone() is None


def test_select_host_binding_prefers_house_person():
    bindings = [
        CalendarBinding(person_name="Jordan", calendar_id="jordan@example.com", account_key="house"),
        CalendarBinding(person_name="House", calendar_id="jarvis.house@example.com", account_key="house"),
    ]
    selected = GoogleCalendarLiveService._select_host_binding(
        bindings=bindings,
        calendar_cfg={"house_person_name": "House"},
    )
    assert selected is not None
    assert selected.person_name == "House"
    assert selected.calendar_id == "jarvis.house@example.com"


def test_resolve_invitee_emails_uses_aliases_people_and_direct_email():
    bindings = [
        CalendarBinding(person_name="House", calendar_id="jarvis.house@example.com", account_key="house"),
        CalendarBinding(person_name="Taylor", calendar_id="second.person@example.com", account_key="house"),
    ]
    resolved_emails, recognized_invitees, unresolved_invitees = GoogleCalendarLiveService._resolve_invitee_emails(
        invitee_names=["Jordan", "Taylor", "custom@example.com", "Unknown"],
        config={
            "contacts": {
                "aliases": [
                    {"name": "Jordan", "email": "personal.sender@example.com"},
                ]
            }
        },
        bindings=bindings,
    )

    assert resolved_emails == ["personal.sender@example.com", "second.person@example.com", "custom@example.com"]
    assert recognized_invitees == ["Jordan", "Taylor", "custom@example.com"]
    assert unresolved_invitees == ["Unknown"]


def test_normalize_requested_person_name_defaults_for_house_pronouns_and_list_repr():
    assert GoogleCalendarLiveService._normalize_requested_person_name("my") is None
    assert GoogleCalendarLiveService._normalize_requested_person_name("our") is None
    assert GoogleCalendarLiveService._normalize_requested_person_name("house") is None
    assert GoogleCalendarLiveService._normalize_requested_person_name("['house']") is None
    assert GoogleCalendarLiveService._normalize_requested_person_name(["House"]) is None
    assert GoogleCalendarLiveService._normalize_requested_person_name("Jordan") == "Jordan"


def test_resolve_explicit_person_name_supports_alias_semantics():
    bindings = [
        CalendarBinding(person_name="House", calendar_id="jarvis.house@example.com", account_key="house"),
        CalendarBinding(person_name="Jordan", calendar_id="personal.sender@example.com", account_key="house"),
    ]
    config = {
        "contacts": {
            "aliases": [
                {"name": "Jordan", "email": "personal.sender@example.com", "aliases": ["Lex", "Jordan"]},
            ]
        }
    }
    assert (
        GoogleCalendarLiveService._resolve_explicit_person_name(
            person_name="jordan",
            bindings=bindings,
            config=config,
        )
        == "Jordan"
    )
    assert (
        GoogleCalendarLiveService._resolve_explicit_person_name(
            person_name="lex",
            bindings=bindings,
            config=config,
        )
        == "Jordan"
    )
    assert (
        GoogleCalendarLiveService._resolve_explicit_person_name(
            person_name="jordan",
            bindings=bindings,
            config=config,
        )
        == "Jordan"
    )


def test_query_events_uses_exact_gog_style_provider_range_and_truthful_truncation(monkeypatch):
    service, api = _query_service(
        monkeypatch,
        config={
            "calendar": {
                "default_timezone": "America/New_York",
                "house_person_name": "House",
                "people": [
                    {"person_name": "House", "calendar_id": "house-provider-id", "account_key": "house"},
                    {"person_name": "Alex", "calendar_id": "alex-provider-id", "account_key": "house"},
                ],
            },
            "oauth": {},
            "contacts": {"aliases": [{"name": "Alex", "aliases": ["Lex"]}]},
        },
        response={
            "items": [
                {
                    "id": "event-1",
                    "summary": "Earlier practice",
                    "start": {"dateTime": "2026-09-01T09:00:00-04:00"},
                    "end": {"dateTime": "2026-09-01T10:00:00-04:00"},
                    "location": "Field 1\nNorth gate",
                },
                {
                    "id": "event-2",
                    "summary": "Later practice",
                    "start": {"dateTime": "2026-09-01T17:00:00-04:00"},
                    "end": {"dateTime": "2026-09-01T18:00:00-04:00"},
                },
            ],
            "nextPageToken": "provider-page-token",
        },
    )

    result = service.query_events(
        start="2026-09-01T04:00:00+00:00",
        end="2026-09-02T04:00:00+00:00",
        calendar_scope="Lex",
        text="practice",
        order="newest",
        limit=1,
    )

    assert result["status"] == "ok"
    assert api.resource.calls == [
        {
            "calendarId": "alex-provider-id",
            "timeMin": "2026-09-01T04:00:00+00:00",
            "timeMax": "2026-09-02T04:00:00+00:00",
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 2,
            "timeZone": "America/New_York",
            "showDeleted": False,
            "q": "practice",
        }
    ]
    payload = result["payload"]
    assert payload["events"][0]["title"] == "Later practice"
    assert payload["events"][0]["calendar_name"] == "Alex"
    assert "provider-id" not in str(payload)
    assert payload["truncated"] is True
    assert payload["source"]["synchronized"] is True
    assert payload["source"]["coverage_complete"] is False
    assert result["untrusted"] is True


def test_query_events_unknown_explicit_scope_never_falls_back_to_house(monkeypatch):
    service, api = _query_service(
        monkeypatch,
        config={
            "calendar": {
                "default_timezone": "UTC",
                "house_person_name": "House",
                "people": [
                    {"person_name": "House", "calendar_id": "house-provider-id"},
                    {"person_name": "Alex", "calendar_id": "alex-provider-id"},
                ],
            },
            "oauth": {},
        },
        response={"items": []},
    )

    result = service.query_events(
        start="2026-09-01T00:00:00+00:00",
        end="2026-09-02T00:00:00+00:00",
        calendar_scope="Missing",
    )

    assert result["status"] == "needs_input"
    assert result["payload"]["calendar_scope"]["candidates"] == ["Alex", "House"]
    assert result["payload"]["calendar_scope"]["resolved"] is False
    assert result["payload"]["source"]["synchronized"] is False
    assert api.resource.calls == []


def test_query_scope_normalizes_generic_possessive_calendar_selector():
    assert GoogleCalendarLiveService._query_scope_key("Alex's Calendar") == "alex"
    assert GoogleCalendarLiveService._query_scope_key("Alex\u2019s calendar") == "alex"
    assert GoogleCalendarLiveService._query_scope_key("Alex's") == "alex"
    assert GoogleCalendarLiveService._query_scope_key("Alex") == "alex"


def test_query_events_ambiguous_alias_returns_bounded_candidates(monkeypatch):
    service, api = _query_service(
        monkeypatch,
        config={
            "calendar": {
                "default_timezone": "UTC",
                "house_person_name": "House",
                "people": [
                    {"person_name": "Alex", "calendar_id": "alex-provider-id"},
                    {"person_name": "Taylor", "calendar_id": "taylor-provider-id"},
                ],
            },
            "oauth": {},
            "contacts": {
                "aliases": [
                    {"name": "Alex", "aliases": ["Kid"]},
                    {"name": "Taylor", "aliases": ["Kid"]},
                ]
            },
        },
        response={"items": []},
    )

    result = service.query_events(
        start="2026-09-01T00:00:00+00:00",
        end="2026-09-02T00:00:00+00:00",
        calendar_scope="Kid",
    )

    assert result["status"] == "needs_input"
    assert result["payload"]["calendar_scope"]["candidates"] == ["Alex", "Taylor"]
    assert api.resource.calls == []


def test_query_events_explicit_default_uses_configured_house_binding(monkeypatch):
    service, api = _query_service(
        monkeypatch,
        config={
            "calendar": {
                "default_timezone": "UTC",
                "house_person_name": "House",
                "people": [
                    {"person_name": "Alex", "calendar_id": "alex-provider-id"},
                    {"person_name": "House", "calendar_id": "house-provider-id"},
                ],
            },
            "oauth": {},
        },
        response={"items": []},
    )

    result = service.query_events(
        start="2026-09-01T00:00:00+00:00",
        end="2026-09-02T00:00:00+00:00",
        calendar_scope="default",
    )

    assert result["status"] == "ok"
    assert result["payload"]["calendar_scope"]["display_name"] == "House"
    assert result["payload"]["calendar_scope"]["is_default"] is True
    assert api.resource.calls[0]["calendarId"] == "house-provider-id"
