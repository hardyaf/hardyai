from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any

from app.jobs.repository import DurableJobRepository
from app.jobs.types import JobStatus, ResourceClass
from app.tasks.repository import TaskRepository
from app.tasks.types import AGENT_TASK_JOB, TaskStatus


class TaskApplicationService:
    """Transport-neutral task command/query service over Core SQLite and durable jobs."""

    def __init__(
        self,
        *,
        repository: TaskRepository,
        jobs: DurableJobRepository,
        workspace_root: str,
        initial_budget_seconds: float = 300.0,
        initial_model_decisions: int = 32,
        initial_capability_calls: int = 100,
        max_budget_seconds: float = 86_400.0,
    ) -> None:
        self.repository = repository
        self.jobs = jobs
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.initial_budget_seconds = max(1.0, float(initial_budget_seconds))
        self.initial_model_decisions = max(1, int(initial_model_decisions))
        self.initial_capability_calls = max(1, int(initial_capability_calls))
        self.max_budget_seconds = max(self.initial_budget_seconds, float(max_budget_seconds))

    @staticmethod
    def _owner_ref(owner_id: str) -> str:
        return hashlib.sha256(str(owner_id).encode("utf-8")).hexdigest()[:20]

    def _workspace_ref(self, *, owner_id: str, task_id: str) -> str:
        return f"owners/{self._owner_ref(owner_id)}/tasks/{task_id}"

    def resolve_workspace(self, workspace_ref: str) -> Path:
        normalized = str(workspace_ref or "").replace("\\", "/").strip("/")
        if not normalized or ".." in normalized.split("/"):
            raise ValueError("task_workspace_ref_invalid")
        candidate = (self.workspace_root / normalized).resolve()
        if self.workspace_root not in candidate.parents:
            raise ValueError("task_workspace_escape")
        return candidate

    def ensure_workspace(self, workspace_ref: str) -> Path:
        root = self.resolve_workspace(workspace_ref)
        for relative in ("runs", "published", "inputs"):
            (root / relative).mkdir(parents=True, exist_ok=True)
        return root

    def _enqueue(self, task: dict[str, Any]) -> dict[str, Any]:
        generation = int(task["run_generation"])
        job = self.jobs.enqueue_job(
            job_type=AGENT_TASK_JOB,
            aggregate_id=str(task["task_id"]),
            idempotency_key=f"agent-task:v1:{task['task_id']}:{generation}",
            payload={
                "task_id": str(task["task_id"]),
                "run_generation": generation,
            },
            max_attempts=8,
            priority=50,
            resource_class=ResourceClass.CPU_SMALL.value,
        )
        if str(job.get("status") or "") in {
            JobStatus.COMPLETED.value,
            JobStatus.DEAD_LETTER.value,
            JobStatus.CANCELLED.value,
        }:
            task = self.repository.advance_queued_generation(
                task_id=str(task["task_id"]), expected_generation=generation
            )
            return self._enqueue(task)
        self.repository.set_active_job(
            task_id=str(task["task_id"]),
            job_id=str(job["job_id"]),
            generation=generation,
        )
        return job

    def create_task(
        self,
        *,
        owner_id: str,
        source_interface: str,
        submission_id: str,
        goal: str,
        title: str | None = None,
        session_id: str | None = None,
        budget_seconds: float | None = None,
        model_decisions: int | None = None,
        capability_calls: int | None = None,
    ) -> dict[str, Any]:
        # Task ID is generated in the repository, so use a stable pending workspace marker first,
        # then bind the final task-specific reference before any worker can claim the job.
        provisional = "pending/" + hashlib.sha256(
            f"{owner_id}\n{submission_id}".encode("utf-8")
        ).hexdigest()[:32]
        task, created = self.repository.create_task(
            owner_id=owner_id,
            source_interface=source_interface,
            submission_id=submission_id,
            goal=goal,
            title=title or str(goal).strip()[:100],
            session_id=session_id,
            workspace_ref=provisional,
            budget_seconds=min(
                self.max_budget_seconds,
                max(1.0, float(budget_seconds or self.initial_budget_seconds)),
            ),
            model_decisions=max(1, int(model_decisions or self.initial_model_decisions)),
            capability_calls=max(1, int(capability_calls or self.initial_capability_calls)),
        )
        if created:
            workspace_ref = self._workspace_ref(
                owner_id=owner_id, task_id=str(task["task_id"])
            )
            task = self.repository.set_workspace_ref(
                task_id=str(task["task_id"]), workspace_ref=workspace_ref
            )
            self.ensure_workspace(workspace_ref)
            self._enqueue(task)
            task = self.repository.get_task(task_id=str(task["task_id"])) or task
        return {"task": task, "created": created}

    def recover_queued(self) -> int:
        recovered = 0
        for task in self.repository.queued_tasks():
            job = self._enqueue(task)
            if str(job.get("status")) in {
                JobStatus.PENDING.value,
                JobStatus.RETRY.value,
                JobStatus.RUNNING.value,
            }:
                recovered += 1
        return recovered

    def list_tasks(self, *, owner_id: str, status: str | None, limit: int) -> list[dict[str, Any]]:
        if status:
            TaskStatus(status)
        return self.repository.list_tasks(owner_id=owner_id, status=status, limit=limit)

    def get_task_detail(self, *, owner_id: str, task_id: str) -> dict[str, Any] | None:
        task = self.repository.get_task(task_id=task_id, owner_id=owner_id)
        if task is None:
            return None
        return {
            "task": task,
            "messages": self.repository.list_messages(task_id=task_id),
            "events": self.repository.list_events(
                task_id=task_id,
                owner_id=owner_id,
                after_event_id=0,
                limit=1000,
            ),
            "effects": self.repository.list_effects(task_id=task_id),
            "script_runs": self.repository.list_script_runs(task_id=task_id),
        }

    def add_message(
        self,
        *,
        owner_id: str,
        task_id: str,
        expected_revision: int,
        submission_id: str,
        content: str,
    ) -> dict[str, Any]:
        task, created = self.repository.add_user_message(
            task_id=task_id,
            owner_id=owner_id,
            expected_revision=expected_revision,
            submission_id=submission_id,
            content=content,
        )
        if created and str(task.get("status")) == TaskStatus.QUEUED.value:
            self._enqueue(task)
            task = self.repository.get_task(task_id=task_id, owner_id=owner_id) or task
        return {"task": task, "accepted": created}

    def pause(self, *, owner_id: str, task_id: str, expected_revision: int) -> dict[str, Any]:
        task = self.repository.pause(
            task_id=task_id,
            owner_id=owner_id,
            expected_revision=expected_revision,
        )
        active_job_id = str(task.get("active_job_id") or "")
        if active_job_id:
            self.jobs.request_cancel(job_id=active_job_id)
        return task

    def cancel(self, *, owner_id: str, task_id: str, expected_revision: int) -> dict[str, Any]:
        task = self.repository.cancel(
            task_id=task_id,
            owner_id=owner_id,
            expected_revision=expected_revision,
        )
        active_job_id = str(task.get("active_job_id") or "")
        if active_job_id:
            self.jobs.request_cancel(job_id=active_job_id)
        return task

    def continue_task(
        self,
        *,
        owner_id: str,
        task_id: str,
        expected_revision: int,
        submission_id: str,
        add_seconds: float | None = None,
        add_model_decisions: int | None = None,
        add_capability_calls: int | None = None,
    ) -> dict[str, Any]:
        task, created = self.repository.continue_task(
            task_id=task_id,
            owner_id=owner_id,
            expected_revision=expected_revision,
            submission_id=submission_id,
            add_seconds=min(
                self.max_budget_seconds,
                max(0.0, float(add_seconds if add_seconds is not None else self.initial_budget_seconds)),
            ),
            add_model_decisions=max(
                0,
                int(
                    add_model_decisions
                    if add_model_decisions is not None
                    else self.initial_model_decisions
                ),
            ),
            add_capability_calls=max(
                0,
                int(
                    add_capability_calls
                    if add_capability_calls is not None
                    else self.initial_capability_calls
                ),
            ),
        )
        if created:
            self._enqueue(task)
            task = self.repository.get_task(task_id=task_id, owner_id=owner_id) or task
        return {"task": task, "accepted": created}

    def published_artifacts(self, *, owner_id: str, task_id: str) -> list[dict[str, Any]]:
        task = self.repository.get_task(task_id=task_id, owner_id=owner_id)
        if task is None:
            return []
        published = self.resolve_workspace(str(task["workspace_ref"])) / "published"
        artifacts: list[dict[str, Any]] = []
        if not published.exists():
            return artifacts
        for path in sorted(published.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(published).as_posix()
            if ".." in relative.split("/"):
                continue
            size = path.stat().st_size
            artifacts.append({"path": relative, "size": size})
            if len(artifacts) >= 200:
                break
        return artifacts

    def artifact_path(self, *, owner_id: str, task_id: str, artifact_name: str) -> Path | None:
        task = self.repository.get_task(task_id=task_id, owner_id=owner_id)
        safe_name = self.safe_artifact_name(artifact_name)
        if task is None or safe_name != artifact_name:
            return None
        published = self.resolve_workspace(str(task["workspace_ref"])) / "published"
        candidate = (published / safe_name).resolve()
        if candidate.parent != published.resolve() or not candidate.is_file() or candidate.is_symlink():
            return None
        return candidate

    def delete_task(self, *, owner_id: str, task_id: str, expected_revision: int) -> bool:
        task = self.repository.get_task(task_id=task_id, owner_id=owner_id)
        if task is None:
            return False
        if int(task["revision"]) != int(expected_revision):
            raise ValueError("task_revision_conflict")
        active_job_id = str(task.get("active_job_id") or "")
        if active_job_id:
            job = self.jobs.get_job(active_job_id) or {}
            if str(job.get("status") or "") not in {
                JobStatus.COMPLETED.value,
                JobStatus.DEAD_LETTER.value,
                JobStatus.CANCELLED.value,
            }:
                raise ValueError("task_job_active")
        workspace = self.resolve_workspace(str(task["workspace_ref"]))
        deleted = self.repository.delete_task(owner_id=owner_id, task_id=task_id)
        if deleted and workspace.exists():
            shutil.rmtree(workspace)
        return deleted

    @staticmethod
    def safe_artifact_name(value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
        return normalized.strip(".-")[:160]
