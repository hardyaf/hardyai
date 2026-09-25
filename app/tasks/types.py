from __future__ import annotations

from enum import StrEnum


AGENT_TASK_JOB = "agent.task.v1"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED_BUDGET = "paused_budget"
    PAUSED_USER = "paused_user"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value}
)

PAUSED_TASK_STATUSES = frozenset(
    {
        TaskStatus.PAUSED_BUDGET.value,
        TaskStatus.PAUSED_USER.value,
        TaskStatus.WAITING_INPUT.value,
        TaskStatus.WAITING_APPROVAL.value,
    }
)
