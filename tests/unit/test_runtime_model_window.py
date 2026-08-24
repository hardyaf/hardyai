from app.core.state_machine import RuntimePowerController
from app.core.types import SessionOwner


def test_larger_models_inactive_with_no_tasks():
    power = RuntimePowerController(larger_model_micro_only_window_seconds=180.0, time_fn=lambda: 0.0)
    status = power.model_runtime_status()
    assert status["larger_models_active"] is False
    assert status["task_count"] == 0


def test_larger_models_activate_when_main_labeled_task_present():
    now = [0.0]

    def _time() -> float:
        return now[0]

    power = RuntimePowerController(larger_model_micro_only_window_seconds=180.0, time_fn=_time)
    changed = power.record_task_label(SessionOwner.MAIN)

    assert changed is True
    assert power.larger_models_active() is True
    status = power.model_runtime_status()
    assert status["main_labeled_count"] == 1
    assert status["micro_labeled_count"] == 0


def test_larger_models_cool_down_after_micro_only_window():
    now = [0.0]

    def _time() -> float:
        return now[0]

    power = RuntimePowerController(larger_model_micro_only_window_seconds=180.0, time_fn=_time)
    power.record_task_label(SessionOwner.MAIN)
    assert power.larger_models_active() is True

    now[0] = 60.0
    power.record_task_label(SessionOwner.MICRO)
    assert power.larger_models_active() is True

    now[0] = 245.0
    power.record_task_label(SessionOwner.MICRO)
    assert power.larger_models_active() is False


def test_system_labels_are_ignored():
    power = RuntimePowerController(larger_model_micro_only_window_seconds=180.0, time_fn=lambda: 0.0)
    changed = power.record_task_label(SessionOwner.SYSTEM)
    assert changed is False
    assert power.larger_models_active() is False


def test_runtime_transition_hook_fires_when_main_cools_down():
    now = [0.0]
    transitions: list[tuple[bool, bool]] = []

    def _time() -> float:
        return now[0]

    power = RuntimePowerController(
        larger_model_micro_only_window_seconds=180.0,
        time_fn=_time,
        transition_hook=lambda previous, current: transitions.append((previous, current)),
    )
    power.record_task_label(SessionOwner.MAIN)
    assert transitions == [(False, True)]

    now[0] = 181.0
    status = power.model_runtime_status()
    assert status["larger_models_active"] is False
    assert transitions == [(False, True), (True, False)]
