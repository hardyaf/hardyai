from app.tools.calendar_service import CalendarService
from app.skills.domains.calendar.handler import CalendarToolHandler
from app.skills.domains.calendar.storage import InMemoryCalendarStorage


def test_calendar_service_add_event_uses_local_stub_when_google_live_missing():
    service = CalendarService(google_live=None)

    response = service.add_event(
        event_title="dinner",
        when_hint="today at 5pm",
        invitee_names=["Jordan"],
    )

    assert response["status"] == "ok"
    assert response["source"] == "local_stub"
    assert response["sync_status"] == "not_synced_to_google"
    assert response["event"]["event_title"] == "dinner"
    assert response["event"]["invitee_names"] == ["Jordan"]


def test_calendar_service_add_event_uses_google_live_when_available():
    class FakeGoogleLive:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def add_event(self, *, event_title: str, when_hint: str, invitee_names=None):
            self.calls.append(
                {
                    "event_title": event_title,
                    "when_hint": when_hint,
                    "invitee_names": invitee_names,
                }
            )
            return {
                "status": "ok",
                "source": "google_live",
                "sync_status": "synced_to_google",
                "event": {
                    "event_title": event_title,
                    "when_hint": when_hint,
                    "invitee_names": invitee_names or [],
                },
                "invite_flow": {"recognized_invitees": invitee_names or []},
            }

    fake = FakeGoogleLive()
    service = CalendarService(google_live=fake)
    response = service.add_event(
        event_title="dinner",
        when_hint="today at 5pm",
        invitee_names=["Jordan"],
    )

    assert response["status"] == "ok"
    assert response["source"] == "google_live"
    assert response["sync_status"] == "synced_to_google"
    assert response["event"]["event_title"] == "dinner"
    assert fake.calls == [
        {
            "event_title": "dinner",
            "when_hint": "today at 5pm",
            "invitee_names": ["Jordan"],
        }
    ]


def test_calendar_service_add_event_surfaces_google_live_errors():
    class FakeGoogleLive:
        def add_event(self, *, event_title: str, when_hint: str, invitee_names=None):
            return {"status": "error", "message": "No calendar binding found."}

    service = CalendarService(google_live=FakeGoogleLive())
    response = service.add_event(
        event_title="dinner",
        when_hint="today at 5pm",
        invitee_names=["Jordan"],
    )

    assert response["status"] == "error"
    assert response["source"] == "google_live"
    assert "No calendar binding found." in response["message"]


def test_calendar_service_forwards_existing_event_update_and_delete_to_google():
    class FakeGoogleLive:
        def __init__(self) -> None:
            self.calls = []

        def update_event(self, **kwargs):
            self.calls.append(("update", kwargs))
            return {"status": "ok", "source": "google_live", "event": {"event_title": "Dinner"}}

        def delete_event(self, **kwargs):
            self.calls.append(("delete", kwargs))
            return {"status": "ok", "source": "google_live", "deleted": True, "event": {"event_title": "Dinner"}}

    google = FakeGoogleLive()
    service = CalendarService(google_live=google)

    updated = service.update_event(event_reference="Dinner", all_day=True, event_id="event-1")
    deleted = service.delete_event(event_reference="Dinner", event_id="event-1")

    assert updated["status"] == "ok"
    assert deleted["deleted"] is True
    assert google.calls == [
        (
            "update",
            {
                "event_reference": "Dinner",
                "new_event_title": None,
                "new_when_hint": None,
                "all_day": True,
                "event_id": "event-1",
                "calendar_id": None,
            },
        ),
        (
            "delete",
            {
                "event_reference": "Dinner",
                "event_id": "event-1",
                "calendar_id": None,
            },
        ),
    ]


def test_calendar_service_refuses_local_stub_existing_event_mutation():
    service = CalendarService(google_live=None)

    updated = service.update_event(event_reference="Dinner", all_day=True)
    deleted = service.delete_event(event_reference="Dinner")

    assert updated["error_code"] == "google_calendar_required"
    assert deleted["error_code"] == "google_calendar_required"


def test_calendar_query_events_delegates_arbitrary_aware_range_without_window_collapse():
    class FakeGoogleLive:
        def __init__(self) -> None:
            self.calls = []

        def query_events(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "status": "ok",
                "payload": {
                    "events": [],
                    "normalized_range": {
                        "start": kwargs["start"],
                        "end": kwargs["end"],
                        "timezone": "America/New_York",
                    },
                    "calendar_scope": {
                        "requested": kwargs["calendar_scope"],
                        "display_name": "House",
                        "resolved": True,
                        "is_default": True,
                        "candidates": [],
                    },
                    "source": {
                        "kind": "google_calendar_live",
                        "synchronized": True,
                        "coverage_complete": True,
                        "queried_at": "2026-09-01T12:00:00+00:00",
                    },
                    "truncated": False,
                },
            }

        def query_timezone(self):
            return "America/New_York"

    google = FakeGoogleLive()
    service = CalendarService(google_live=google)

    result = service.query_events(
        start="2026-03-08T00:00:00-05:00",
        end="2026-03-09T00:00:00-04:00",
        calendar_scope="default",
        time_basis="absolute",
        text="practice",
        order="newest",
        limit=7,
    )

    assert result["status"] == "ok"
    assert google.calls == [
        {
            "start": "2026-03-08T00:00:00-05:00",
            "end": "2026-03-09T00:00:00-04:00",
            "calendar_scope": "default",
            "text": "practice",
            "order": "newest",
            "limit": 7,
        }
    ]


def test_calendar_tool_timezone_is_server_owned_and_fails_closed():
    class FakeGoogleLive:
        def __init__(self, timezone_name):
            self.timezone_name = timezone_name

        def query_timezone(self):
            if isinstance(self.timezone_name, Exception):
                raise self.timezone_name
            return self.timezone_name

    assert CalendarService(google_live=None).tool_timezone({"timezone": "untrusted"}) == "UTC"
    assert (
        CalendarService(google_live=FakeGoogleLive("America/New_York")).tool_timezone(
            {"timezone": "untrusted"}
        )
        == "America/New_York"
    )
    assert CalendarService(google_live=FakeGoogleLive(RuntimeError("bad config"))).tool_timezone({}) is None


def test_calendar_query_events_rejects_naive_or_reversed_ranges_and_bounds():
    service = CalendarService(google_live=None)

    naive = service.query_events(
        start="2026-09-01T00:00:00",
        end="2026-09-02T00:00:00-04:00",
        calendar_scope="default",
        time_basis="absolute",
    )
    reversed_range = service.query_events(
        start="2026-09-02T00:00:00-04:00",
        end="2026-09-01T00:00:00-04:00",
        calendar_scope="default",
        time_basis="absolute",
    )
    bad_limit = service.query_events(
        start="2026-09-01T00:00:00-04:00",
        end="2026-09-02T00:00:00-04:00",
        calendar_scope="default",
        time_basis="absolute",
        limit=101,
    )

    assert naive["status"] == "error"
    assert reversed_range["status"] == "error"
    assert bad_limit["status"] == "error"


def test_calendar_query_events_local_fallback_is_truthful_and_bounded():
    storage = InMemoryCalendarStorage()
    storage.append_event(
        {
            "event_title": "Morning practice",
            "start_at": "2026-09-01T09:00:00+00:00",
            "end_at": "2026-09-01T10:00:00+00:00",
            "location": "Field 1",
            "person_name": "Alex",
        }
    )
    storage.append_event(
        {
            "event_title": "Unstructured legacy event",
            "when_hint": "sometime tomorrow",
            "person_name": "Alex",
        }
    )
    service = CalendarService(google_live=None, storage=storage)

    result = service.query_events(
        start="2026-09-01T00:00:00+00:00",
        end="2026-09-02T00:00:00+00:00",
        calendar_scope="Alex",
        time_basis="absolute",
        text="practice",
        limit=1,
    )

    assert result["status"] == "ok"
    payload = result["payload"]
    assert payload["events"][0]["title"] == "Morning practice"
    assert payload["source"] == {
        "kind": "local_in_memory",
        "synchronized": False,
        "coverage_complete": False,
        "queried_at": payload["source"]["queried_at"],
    }
    assert payload["truncated"] is False
    assert result["untrusted"] is True


def test_calendar_query_events_local_unknown_person_never_defaults():
    storage = InMemoryCalendarStorage()
    storage.append_event(
        {
            "event_title": "Practice",
            "start_at": "2026-09-01T09:00:00+00:00",
            "end_at": "2026-09-01T10:00:00+00:00",
            "person_name": "Alex",
        }
    )
    service = CalendarService(google_live=None, storage=storage)

    result = service.query_events(
        start="2026-09-01T00:00:00+00:00",
        end="2026-09-02T00:00:00+00:00",
        calendar_scope="Missing",
        time_basis="absolute",
    )

    assert result["status"] == "needs_input"
    assert result["payload"]["calendar_scope"]["resolved"] is False
    assert result["payload"]["calendar_scope"]["candidates"] == ["Alex"]
    assert result["payload"]["events"] == []


def test_calendar_typed_handler_canonicalizes_defaults_without_parsing_user_language():
    handler = CalendarToolHandler()

    result = handler.canonicalize_tool_arguments(
        tool_id="calendar.query_events",
        validated_arguments={
            "start": "2026-09-01T04:00:00+00:00",
            "end": "2026-09-02T04:00:00+00:00",
            "calendar_scope": " Alex Calendar ",
            "time_basis": "local_calendar",
        },
        request_context={},
    )

    assert result == {
        "start": "2026-09-01T04:00:00+00:00",
        "end": "2026-09-02T04:00:00+00:00",
        "calendar_scope": "Alex Calendar",
        "time_basis": "local_calendar",
        "order": "oldest",
        "limit": 20,
    }


def test_calendar_local_time_basis_corrects_dst_offsets_from_server_timezone():
    class FakeGoogleLive:
        def __init__(self) -> None:
            self.calls = []

        def query_timezone(self):
            return "America/New_York"

        def query_events(self, **kwargs):
            self.calls.append(kwargs)
            return {"status": "ok", "payload": {}}

    google = FakeGoogleLive()
    service = CalendarService(google_live=google)

    result = service.query_events(
        start="2026-03-08T00:00:00-04:00",
        end="2026-03-09T00:00:00-04:00",
        calendar_scope="default",
        time_basis="local_calendar",
    )

    assert result["status"] == "ok"
    assert google.calls[0]["start"] == "2026-03-08T00:00:00-05:00"
    assert google.calls[0]["end"] == "2026-03-09T00:00:00-04:00"
