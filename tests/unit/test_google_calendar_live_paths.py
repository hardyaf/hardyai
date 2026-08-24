from pathlib import Path
from datetime import date, time

from app.services.google.calendar_live import CalendarBinding, GoogleCalendarLiveService


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
