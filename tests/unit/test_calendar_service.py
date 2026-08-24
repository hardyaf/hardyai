from app.tools.calendar_service import CalendarService


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
