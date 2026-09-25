from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.tasks.capabilities import TaskCapabilityBridge
from app.tasks.code_runner.broker import TaskCapabilityBroker
from app.tasks.code_runner.client import TaskRunnerLauncherClient
from app.tasks.repository import TaskRepository
from app.tasks.service import TaskApplicationService


class TaskCodeRunner:
    """Prepares immutable inputs and executes them through the trusted launcher."""

    def __init__(
        self,
        *,
        repository: TaskRepository,
        task_service: TaskApplicationService,
        capabilities: TaskCapabilityBridge,
        launcher: TaskRunnerLauncherClient,
        timeout_seconds: float = 30.0,
        max_source_chars: int = 100_000,
    ) -> None:
        self._repository = repository
        self._task_service = task_service
        self._capabilities = capabilities
        self._launcher = launcher
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 300.0))
        self._max_source_chars = max(1_000, min(int(max_source_chars), 500_000))

    def run(
        self,
        *,
        task: dict[str, Any],
        agent_id: str,
        logical_run_id: str,
        source: str,
        expected_steering_revision: int,
    ) -> dict[str, Any]:
        code = str(source or "")
        if not code.strip() or len(code) > self._max_source_chars:
            return {"status": "error", "error_code": "python_source_invalid"}
        workspace_ref = str(task["workspace_ref"])
        task_root = self._task_service.ensure_workspace(workspace_ref)
        prepared, created = self._repository.create_script_run(
            task_id=str(task["task_id"]),
            logical_run_id=str(logical_run_id or "").strip(),
            source=code,
            source_ref="pending",
            workspace_ref=workspace_ref,
            steering_revision=expected_steering_revision,
        )
        run_id = str(prepared["run_id"])
        run_root = task_root / "runs" / run_id
        input_root = run_root / "input"
        work_root = run_root / "work"
        ipc_root = run_root / "ipc"
        for path in (input_root, work_root, ipc_root, task_root / "published"):
            path.mkdir(parents=True, exist_ok=True)
        os.chmod(run_root, 0o755)
        os.chmod(work_root, 0o777)
        os.chmod(task_root / "published", 0o777)
        source_path = input_root / "source.py"
        if created:
            with source_path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(code)
            os.chmod(source_path, 0o444)
        elif str(prepared.get("status")) in {"completed", "failed", "timed_out"}:
            replay = dict(prepared)
            script_status = str(prepared.get("status"))
            replay["status"] = "ok" if script_status == "completed" else "error"
            replay["script_status"] = script_status
            replay["replayed"] = True
            return replay

        self._repository.mark_script_running(run_id=run_id)
        broker_token = secrets.token_urlsafe(32)
        ordinal = 0

        def handle(request: dict[str, Any]) -> dict[str, Any]:
            nonlocal ordinal
            current = self._repository.get_task(task_id=str(task["task_id"]))
            if current is None or str(current.get("status")) != "running":
                return {"status": "interrupted", "error_code": "task_not_running"}
            if int(current.get("steering_revision") or 0) != int(expected_steering_revision):
                return {"status": "interrupted", "error_code": "task_steering_changed"}
            active_job = self._task_service.jobs.get_job(
                str(current.get("active_job_id") or "")
            )
            lease_expires = str((active_job or {}).get("lease_expires_at") or "")
            try:
                lease_current = datetime.fromisoformat(lease_expires).astimezone(UTC) > datetime.now(UTC)
            except (TypeError, ValueError):
                lease_current = False
            if (
                not active_job
                or str(active_job.get("status")) != "running"
                or active_job.get("cancel_requested_at")
                or not lease_current
            ):
                return {"status": "interrupted", "error_code": "task_worker_lease_lost"}
            action = str(request.get("action") or "")
            if action == "describe":
                return self._capabilities.describe(
                    task=current,
                    agent_id=agent_id,
                    tool_id=str(request.get("tool_id") or ""),
                )
            if action != "call":
                return {"status": "error", "error_code": "broker_action_invalid"}
            arguments = request.get("arguments")
            if not isinstance(arguments, dict):
                return {"status": "error", "error_code": "arguments_invalid"}
            ordinal += 1
            return self._capabilities.call(
                task=current,
                agent_id=agent_id,
                tool_id=str(request.get("tool_id") or ""),
                contract_version=int(request.get("contract_version") or 1),
                arguments=arguments,
                logical_operation_id=str(request.get("logical_operation_id") or "") or None,
                call_ordinal=ordinal,
                expected_steering_revision=expected_steering_revision,
                run_id=run_id,
            )

        ipc_fd: int | None = None
        try:
            # AF_UNIX pathname sockets are limited to roughly 108 bytes on Linux.
            # Bind through an open directory descriptor so deeply nested task
            # workspaces still create the socket inside the exact IPC directory
            # mounted by the trusted launcher.
            ipc_fd = os.open(ipc_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            socket_path = Path(f"/proc/self/fd/{ipc_fd}/broker.sock")
            with TaskCapabilityBroker(
                socket_path=socket_path,
                token=broker_token,
                handler=handle,
            ):
                result = self._launcher.run(
                    task_id=str(task["task_id"]),
                    run_id=run_id,
                    workspace_ref=workspace_ref,
                    broker_token=broker_token,
                    timeout_seconds=self._timeout_seconds,
                )
        except Exception as exc:
            result = {
                "status": "error",
                "error_code": "task_runner_unavailable",
                "stderr": type(exc).__name__,
                "exit_code": None,
            }
        finally:
            if ipc_fd is not None:
                os.close(ipc_fd)
        artifacts = self._task_service.published_artifacts(
            owner_id=str(task["owner_id"]), task_id=str(task["task_id"])
        )
        boundary = self._repository.get_task(task_id=str(task["task_id"]))
        interrupted = (
            boundary is None
            or str(boundary.get("status") or "") != "running"
            or int(boundary.get("steering_revision") or 0)
            != int(expected_steering_revision)
        )
        script_status = "interrupted" if interrupted else "completed"
        if not interrupted and result.get("timed_out") is True:
            script_status = "timed_out"
        elif not interrupted and (
            int(result.get("exit_code") or 0) != 0 or result.get("status") != "ok"
        ):
            script_status = "failed"
        persisted = self._repository.finish_script_run(
            run_id=run_id,
            status=script_status,
            exit_code=result.get("exit_code"),
            stdout_text=str(result.get("stdout") or ""),
            stderr_text=str(result.get("stderr") or ""),
            artifacts=artifacts,
        )
        response_status = "ok" if script_status == "completed" else "error"
        if script_status == "interrupted":
            response_status = "interrupted"
        return {
            "status": response_status,
            "run_id": run_id,
            "script_status": script_status,
            "exit_code": persisted.get("exit_code"),
            "stdout": persisted.get("stdout_text", ""),
            "stderr": persisted.get("stderr_text", ""),
            "artifacts": artifacts,
            **(
                {"error_code": "task_steering_changed"}
                if script_status == "interrupted"
                else {}
            ),
        }
