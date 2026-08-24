from __future__ import annotations

from collections import deque
from time import monotonic
from typing import Callable

from app.core.types import FAST_COMMAND_INTENTS, Intent, PowerState, SessionOwner, SessionState


class RuntimePowerController:
    def __init__(
        self,
        larger_model_micro_only_window_seconds: float = 180.0,
        time_fn: Callable[[], float] | None = None,
        transition_hook: Callable[[bool, bool], None] | None = None,
    ) -> None:
        self._state = PowerState.AWAKE
        self._larger_model_window_seconds = max(float(larger_model_micro_only_window_seconds), 1.0)
        self._time_fn = time_fn or monotonic
        self._task_labels: deque[tuple[float, SessionOwner]] = deque()
        self._larger_models_active = False
        self._transition_hook = transition_hook

    @property
    def state(self) -> PowerState:
        return self._state

    def is_awake(self) -> bool:
        return self._state == PowerState.AWAKE

    def wake(self) -> None:
        self._state = PowerState.AWAKE

    def sleep(self) -> None:
        self._state = PowerState.ASLEEP
        self.reset_model_activity()

    @property
    def larger_model_window_seconds(self) -> float:
        return self._larger_model_window_seconds

    def reset_model_activity(self) -> None:
        self._task_labels.clear()
        self._larger_models_active = False

    def set_model_runtime_transition_hook(self, hook: Callable[[bool, bool], None] | None) -> None:
        self._transition_hook = hook

    def record_task_label(self, owner: SessionOwner | str) -> bool:
        normalized_owner = self._coerce_owner(owner)
        if normalized_owner not in {SessionOwner.MICRO, SessionOwner.MAIN}:
            return False
        now = self._time_fn()
        self._task_labels.append((now, normalized_owner))
        return self._recompute_larger_models_active(now)

    def larger_models_active(self) -> bool:
        now = self._time_fn()
        self._recompute_larger_models_active(now)
        return self._larger_models_active

    def model_runtime_status(self) -> dict[str, object]:
        now = self._time_fn()
        self._recompute_larger_models_active(now)
        micro_labeled_count = sum(1 for _, owner in self._task_labels if owner == SessionOwner.MICRO)
        main_labeled_count = sum(1 for _, owner in self._task_labels if owner == SessionOwner.MAIN)
        return {
            "larger_models_active": self._larger_models_active,
            "window_seconds": self._larger_model_window_seconds,
            "task_count": len(self._task_labels),
            "micro_labeled_count": micro_labeled_count,
            "main_labeled_count": main_labeled_count,
        }

    @staticmethod
    def _coerce_owner(owner: SessionOwner | str) -> SessionOwner | None:
        if isinstance(owner, SessionOwner):
            return owner
        normalized = str(owner).strip().lower()
        for candidate in SessionOwner:
            if candidate.value == normalized:
                return candidate
        return None

    def _prune_expired_labels(self, now: float) -> None:
        while self._task_labels and (now - self._task_labels[0][0]) > self._larger_model_window_seconds:
            self._task_labels.popleft()

    def _recompute_larger_models_active(self, now: float) -> bool:
        self._prune_expired_labels(now)
        previous = self._larger_models_active
        self._larger_models_active = any(owner == SessionOwner.MAIN for _, owner in self._task_labels)
        changed = previous != self._larger_models_active
        if changed and self._transition_hook is not None:
            try:
                self._transition_hook(previous, self._larger_models_active)
            except Exception:
                # Runtime transitions should never break request routing.
                pass
        return changed


def choose_owner_for_intent(intent: Intent, recommended_owner: SessionOwner) -> SessionOwner:
    if intent in {Intent.SYSTEM_WAKE, Intent.SYSTEM_SLEEP}:
        return SessionOwner.SYSTEM
    if intent in {Intent.CONVERSATIONAL, Intent.UNKNOWN}:
        # Non-tool conversational turns are always main-owned.
        return SessionOwner.MAIN
    if intent in FAST_COMMAND_INTENTS:
        if recommended_owner in {SessionOwner.MAIN, SessionOwner.MICRO}:
            return recommended_owner
        return SessionOwner.MICRO
    return recommended_owner


def next_state_for_owner_intent(owner: SessionOwner, intent: Intent) -> SessionState:
    if owner == SessionOwner.MICRO and intent in FAST_COMMAND_INTENTS:
        return SessionState.FAST_COMMAND
    if owner == SessionOwner.MAIN:
        return SessionState.CONVERSATIONAL
    return SessionState.IDLE
