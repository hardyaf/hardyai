from app.core.session_store import SessionStore


def test_channel_session_reused_within_idle_timeout():
    clock = {"now": 0.0}
    store = SessionStore(
        channel_idle_timeout_seconds=180.0,
        time_fn=lambda: float(clock["now"]),
    )

    first = store.get_or_create(
        session_id=None,
        user_id="jordan",
        source="web",
        channel_key="jordan:dashboard.command",
    )
    clock["now"] = 120.0
    second = store.get_or_create(
        session_id=None,
        user_id="jordan",
        source="web",
        channel_key="jordan:dashboard.command",
    )

    assert first.session_id == second.session_id


def test_channel_session_rotates_after_idle_timeout():
    clock = {"now": 0.0}
    store = SessionStore(
        channel_idle_timeout_seconds=60.0,
        time_fn=lambda: float(clock["now"]),
    )

    first = store.get_or_create(
        session_id=None,
        user_id="jordan",
        source="web",
        channel_key="jordan:dashboard.command",
    )
    clock["now"] = 61.0
    second = store.get_or_create(
        session_id=None,
        user_id="jordan",
        source="web",
        channel_key="jordan:dashboard.command",
    )

    assert first.session_id != second.session_id


def test_channel_session_can_force_rotation():
    clock = {"now": 0.0}
    store = SessionStore(
        channel_idle_timeout_seconds=180.0,
        time_fn=lambda: float(clock["now"]),
    )

    first = store.get_or_create(
        session_id=None,
        user_id="jordan",
        source="web",
        channel_key="jordan:dashboard.command",
    )
    clock["now"] = 10.0
    second = store.get_or_create(
        session_id=None,
        user_id="jordan",
        source="web",
        channel_key="jordan:dashboard.command",
        force_new_for_channel=True,
    )

    assert first.session_id != second.session_id


def test_channel_status_reports_runtime_fields():
    clock = {"now": 0.0}
    store = SessionStore(
        channel_idle_timeout_seconds=60.0,
        time_fn=lambda: float(clock["now"]),
    )
    first = store.get_or_create(
        session_id=None,
        user_id="jordan",
        source="web",
        channel_key="jordan:dashboard.command",
    )

    status = store.channel_status("jordan:dashboard.command")
    assert isinstance(status, dict)
    assert status["session_id"] == first.session_id
    assert status["channel_key"] == "jordan:dashboard.command"
    assert isinstance(status["last_activity_at"], str) and status["last_activity_at"]
    assert isinstance(status["expires_at"], str) and status["expires_at"]
    assert status["expired"] is False
    assert status["expires_in_seconds"] == 60.0


def test_channel_status_marks_expired_after_idle_timeout():
    clock = {"now": 0.0}
    store = SessionStore(
        channel_idle_timeout_seconds=60.0,
        time_fn=lambda: float(clock["now"]),
    )
    store.get_or_create(
        session_id=None,
        user_id="jordan",
        source="web",
        channel_key="jordan:dashboard.command",
    )
    clock["now"] = 61.0

    status = store.channel_status("jordan:dashboard.command")
    assert isinstance(status, dict)
    assert status["expired"] is True
    assert status["expires_in_seconds"] == 0.0


def test_manual_sweep_removes_only_expired_bindings():
    clock = {"now": 0.0}
    store = SessionStore(
        channel_idle_timeout_seconds=60.0,
        time_fn=lambda: float(clock["now"]),
    )
    store.get_or_create(
        session_id=None,
        user_id="jordan",
        source="web",
        channel_key="jordan:dashboard.command",
    )
    clock["now"] = 30.0
    store.get_or_create(
        session_id=None,
        user_id="jordan",
        source="web",
        channel_key="jordan:dashboard.voice",
    )
    clock["now"] = 89.0

    removed = store.sweep_expired_channel_bindings()
    assert removed == 1
    assert store.channel_status("jordan:dashboard.command") is None
    assert isinstance(store.channel_status("jordan:dashboard.voice"), dict)


def test_get_or_create_auto_sweeps_expired_other_channels():
    clock = {"now": 0.0}
    store = SessionStore(
        channel_idle_timeout_seconds=60.0,
        time_fn=lambda: float(clock["now"]),
    )
    store.get_or_create(
        session_id=None,
        user_id="jordan",
        source="web",
        channel_key="jordan:dashboard.command",
    )
    first_voice = store.get_or_create(
        session_id=None,
        user_id="jordan",
        source="web",
        channel_key="jordan:dashboard.voice",
    )
    clock["now"] = 61.0

    second_voice = store.get_or_create(
        session_id=None,
        user_id="jordan",
        source="web",
        channel_key="jordan:dashboard.voice",
    )

    assert store.channel_status("jordan:dashboard.command") is None
    assert second_voice.session_id != first_voice.session_id
