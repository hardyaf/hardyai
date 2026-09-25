from __future__ import annotations

from app.config import settings
from app.container import ApplicationContainer
from app.services.offline_runtime_policy import validate_offline_runtime
from app.tasks.worker import install_signal_handlers


def main() -> int:
    if not settings.task_workspace_enabled:
        raise RuntimeError("TASK_WORKSPACE_ENABLED must be true to run the task worker.")
    validate_offline_runtime(settings, entrypoint="agent-task-worker")
    container = ApplicationContainer.from_default_runtime()
    if container.task_service is None or container.task_worker is None:
        raise RuntimeError("Task workspace runtime is not configured.")
    container.task_service.recover_queued()
    install_signal_handlers(container.task_worker)
    container.task_worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
