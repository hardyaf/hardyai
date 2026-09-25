import copy
import re
from pathlib import Path
from datetime import date, time

from app.services.google.calendar_live import CalendarBinding, GoogleCalendarLiveService


class _FakeEventsResource:
    def __init__(self, response):
        self.responses = response if isinstance(response, list) else [response]
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


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


class _TypedHttpError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"http-{status_code}")
        self.status_code = status_code


class _TypedRequest:
    def __init__(self, callback):
        self.headers = {}
        self._callback = callback

    def execute(self):
        return self._callback(self.headers)


class _TypedEventsResource:
    def __init__(self) -> None:
        self.events_by_id = {}
        self.calls = []
        self.effect_count = 0
        self.patch_uncertain_after_effect = False
        self.delete_uncertain_after_effect = False

    def insert(self, **kwargs):
        self.calls.append(("insert", copy.deepcopy(kwargs)))

        def execute(_headers):
            event_id = kwargs["body"]["id"]
            if event_id in self.events_by_id:
                raise _TypedHttpError(409)
            event = copy.deepcopy(kwargs["body"])
            event.update({"etag": "etag-1", "status": "confirmed"})
            self.events_by_id[event_id] = event
            self.effect_count += 1
            return copy.deepcopy(event)

        return _TypedRequest(execute)

    def get(self, **kwargs):
        self.calls.append(("get", dict(kwargs)))

        def execute(_headers):
            event = self.events_by_id.get(kwargs["eventId"])
            if event is None:
                raise _TypedHttpError(404)
            return copy.deepcopy(event)

        return _TypedRequest(execute)

    def list(self, **kwargs):
        self.calls.append(("list", dict(kwargs)))
        return _TypedRequest(
            lambda _headers: {"items": [copy.deepcopy(item) for item in self.events_by_id.values()]}
        )

    def patch(self, **kwargs):
        self.calls.append(("patch", copy.deepcopy(kwargs)))

        def execute(headers):
            current = self.events_by_id[kwargs["eventId"]]
            if headers.get("If-Match") != current["etag"]:
                raise _TypedHttpError(412)
            current.update(copy.deepcopy(kwargs["body"]))
            current["etag"] = "etag-2"
            self.effect_count += 1
            if self.patch_uncertain_after_effect:
                self.patch_uncertain_after_effect = False
                raise TimeoutError("after provider commit")
            return copy.deepcopy(current)

        return _TypedRequest(execute)

    def delete(self, **kwargs):
        self.calls.append(("delete", dict(kwargs)))

        def execute(headers):
            current = self.events_by_id[kwargs["eventId"]]
            if headers.get("If-Match") != current["etag"]:
                raise _TypedHttpError(412)
            del self.events_by_id[kwargs["eventId"]]
            self.effect_count += 1
            if self.delete_uncertain_after_effect:
                self.delete_uncertain_after_effect = False
                raise TimeoutError("after provider delete")
            return None

        return _TypedRequest(execute)


class _TypedCalendarApi:
    def __init__(self) -> None:
        self.resource = _TypedEventsResource()

    def events(self):
        return self.resource


def _typed_service(monkeypatch):
    service = GoogleCalendarLiveService("unused.yaml")
    api = _TypedCalendarApi()
    config = {
        "calendar": {
            "default_timezone": "America/New_York",
            "house_person_name": "House",
            "people": [
                {"person_name": "House", "calendar_id": "house-provider-id", "account_key": "house"}
            ],
        },
        "oauth": {},
    }
    monkeypatch.setattr(service, "_load_permissions", lambda: config)
    monkeypatch.setattr(service, "_authorized_calendar_service", lambda **_kwargs: api)
    return service, api


def test_typed_create_uses_deterministic_provider_id_and_reconciles_duplicate(monkeypatch):
    service, api = _typed_service(monkeypatch)
    arguments = service.canonicalize_typed_write(
        tool_id="calendar.create_event",
        arguments={
            "title": "Dentist",
            "start": "2026-09-03T10:00:00-04:00",
            "end": "2026-09-03T11:00:00-04:00",
            "all_day": False,
            "timezone": "America/New_York",
            "calendar_scope": "default",
        },
    )
    operation_id = "toolop_v1_" + "a" * 64

    first = service.execute_typed_create(
        operation_id=operation_id,
        arguments_hash="b" * 64,
        arguments=arguments,
        include_invites=False,
    )
    replay = service.execute_typed_create(
        operation_id=operation_id,
        arguments_hash="b" * 64,
        arguments=arguments,
        include_invites=False,
    )

    provider_id = first["payload"]["provider_event_id"]
    assert provider_id == GoogleCalendarLiveService.typed_event_id(operation_id)
    assert len(provider_id) == 58
    assert re.fullmatch(r"[a-v0-9]+", provider_id)
    insert = next(call for call in api.resource.calls if call[0] == "insert")
    assert insert[1]["sendUpdates"] == "none"
    assert insert[1]["body"]["extendedProperties"]["private"] == {
        "jarvisOperationId": operation_id,
        "jarvisArgumentsHash": "b" * 64,
    }
    assert replay["payload"]["idempotent_replay"] is True
    assert api.resource.effect_count == 1


def test_typed_invited_create_is_one_insert_with_send_updates_all(monkeypatch):
    service, api = _typed_service(monkeypatch)
    arguments = service.canonicalize_typed_write(
        tool_id="calendar.create_event_with_invites",
        arguments={
            "title": "Planning",
            "start": "2026-09-04T09:00:00-04:00",
            "end": "2026-09-04T09:30:00-04:00",
            "all_day": False,
            "timezone": "America/New_York",
            "calendar_scope": "House",
            "invitee_emails": ["guest@example.com"],
        },
    )
    result = service.execute_typed_create(
        operation_id="toolop_v1_" + "c" * 64,
        arguments_hash="d" * 64,
        arguments=arguments,
        include_invites=True,
    )

    assert result["status"] == "ok"
    insert = next(call for call in api.resource.calls if call[0] == "insert")
    assert insert[1]["sendUpdates"] == "all"
    assert insert[1]["body"]["attendees"] == [{"email": "guest@example.com"}]


def test_typed_update_and_delete_reconcile_uncertain_commits_without_repeat(monkeypatch):
    service, api = _typed_service(monkeypatch)
    create_arguments = service.canonicalize_typed_write(
        tool_id="calendar.create_event",
        arguments={
            "title": "Original",
            "start": "2026-09-05T10:00:00-04:00",
            "end": "2026-09-05T11:00:00-04:00",
            "all_day": False,
            "timezone": "America/New_York",
            "calendar_scope": "default",
        },
    )
    created = service.execute_typed_create(
        operation_id="toolop_v1_" + "e" * 64,
        arguments_hash="f" * 64,
        arguments=create_arguments,
        include_invites=False,
    )
    target = created["payload"]
    update_arguments = service.canonicalize_typed_write(
        tool_id="calendar.update_event",
        arguments={
            "event_ref": target["event_ref"],
            "event_start": target["event"]["start"],
            "calendar_scope": "House",
            "timezone": "America/New_York",
            "patch": {"title": "Updated"},
        },
    )
    api.resource.patch_uncertain_after_effect = True
    updated = service.execute_typed_update(
        operation_id="toolop_v1_" + "1" * 64,
        arguments_hash="2" * 64,
        arguments=update_arguments,
    )

    assert updated["status"] == "ok"
    assert updated["payload"]["event"]["title"] == "Updated"
    assert updated["payload"]["idempotent_replay"] is True
    patch_request = next(call for call in api.resource.calls if call[0] == "patch")
    assert "attendees" not in patch_request[1]["body"]

    delete_arguments = service.canonicalize_typed_write(
        tool_id="calendar.delete_event",
        arguments={
            "event_ref": updated["payload"]["event_ref"],
            "event_start": updated["payload"]["event"]["start"],
            "calendar_scope": "House",
            "timezone": "America/New_York",
        },
    )
    api.resource.delete_uncertain_after_effect = True
    deleted = service.execute_typed_delete(
        operation_id="toolop_v1_" + "3" * 64,
        arguments_hash="4" * 64,
        arguments=delete_arguments,
    )

    assert deleted["status"] == "ok"
    assert deleted["payload"]["event"]["deleted"] is True
    assert api.resource.effect_count == 3


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
        response=[
            {
                "items": [
                    {
                        "id": "event-1",
                        "summary": "Earlier practice",
                        "start": {"dateTime": "2026-09-01T09:00:00-04:00"},
                        "end": {"dateTime": "2026-09-01T10:00:00-04:00"},
                        "location": "Field 1\nNorth gate",
                    }
                ],
                "nextPageToken": "provider-page-token",
            },
            {
                "items": [
                    {
                        "id": "event-2",
                        "summary": "Later practice",
                        "start": {"dateTime": "2026-09-01T17:00:00-04:00"},
                        "end": {"dateTime": "2026-09-01T18:00:00-04:00"},
                    }
                ]
            },
        ],
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
            "maxResults": 100,
            "timeZone": "America/New_York",
            "showDeleted": False,
            "q": "practice",
        },
        {
            "calendarId": "alex-provider-id",
            "timeMin": "2026-09-01T04:00:00+00:00",
            "timeMax": "2026-09-02T04:00:00+00:00",
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 100,
            "timeZone": "America/New_York",
            "showDeleted": False,
            "q": "practice",
            "pageToken": "provider-page-token",
        },
    ]
    payload = result["payload"]
    assert payload["events"][0]["title"] == "Later practice"
    assert payload["events"][0]["calendar_name"] == "Alex"
    assert "provider-id" not in str(payload)
    assert payload["truncated"] is True
    assert payload["source"]["synchronized"] is True
    assert payload["source"]["coverage_complete"] is False
    assert result["untrusted"] is True


def test_typed_recurrence_uses_structured_weekly_rrule(monkeypatch):
    service, api = _typed_service(monkeypatch)
    arguments = service.canonicalize_typed_write(
        tool_id="calendar.create_event",
        arguments={
            "title": "Acceptance practice",
            "start": "2026-09-29T18:00:00-04:00",
            "end": "2026-09-29T19:00:00-04:00",
            "all_day": False,
            "timezone": "America/New_York",
            "calendar_scope": "default",
            "recurrence": {
                "frequency": "weekly",
                "interval": 1,
                "count": 12,
                "by_weekday": ["TU", "TH"],
            },
        },
    )
    result = service.execute_typed_create(
        operation_id="toolop_v1_" + "8" * 64,
        arguments_hash="9" * 64,
        arguments=arguments,
        include_invites=False,
    )

    assert result["status"] == "ok"
    insert = next(call for call in api.resource.calls if call[0] == "insert")
    assert insert[1]["body"]["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=TU,TH;COUNT=12"]


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
