from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.api.operator_auth import require_operator
from app.api.principals import RequestPrincipal
from app.dependencies import get_task_repository, get_task_service
from app.dependencies import get_job_repository
from app.jobs.repository import DurableJobRepository
from app.tasks.repository import TaskConflictError, TaskRepository
from app.tasks.service import TaskApplicationService


router = APIRouter(prefix="/api", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    submission_id: str = Field(min_length=8, max_length=160)
    goal: str = Field(min_length=1, max_length=40_000)
    title: str | None = Field(default=None, max_length=160)
    session_id: str | None = Field(default=None, max_length=160)
    budget_seconds: float | None = Field(default=None, ge=1, le=86_400)
    model_decisions: int | None = Field(default=None, ge=1, le=256)
    capability_calls: int | None = Field(default=None, ge=1, le=1000)


class RevisionRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class TaskMessageRequest(RevisionRequest):
    submission_id: str = Field(min_length=8, max_length=160)
    content: str = Field(min_length=1, max_length=40_000)


class ContinueTaskRequest(RevisionRequest):
    submission_id: str = Field(min_length=8, max_length=160)
    add_seconds: float | None = Field(default=None, ge=0, le=86_400)
    add_model_decisions: int | None = Field(default=None, ge=0, le=256)
    add_capability_calls: int | None = Field(default=None, ge=0, le=1000)


class PreferenceRequest(BaseModel):
    scope: Literal["general", "project", "skill"]
    rule_text: str = Field(min_length=1, max_length=4_000)
    source_instruction: str = Field(min_length=1, max_length=4_000)
    skill_id: str | None = Field(default=None, max_length=160)
    preference_id: str | None = Field(default=None, max_length=160)


class RestoreRequest(BaseModel):
    revision: int = Field(ge=1)


class SkillRequest(BaseModel):
    skill_id: str = Field(pattern=r"^skill\.[a-z0-9][a-z0-9_.-]{0,153}$")
    title: str = Field(min_length=1, max_length=160)
    instructions_markdown: str = Field(min_length=1, max_length=40_000)
    source_instruction: str = Field(min_length=1, max_length=4_000)
    base_skill_id: str | None = Field(default=None, max_length=160)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="task_not_found")


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.post("/tasks", status_code=202)
def create_task(
    payload: CreateTaskRequest,
    principal: RequestPrincipal = Depends(require_operator),
    service: TaskApplicationService = Depends(get_task_service),
) -> dict:
    try:
        return service.create_task(
            owner_id=principal.user_id,
            source_interface="task_workspace",
            submission_id=payload.submission_id,
            goal=payload.goal,
            title=payload.title,
            session_id=payload.session_id,
            budget_seconds=payload.budget_seconds,
            model_decisions=payload.model_decisions,
            capability_calls=payload.capability_calls,
        )
    except (TaskConflictError, ValueError) as exc:
        raise _conflict(exc) from exc


@router.get("/tasks")
def list_tasks(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    principal: RequestPrincipal = Depends(require_operator),
    service: TaskApplicationService = Depends(get_task_service),
) -> dict:
    try:
        tasks = service.list_tasks(owner_id=principal.user_id, status=status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="task_status_invalid") from exc
    return {"tasks": tasks}


@router.get("/task-runtime")
def task_runtime(
    _: RequestPrincipal = Depends(require_operator),
    jobs: DurableJobRepository = Depends(get_job_repository),
) -> dict:
    return {
        "worker": jobs.get_worker_heartbeat("agent_task"),
        "queued_jobs": jobs.list_jobs(job_type="agent.task.v1", limit=100),
    }


@router.get("/tasks/{task_id}")
def get_task(
    task_id: str,
    principal: RequestPrincipal = Depends(require_operator),
    service: TaskApplicationService = Depends(get_task_service),
) -> dict:
    detail = service.get_task_detail(owner_id=principal.user_id, task_id=task_id)
    if detail is None:
        raise _not_found()
    detail["artifacts"] = service.published_artifacts(
        owner_id=principal.user_id, task_id=task_id
    )
    return detail


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: str,
    after: int = Query(default=0, ge=0),
    principal: RequestPrincipal = Depends(require_operator),
    repository: TaskRepository = Depends(get_task_repository),
) -> StreamingResponse:
    if repository.get_task(task_id=task_id, owner_id=principal.user_id) is None:
        raise _not_found()

    async def stream() -> AsyncIterator[str]:
        cursor = after
        idle = 0
        while idle < 300:
            events = await asyncio.to_thread(
                repository.list_events,
                task_id=task_id,
                owner_id=principal.user_id,
                after_event_id=cursor,
                limit=200,
            )
            if events:
                idle = 0
                for event in events:
                    cursor = max(cursor, int(event["event_id"]))
                    data = json.dumps(event, ensure_ascii=True, separators=(",", ":"))
                    yield f"id: {cursor}\nevent: task\ndata: {data}\n\n"
            else:
                idle += 1
                if idle % 15 == 0:
                    yield ": keepalive\n\n"
                await asyncio.sleep(1.0)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/tasks/{task_id}/messages", status_code=202)
def add_task_message(
    task_id: str,
    payload: TaskMessageRequest,
    principal: RequestPrincipal = Depends(require_operator),
    service: TaskApplicationService = Depends(get_task_service),
) -> dict:
    try:
        return service.add_message(
            owner_id=principal.user_id,
            task_id=task_id,
            expected_revision=payload.expected_revision,
            submission_id=payload.submission_id,
            content=payload.content,
        )
    except KeyError as exc:
        raise _not_found() from exc
    except (TaskConflictError, ValueError) as exc:
        raise _conflict(exc) from exc


@router.post("/tasks/{task_id}/pause")
def pause_task(
    task_id: str,
    payload: RevisionRequest,
    principal: RequestPrincipal = Depends(require_operator),
    service: TaskApplicationService = Depends(get_task_service),
) -> dict:
    try:
        return service.pause(
            owner_id=principal.user_id,
            task_id=task_id,
            expected_revision=payload.expected_revision,
        )
    except KeyError as exc:
        raise _not_found() from exc
    except (TaskConflictError, ValueError) as exc:
        raise _conflict(exc) from exc


@router.post("/tasks/{task_id}/cancel")
def cancel_task(
    task_id: str,
    payload: RevisionRequest,
    principal: RequestPrincipal = Depends(require_operator),
    service: TaskApplicationService = Depends(get_task_service),
) -> dict:
    try:
        return service.cancel(
            owner_id=principal.user_id,
            task_id=task_id,
            expected_revision=payload.expected_revision,
        )
    except KeyError as exc:
        raise _not_found() from exc
    except (TaskConflictError, ValueError) as exc:
        raise _conflict(exc) from exc


@router.post("/tasks/{task_id}/continue", status_code=202)
def continue_task(
    task_id: str,
    payload: ContinueTaskRequest,
    principal: RequestPrincipal = Depends(require_operator),
    service: TaskApplicationService = Depends(get_task_service),
) -> dict:
    try:
        return service.continue_task(
            owner_id=principal.user_id,
            task_id=task_id,
            expected_revision=payload.expected_revision,
            submission_id=payload.submission_id,
            add_seconds=payload.add_seconds,
            add_model_decisions=payload.add_model_decisions,
            add_capability_calls=payload.add_capability_calls,
        )
    except KeyError as exc:
        raise _not_found() from exc
    except (TaskConflictError, ValueError) as exc:
        raise _conflict(exc) from exc


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(
    task_id: str,
    expected_revision: int = Query(ge=1),
    principal: RequestPrincipal = Depends(require_operator),
    service: TaskApplicationService = Depends(get_task_service),
) -> None:
    try:
        deleted = service.delete_task(
            owner_id=principal.user_id,
            task_id=task_id,
            expected_revision=expected_revision,
        )
    except (TaskConflictError, ValueError) as exc:
        raise _conflict(exc) from exc
    if not deleted:
        raise _not_found()


@router.get("/tasks/{task_id}/artifacts/{artifact_name}")
def download_artifact(
    task_id: str,
    artifact_name: str,
    principal: RequestPrincipal = Depends(require_operator),
    service: TaskApplicationService = Depends(get_task_service),
) -> FileResponse:
    path = service.artifact_path(
        owner_id=principal.user_id, task_id=task_id, artifact_name=artifact_name
    )
    if path is None:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    return FileResponse(path, filename=path.name)


@router.get("/preferences")
def list_preferences(
    principal: RequestPrincipal = Depends(require_operator),
    repository: TaskRepository = Depends(get_task_repository),
) -> dict:
    return {"preferences": repository.list_preferences(owner_id=principal.user_id)}


@router.post("/preferences")
def save_preference(
    payload: PreferenceRequest,
    principal: RequestPrincipal = Depends(require_operator),
    repository: TaskRepository = Depends(get_task_repository),
) -> dict:
    try:
        return repository.save_preference(owner_id=principal.user_id, **payload.model_dump())
    except (TaskConflictError, ValueError) as exc:
        raise _conflict(exc) from exc


@router.get("/preferences/{preference_id}/history")
def preference_history(
    preference_id: str,
    principal: RequestPrincipal = Depends(require_operator),
    repository: TaskRepository = Depends(get_task_repository),
) -> dict:
    return {
        "history": repository.preference_history(
            owner_id=principal.user_id, preference_id=preference_id
        )
    }


@router.post("/preferences/{preference_id}/restore")
def restore_preference(
    preference_id: str,
    payload: RestoreRequest,
    principal: RequestPrincipal = Depends(require_operator),
    repository: TaskRepository = Depends(get_task_repository),
) -> dict:
    try:
        return repository.restore_preference(
            owner_id=principal.user_id,
            preference_id=preference_id,
            revision=payload.revision,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/preferences/{preference_id}", status_code=204)
def retire_preference(
    preference_id: str,
    principal: RequestPrincipal = Depends(require_operator),
    repository: TaskRepository = Depends(get_task_repository),
) -> None:
    if not repository.retire_preference(
        owner_id=principal.user_id, preference_id=preference_id
    ):
        raise HTTPException(status_code=404, detail="preference_not_found")


@router.get("/skills")
def list_skills(
    principal: RequestPrincipal = Depends(require_operator),
    repository: TaskRepository = Depends(get_task_repository),
) -> dict:
    return {"skills": repository.list_user_skills(owner_id=principal.user_id)}


@router.post("/skills")
def save_skill(
    payload: SkillRequest,
    principal: RequestPrincipal = Depends(require_operator),
    repository: TaskRepository = Depends(get_task_repository),
) -> dict:
    try:
        return repository.save_skill_revision(owner_id=principal.user_id, **payload.model_dump())
    except (TaskConflictError, ValueError) as exc:
        raise _conflict(exc) from exc


@router.get("/skills/{skill_id}/history")
def skill_history(
    skill_id: str,
    principal: RequestPrincipal = Depends(require_operator),
    repository: TaskRepository = Depends(get_task_repository),
) -> dict:
    return {
        "history": repository.skill_history(owner_id=principal.user_id, skill_id=skill_id)
    }


@router.get("/skills/{skill_id}/export", response_class=PlainTextResponse)
def export_skill(
    skill_id: str,
    principal: RequestPrincipal = Depends(require_operator),
    repository: TaskRepository = Depends(get_task_repository),
) -> str:
    skill = repository.get_user_skill(owner_id=principal.user_id, skill_id=skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill_not_found")
    return str(skill["instructions_markdown"])


@router.post("/skills/{skill_id}/restore")
def restore_skill(
    skill_id: str,
    payload: RestoreRequest,
    principal: RequestPrincipal = Depends(require_operator),
    repository: TaskRepository = Depends(get_task_repository),
) -> dict:
    try:
        return repository.restore_user_skill(
            owner_id=principal.user_id, skill_id=skill_id, revision=payload.revision
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/skills/{skill_id}", status_code=204)
def retire_skill(
    skill_id: str,
    principal: RequestPrincipal = Depends(require_operator),
    repository: TaskRepository = Depends(get_task_repository),
) -> None:
    if not repository.retire_user_skill(owner_id=principal.user_id, skill_id=skill_id):
        raise HTTPException(status_code=404, detail="skill_not_found")
