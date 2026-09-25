from __future__ import annotations

from pathlib import Path

import pytest

from app.jobs.repository import DurableJobRepository
from app.tasks.repository import TaskConflictError, TaskRepository
from app.tasks.service import TaskApplicationService
from app.tasks.worker import AgentTaskWorker


def _runtime(tmp_path: Path, *, decisions: int = 8):
    database = tmp_path / "core.db"
    repository = TaskRepository(str(database))
    jobs = DurableJobRepository(str(database))
    service = TaskApplicationService(
        repository=repository,
        jobs=jobs,
        workspace_root=str(tmp_path / "workspaces"),
        initial_budget_seconds=300,
        initial_model_decisions=decisions,
        initial_capability_calls=20,
    )
    return repository, jobs, service


def test_task_creation_is_idempotent_and_owner_workspace_is_scoped(tmp_path):
    repository, _jobs, service = _runtime(tmp_path)
    first = service.create_task(
        owner_id="operator",
        source_interface="task_workspace",
        submission_id="submission-0001",
        goal="Create a useful artifact",
    )
    replay = service.create_task(
        owner_id="operator",
        source_interface="task_workspace",
        submission_id="submission-0001",
        goal="Create a useful artifact",
    )

    assert first["created"] is True
    assert replay["created"] is False
    assert replay["task"]["task_id"] == first["task"]["task_id"]
    assert first["task"]["workspace_ref"].startswith("owners/")
    assert "operator" not in first["task"]["workspace_ref"]
    assert repository.list_messages(task_id=first["task"]["task_id"])[0]["content"] == (
        "Create a useful artifact"
    )


def test_effect_receipt_replays_only_same_bound_operation(tmp_path):
    repository, _jobs, service = _runtime(tmp_path)
    task = service.create_task(
        owner_id="operator",
        source_interface="task_workspace",
        submission_id="submission-0002",
        goal="Write once",
    )["task"]
    first, created = repository.reserve_effect(
        task_id=task["task_id"],
        logical_operation_id="create-item-1",
        provider_operation_id="provider-op-1",
        tool_id="lists.add_items",
        arguments_hash="a" * 64,
        run_id=None,
    )
    replay, replay_created = repository.reserve_effect(
        task_id=task["task_id"],
        logical_operation_id="create-item-1",
        provider_operation_id="provider-op-1",
        tool_id="lists.add_items",
        arguments_hash="a" * 64,
        run_id=None,
    )
    assert created is True
    assert replay_created is False
    assert replay["receipt_id"] == first["receipt_id"]
    with pytest.raises(TaskConflictError, match="task_effect_operation_conflict"):
        repository.reserve_effect(
            task_id=task["task_id"],
            logical_operation_id="create-item-1",
            provider_operation_id="provider-op-2",
            tool_id="lists.add_items",
            arguments_hash="b" * 64,
            run_id=None,
        )


def test_preferences_and_instruction_only_skills_are_versioned_and_reversible(tmp_path):
    repository, _jobs, _service = _runtime(tmp_path)
    preference = repository.save_preference(
        owner_id="operator",
        scope="general",
        rule_text="Use concise bullets.",
        source_instruction="Remember this.",
    )
    revised = repository.save_preference(
        owner_id="operator",
        preference_id=preference["preference_id"],
        scope="general",
        rule_text="Use compact tables when comparing items.",
        source_instruction="Use tables from now on.",
    )
    restored = repository.restore_preference(
        owner_id="operator",
        preference_id=preference["preference_id"],
        revision=1,
    )
    skill = repository.save_skill_revision(
        owner_id="operator",
        skill_id="skill.weekly_review",
        title="Weekly review",
        instructions_markdown="Load Calendar, then group events by week.",
        source_instruction="Create this procedure.",
    )

    assert revised["revision"] == 2
    assert restored["revision"] == 3
    assert restored["rule_text"] == "Use concise bullets."
    assert skill["revision"] == 1
    assert repository.get_user_skill(
        owner_id="operator", skill_id="skill.weekly_review"
    )["instructions_markdown"].startswith("Load Calendar")
    assert repository.retire_user_skill(
        owner_id="operator", skill_id="skill.weekly_review"
    ) is True
    assert repository.get_user_skill(
        owner_id="operator", skill_id="skill.weekly_review"
    ) is None


class _Model:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, **_kwargs):
        return {
            "message": self.responses.pop(0),
            "model": "fixture",
            "done_reason": "stop",
            "prompt_eval_count": 1,
            "eval_count": 1,
        }


class _Capabilities:
    def discover(self, **_kwargs):
        return {"status": "ok", "skills": []}


class _CodeRunner:
    def run(self, **_kwargs):
        raise AssertionError("code runner was not expected")


def _call(name, arguments):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
    }


def test_budget_pause_and_continue_keep_same_task_and_plan(tmp_path):
    repository, jobs, service = _runtime(tmp_path, decisions=1)
    task = service.create_task(
        owner_id="operator",
        source_interface="task_workspace",
        submission_id="submission-0003",
        goal="Plan and finish",
    )["task"]
    first_worker = AgentTaskWorker(
        repository=repository,
        jobs=jobs,
        model=_Model(
            [_call("update_plan", {"plan_markdown": "1. Inspect\n2. Finish", "progress_summary": "Planned"})]
        ),
        capabilities=_Capabilities(),
        code_runner=_CodeRunner(),
        poll_seconds=0.1,
    )
    first = first_worker.run_once()[0]
    paused = repository.get_task(task_id=task["task_id"])
    assert first["status"] == "paused_budget"
    assert paused["task_id"] == task["task_id"]
    assert paused["plan_markdown"].startswith("1. Inspect")

    continued = service.continue_task(
        owner_id="operator",
        task_id=task["task_id"],
        expected_revision=paused["revision"],
        submission_id="continuation-0001",
        add_seconds=60,
        add_model_decisions=2,
        add_capability_calls=0,
    )["task"]
    second_worker = AgentTaskWorker(
        repository=repository,
        jobs=jobs,
        model=_Model([_call("finish_task", {"result": "Finished with evidence.", "summary": "Done"})]),
        capabilities=_Capabilities(),
        code_runner=_CodeRunner(),
        poll_seconds=0.1,
    )
    second = second_worker.run_once()[0]
    completed = repository.get_task(task_id=task["task_id"])
    assert continued["task_id"] == task["task_id"]
    assert second["status"] == "completed"
    assert completed["final_result"] == "Finished with evidence."
    assert completed["plan_markdown"].startswith("1. Inspect")


def test_owner_pause_wins_over_stale_worker_transition(tmp_path):
    repository, jobs, service = _runtime(tmp_path)
    task = service.create_task(
        owner_id="operator",
        source_interface="task_workspace",
        submission_id="submission-0004",
        goal="Do not complete after I pause",
    )["task"]
    job = jobs.claim_jobs(
        job_type="agent.task.v1",
        worker_id="fixture-worker",
        limit=1,
        lease_seconds=60,
    )[0]
    running = repository.begin_run(
        task_id=task["task_id"],
        job_id=job["job_id"],
        generation=task["run_generation"],
    )
    paused = repository.pause(
        task_id=task["task_id"],
        owner_id="operator",
        expected_revision=running["revision"],
    )

    with pytest.raises(TaskConflictError, match="steering_changed"):
        repository.worker_transition(
            task_id=task["task_id"],
            status="completed",
            event_type="task.completed",
            final_result="stale result",
            expected_steering_revision=running["steering_revision"],
        )
    assert repository.get_task(task_id=task["task_id"])["status"] == "paused_user"
    assert paused["steering_revision"] == running["steering_revision"] + 1


def test_continue_rejects_a_task_that_is_already_running(tmp_path):
    repository, jobs, service = _runtime(tmp_path)
    task = service.create_task(
        owner_id="operator",
        source_interface="task_workspace",
        submission_id="submission-0005",
        goal="Keep one execution generation",
    )["task"]
    job = jobs.claim_jobs(
        job_type="agent.task.v1",
        worker_id="fixture-worker",
        limit=1,
        lease_seconds=60,
    )[0]
    running = repository.begin_run(
        task_id=task["task_id"],
        job_id=job["job_id"],
        generation=task["run_generation"],
    )
    with pytest.raises(TaskConflictError, match="task_not_resumable"):
        service.continue_task(
            owner_id="operator",
            task_id=task["task_id"],
            expected_revision=running["revision"],
            submission_id="continuation-while-running",
        )


def test_restart_context_closes_incomplete_native_tool_batch(tmp_path):
    repository, jobs, service = _runtime(tmp_path)
    task = service.create_task(
        owner_id="operator",
        source_interface="task_workspace",
        submission_id="submission-0006",
        goal="Recover the tool transcript",
    )["task"]
    repository.append_message(
        task_id=task["task_id"],
        role="assistant",
        content="",
        tool_calls=[
            {"id": "call-1", "function": {"name": "discover_capabilities", "arguments": {}}},
            {"id": "call-2", "function": {"name": "update_plan", "arguments": {}}},
        ],
        metadata={},
    )
    repository.append_message(
        task_id=task["task_id"],
        role="tool",
        content='{"status":"ok"}',
        tool_name="discover_capabilities",
        tool_call_id="call-1",
        metadata={},
    )
    worker = AgentTaskWorker(
        repository=repository,
        jobs=jobs,
        model=_Model([]),
        capabilities=_Capabilities(),
        code_runner=_CodeRunner(),
    )

    messages = worker._model_messages(repository.get_task(task_id=task["task_id"]))

    assert messages[-1]["role"] == "tool"
    assert messages[-1]["tool_call_id"] == "call-2"
    assert "worker_restarted_before_observation" in messages[-1]["content"]


def test_workspace_resolution_and_owner_queries_do_not_cross_boundaries(tmp_path):
    repository, _jobs, service = _runtime(tmp_path)
    task = service.create_task(
        owner_id="owner-a",
        source_interface="task_workspace",
        submission_id="submission-0007",
        goal="Keep this private",
    )["task"]

    assert service.get_task_detail(owner_id="owner-b", task_id=task["task_id"]) is None
    assert service.artifact_path(
        owner_id="owner-b", task_id=task["task_id"], artifact_name="result.txt"
    ) is None
    with pytest.raises(ValueError, match="workspace_ref_invalid"):
        service.resolve_workspace("../owner-b/task")
