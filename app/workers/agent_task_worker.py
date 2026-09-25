from __future__ import annotations

from app.config import settings
from app.services.offline_runtime_policy import validate_offline_runtime
from app.tasks.worker import AgentTaskWorker, install_signal_handlers


def main() -> int:
    if not settings.task_workspace_enabled:
        raise RuntimeError("TASK_WORKSPACE_ENABLED must be true to run the task worker.")
    validate_offline_runtime(settings, entrypoint="agent-task-worker")
    from app.runtime import task_service, task_worker

    if task_service is None or task_worker is None:
        raise RuntimeError("Task workspace runtime is not configured.")
    task_service.recover_queued()
    install_signal_handlers(task_worker)
    task_worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
