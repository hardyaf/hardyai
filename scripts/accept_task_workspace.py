#!/usr/bin/env python3
"""Run the bounded integrated acceptance campaign for the task workspace.

The script is intentionally stdlib-only and writes content-minimized evidence. It is
meant for an isolated candidate Compose project; production use requires an explicit
flag. Provider fixture names must start with ``ACCEPTANCE-``.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


TERMINAL_OR_PAUSED = {
    "completed",
    "failed",
    "cancelled",
    "paused_budget",
    "paused_user",
    "waiting_input",
    "waiting_approval",
}


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _operator_key(env_path: Path) -> str:
    values = _load_env(env_path)
    direct = values.get("JARVIS_OPERATOR_API_KEY", "").strip()
    if direct:
        return direct
    key_file = values.get("JARVIS_OPERATOR_API_KEY_FILE", "").strip()
    if key_file:
        return Path(key_file).read_text(encoding="utf-8").strip()
    raise RuntimeError("operator_key_not_configured")


class Api:
    def __init__(self, base_url: str, key: str, *, cookies: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.cookies = cookies
        self.csrf = ""
        handlers: list[Any] = []
        if cookies:
            handlers.append(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.opener = urllib.request.build_opener(*handlers)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        use_session: bool = False,
        timeout: float = 30.0,
    ) -> Any:
        encoded = None if body is None else json.dumps(body).encode("utf-8")
        headers: dict[str, str] = {}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        if use_session:
            if method not in {"GET", "HEAD", "OPTIONS"}:
                headers["X-CSRF-Token"] = self.csrf
        else:
            headers["X-Jarvis-Operator-Key"] = self.key
        request = urllib.request.Request(
            self.base_url + path,
            data=encoded,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                data = response.read()
                if not data:
                    return None
                content_type = response.headers.get("Content-Type", "")
                if "json" in content_type:
                    return json.loads(data.decode("utf-8"))
                return data
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2_000]
            raise RuntimeError(f"http_{exc.code}:{path}:{detail}") from exc

    def login(self) -> None:
        if not self.cookies:
            raise RuntimeError("cookie_client_required")
        payload = self.request("/operator/session", method="POST")
        self.csrf = str(payload.get("csrf_token") or "")
        if not self.csrf:
            raise AssertionError("operator_session_missing_csrf")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _wait_task(
    api: Api,
    task_id: str,
    *,
    wanted: set[str] = TERMINAL_OR_PAUSED,
    timeout: float = 900.0,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        detail = api.request(f"/api/tasks/{task_id}")
        status = str(detail["task"]["status"])
        marker = f"{status}:{detail['task'].get('revision')}"
        if marker != last:
            print(f"task {task_id[:8]} -> {marker}", flush=True)
            last = marker
        if predicate is not None and predicate(detail):
            return detail
        if predicate is None and status in wanted:
            return detail
        time.sleep(1.5)
    raise TimeoutError(f"task_wait_timeout:{task_id}:{last}")


def _create_task(
    api: Api,
    goal: str,
    *,
    title: str,
    seconds: float = 300,
    decisions: int = 24,
    calls: int = 100,
) -> dict[str, Any]:
    result = api.request(
        "/api/tasks",
        method="POST",
        body={
            "submission_id": str(uuid4()),
            "goal": goal,
            "title": title,
            "budget_seconds": seconds,
            "model_decisions": decisions,
            "capability_calls": calls,
        },
    )
    _assert(result.get("created") is True, "task_not_created")
    return result["task"]


def _tool_observations(detail: dict[str, Any]) -> list[dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    output: list[dict[str, Any]] = []
    for message in detail.get("messages", []):
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict) or not call.get("id"):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                calls[str(call["id"])] = function
        if message.get("role") != "tool":
            continue
        try:
            result = json.loads(str(message.get("content") or "{}"))
        except json.JSONDecodeError:
            result = {"status": "invalid_json"}
        function = calls.get(str(message.get("tool_call_id") or ""), {})
        arguments = function.get("arguments") if isinstance(function.get("arguments"), dict) else {}
        output.append(
            {
                "task_tool": str(function.get("name") or message.get("tool_name") or ""),
                "capability": str(arguments.get("tool_id") or ""),
                "result": result,
            }
        )
    return output


def _task_evidence(detail: dict[str, Any]) -> dict[str, Any]:
    task = detail["task"]
    observations = _tool_observations(detail)
    return {
        "task_id": task["task_id"],
        "status": task["status"],
        "revision": task["revision"],
        "plan_present": bool(str(task.get("plan_markdown") or "").strip()),
        "final_present": bool(str(task.get("final_result") or "").strip()),
        "event_types": [str(item.get("event_type") or "") for item in detail.get("events", [])],
        "task_tools": [item["task_tool"] for item in observations],
        "capabilities": [item["capability"] for item in observations if item["capability"]],
        "effects": [
            {
                "tool_id": item.get("tool_id"),
                "logical_operation_id": item.get("logical_operation_id"),
                "state": item.get("state"),
                "result_status": (item.get("result") or {}).get("status"),
            }
            for item in detail.get("effects", [])
        ],
        "script_statuses": [str(item.get("status") or "") for item in detail.get("script_runs", [])],
        "artifacts": detail.get("artifacts", []),
        "budget": task.get("budget"),
    }


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _fixture_events(detail: dict[str, Any], title: str) -> list[dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for observation in _tool_observations(detail):
        if observation["capability"] not in {"calendar.query_events", "calendar.get_event"}:
            continue
        for item in _walk(observation["result"]):
            if not isinstance(item, dict) or str(item.get("title") or "") != title:
                continue
            start = str(item.get("start") or "")
            ref = str(item.get("event_ref") or "")
            if start:
                found[(ref, start)] = item
    return [found[key] for key in sorted(found, key=lambda value: value[1])]


def _artifact_json(api: Api, task_id: str, name: str) -> dict[str, Any]:
    raw = api.request(f"/api/tasks/{task_id}/artifacts/{name}")
    parsed = raw if isinstance(raw, dict) else json.loads(raw.decode("utf-8"))
    _assert(isinstance(parsed, dict), f"artifact_not_object:{name}")
    return parsed


def _validate_existing_learning(api: Api, label: str, task_id: str) -> dict[str, Any]:
    suffix = "".join(character for character in label.casefold() if character.isalnum())[-16:]
    preference_id = f"acceptance-pref-{suffix}"
    skill_id = f"skill.acceptance.weekly_{suffix}"
    detail = api.request(f"/api/tasks/{task_id}")
    _assert(detail["task"]["status"] == "completed", "learning_task_not_completed")
    _assert("Fixture complete." in str(detail["task"].get("final_result") or ""), "preference_not_applied")
    _assert("acceptance-weekly-checklist.json" in {item["path"] for item in detail["artifacts"]}, "skill_artifact_missing")
    artifact = _artifact_json(api, task_id, "acceptance-weekly-checklist.json")
    _assert("Acceptance Week Plan" in json.dumps(artifact), "skill_answer_not_used")
    tools = [item["task_tool"] for item in _tool_observations(detail)]
    for expected in ("discover_capabilities", "load_skill", "ask_user", "run_python"):
        _assert(expected in tools, f"learning_tool_missing:{expected}")
    preference_history = api.request(f"/api/preferences/{preference_id}/history")["history"]
    skill_history = api.request(f"/api/skills/{skill_id}/history")["history"]
    _assert(len(preference_history) >= 3, "preference_history_missing")
    _assert(len(skill_history) >= 3, "skill_history_missing")
    return {
        "preference_revisions": len(preference_history),
        "skill_revisions": len(skill_history),
        "task": _task_evidence(detail),
    }


def _run_learning(api: Api, label: str) -> tuple[dict[str, Any], dict[str, str]]:
    print("stage learning", flush=True)
    suffix = "".join(character for character in label.casefold() if character.isalnum())[-16:]
    preference_id = f"acceptance-pref-{suffix}"
    first = api.request(
        "/api/preferences",
        method="POST",
        body={
            "scope": "general",
            "rule_text": "For acceptance tasks, use compact Markdown bullets and end with `Fixture complete.`",
            "source_instruction": "Explicit acceptance preference.",
            "preference_id": preference_id,
        },
    )
    second = api.request(
        "/api/preferences",
        method="POST",
        body={
            "scope": "general",
            "rule_text": "For acceptance tasks, use a numbered paragraph.",
            "source_instruction": "Temporary acceptance revision.",
            "preference_id": preference_id,
        },
    )
    history = api.request(f"/api/preferences/{preference_id}/history")["history"]
    restored = api.request(
        f"/api/preferences/{preference_id}/restore",
        method="POST",
        body={"revision": int(first["revision"])},
    )
    _assert(int(restored["revision"]) > int(second["revision"]), "preference_restore_not_versioned")

    skill_id = f"skill.acceptance.weekly_{suffix}"
    instructions = (
        "# Acceptance weekly checklist\n\n"
        "Always ask the user for the checklist heading with `ask_user` before processing. "
        "After the reply, call `run_python` to group the supplied dated facts by ISO week. The "
        "Python source must import `published_path` from `jarvis_task_api` and write JSON containing "
        "the chosen heading and grouped facts to "
        "`published_path('acceptance-weekly-checklist.json')`; a file in the work directory alone "
        "is not a published artifact. Do not call provider tools. Do not finish until the tool result "
        "lists that artifact. Finish with compact Markdown bullets and the exact text "
        "`Fixture complete.`"
    )
    skill_first = api.request(
        "/api/skills",
        method="POST",
        body={
            "skill_id": skill_id,
            "title": "Acceptance weekly checklist",
            "instructions_markdown": instructions,
            "source_instruction": "Explicit acceptance skill.",
        },
    )
    skill_second = api.request(
        "/api/skills",
        method="POST",
        body={
            "skill_id": skill_id,
            "title": "Acceptance weekly checklist temporary revision",
            "instructions_markdown": "Do not use this temporary revision.",
            "source_instruction": "Temporary acceptance revision.",
        },
    )
    skill_history = api.request(f"/api/skills/{skill_id}/history")["history"]
    skill_restored = api.request(
        f"/api/skills/{skill_id}/restore",
        method="POST",
        body={"revision": int(skill_first["revision"])},
    )
    _assert(int(skill_restored["revision"]) > int(skill_second["revision"]), "skill_restore_not_versioned")

    task = _create_task(
        api,
        (
            f"Discover and load the instruction-only skill `{skill_id}`. Use it to turn these facts "
            "into a weekly checklist: 2026-09-29 field setup; 2026-10-01 bring cones; "
            "2026-10-06 roster check. Follow the skill exactly; the heading is intentionally absent. "
            "The published artifact is required, and you must not finish if it is absent."
        ),
        title=f"{label} learning",
        decisions=16,
        calls=10,
    )
    waiting = _wait_task(api, task["task_id"], wanted={"waiting_input", "completed", "failed"})
    _assert(waiting["task"]["status"] == "waiting_input", "instruction_skill_did_not_ask")
    resumed = api.request(
        f"/api/tasks/{task['task_id']}/messages",
        method="POST",
        body={
            "expected_revision": waiting["task"]["revision"],
            "submission_id": str(uuid4()),
            "content": "Use the heading `Acceptance Week Plan` and continue.",
        },
    )
    _assert(resumed.get("accepted") is True, "question_answer_not_accepted")
    completed = _wait_task(api, task["task_id"], wanted={"completed", "failed", "paused_budget"})
    _assert(completed["task"]["status"] == "completed", "learning_task_not_completed")
    final = str(completed["task"].get("final_result") or "")
    _assert("Fixture complete." in final, "preference_not_applied")
    _assert("acceptance-weekly-checklist.json" in {item["path"] for item in completed["artifacts"]}, "skill_artifact_missing")
    artifact = _artifact_json(api, task["task_id"], "acceptance-weekly-checklist.json")
    _assert("Acceptance Week Plan" in json.dumps(artifact), "skill_answer_not_used")
    evidence = {
        "preference_revisions": len(history),
        "preference_restored_revision": restored["revision"],
        "skill_revisions": len(skill_history),
        "skill_restored_revision": skill_restored["revision"],
        "task": _task_evidence(completed),
    }
    return evidence, {"preference_id": preference_id, "skill_id": skill_id}


def _run_budget(api: Api, label: str) -> dict[str, Any]:
    print("stage budget", flush=True)
    task = _create_task(
        api,
        (
            "First call update_plan with a concise two-step plan. Do not call any other tool in the "
            "same response. On the next decision, finish with three compact bullets and the marker "
            "BUDGET-FINISHED. Do not use Python or provider capabilities."
        ),
        title=f"{label} budget",
        decisions=1,
        calls=2,
    )
    paused = _wait_task(api, task["task_id"], wanted={"paused_budget", "completed", "failed"})
    _assert(paused["task"]["status"] == "paused_budget", "budget_did_not_pause")
    _assert(bool(str(paused["task"].get("plan_markdown") or "").strip()), "budget_plan_missing")
    steered = api.request(
        f"/api/tasks/{task['task_id']}/messages",
        method="POST",
        body={
            "expected_revision": paused["task"]["revision"],
            "submission_id": str(uuid4()),
            "content": "Redirect the remaining work: include the marker REDIRECTED-BUDGET in the final result.",
        },
    )["task"]
    user_paused = api.request(
        f"/api/tasks/{task['task_id']}/pause",
        method="POST",
        body={"expected_revision": steered["revision"]},
    )
    _assert(user_paused["status"] == "paused_user", "user_pause_failed")
    continued = api.request(
        f"/api/tasks/{task['task_id']}/continue",
        method="POST",
        body={
            "expected_revision": user_paused["revision"],
            "submission_id": str(uuid4()),
            "add_seconds": 120,
            "add_model_decisions": 5,
            "add_capability_calls": 2,
        },
    )
    _assert(continued.get("accepted") is True, "budget_continue_not_accepted")
    completed = _wait_task(api, task["task_id"], wanted={"completed", "failed", "paused_budget"})
    _assert(completed["task"]["status"] == "completed", "budget_task_not_completed")
    _assert("REDIRECTED-BUDGET" in str(completed["task"].get("final_result") or ""), "budget_redirect_missing")
    _assert(completed["task"]["task_id"] == task["task_id"], "budget_task_identity_changed")
    return _task_evidence(completed)


def _restart_worker(container: str) -> None:
    _assert(container.startswith("jarvis-taskws-accept-"), "unsafe_worker_container_name")
    subprocess.run(["docker", "restart", container], check=True, stdout=subprocess.DEVNULL)


def _run_interrupt(api: Api, label: str, worker_container: str) -> dict[str, Any]:
    print("stage interrupted-script", flush=True)
    task = _create_task(
        api,
        (
            "Publish a plan, then call run_python with logical_run_id `acceptance-interrupt`. "
            "The Python source must import time, sleep for 20 seconds, and then print "
            "`obsolete-direction-finished`. Do not discover or call provider capabilities. After the "
            "program returns, finish. This run will be redirected while it is sleeping."
        ),
        title=f"{label} interrupted script",
        seconds=240,
        decisions=12,
        calls=5,
    )
    running = _wait_task(
        api,
        task["task_id"],
        timeout=420,
        predicate=lambda detail: any(
            item.get("status") == "running" for item in detail.get("script_runs", [])
        )
        or detail["task"]["status"] in TERMINAL_OR_PAUSED,
    )
    _assert(any(item.get("status") == "running" for item in running.get("script_runs", [])), "script_never_ran")
    steered = api.request(
        f"/api/tasks/{task['task_id']}/messages",
        method="POST",
        body={
            "expected_revision": running["task"]["revision"],
            "submission_id": str(uuid4()),
            "content": "Redirect now: the sleeping program is obsolete. Do not rerun it; finish by reporting INTERRUPT-RECOVERED.",
        },
    )["task"]
    paused = api.request(
        f"/api/tasks/{task['task_id']}/pause",
        method="POST",
        body={"expected_revision": steered["revision"]},
    )
    _assert(paused["status"] == "paused_user", "interrupt_pause_failed")
    interrupted = _wait_task(
        api,
        task["task_id"],
        timeout=90,
        predicate=lambda detail: any(
            item.get("status") == "interrupted" for item in detail.get("script_runs", [])
        ),
    )
    _restart_worker(worker_container)
    time.sleep(4)
    cookie_client = Api(api.base_url, api.key, cookies=True)
    cookie_client.login()
    reloaded = cookie_client.request(f"/api/tasks/{task['task_id']}", use_session=True)
    _assert(reloaded["task"]["status"] == "paused_user", "browser_restart_state_lost")
    continued = cookie_client.request(
        f"/api/tasks/{task['task_id']}/continue",
        method="POST",
        use_session=True,
        body={
            "expected_revision": reloaded["task"]["revision"],
            "submission_id": str(uuid4()),
            "add_seconds": 120,
            "add_model_decisions": 5,
            "add_capability_calls": 2,
        },
    )
    _assert(continued.get("accepted") is True, "interrupt_continue_not_accepted")
    completed = _wait_task(api, task["task_id"], wanted={"completed", "failed", "paused_budget"})
    _assert(completed["task"]["status"] == "completed", "interrupt_task_not_completed")
    _assert("INTERRUPT-RECOVERED" in str(completed["task"].get("final_result") or ""), "interrupt_redirect_missing")
    _assert("interrupted" in [item.get("status") for item in interrupted["script_runs"]], "interrupted_run_missing")
    return _task_evidence(completed)


def _run_lists_documents_python(api: Api, label: str) -> dict[str, Any]:
    print("stage lists-documents-python", flush=True)
    list_name = f"{label} bounded runner"
    artifact_name = "acceptance-boundary.json"
    goal = f"""
Complete this acceptance workflow using the Lists and Documents capabilities and bounded Python.

1. Create or reuse the exact personal list `{list_name}`. Ensure it contains exactly these four
fixture items without duplicating an existing item: inspect calendar, group weeks, pack cones,
publish checklist.
2. Use the configured Documents read path only: call documents.status, then search for the unique
text `{label}-NO-MATCH`; report only status and result count. Do not upload, reprocess, or retain
document contents.
3. Run bounded Python with logical_run_id `acceptance-boundary`. In that code, use
jarvis_task_api.call for a Lists read, and write `{artifact_name}` with
jarvis_task_api.published_path. The JSON must contain the Lists read status plus these booleans:
`network_blocked`, after a <=1 second socket connection attempt to 1.1.1.1:53;
`secrets_absent`, after checking that /run/secrets does not exist;
`readonly_root`, after a failed write under /etc; and `workspace_escape_blocked`, after
published_path('../escape.json') raises. Never print environment variables or file contents.
4. Read the list again, verify the four fixture items, and finish with evidence.
""".strip()
    task = _create_task(api, goal, title=f"{label} composed capability task", seconds=480, decisions=24, calls=80)
    completed = _wait_task(api, task["task_id"], wanted={"completed", "failed", "paused_budget", "waiting_input"})
    return _validate_lists_documents(api, completed)


def _validate_lists_documents(api: Api, completed: dict[str, Any]) -> dict[str, Any]:
    _assert(completed["task"]["status"] == "completed", "lists_documents_task_not_completed")
    artifact = _artifact_json(
        api, completed["task"]["task_id"], "acceptance-boundary.json"
    )
    for field in ("network_blocked", "secrets_absent", "readonly_root", "workspace_escape_blocked"):
        _assert(artifact.get(field) is True, f"runner_boundary_failed:{field}")
    capabilities = [item["capability"] for item in _tool_observations(completed)]
    _assert(any(value.startswith("lists.") for value in capabilities), "lists_capability_missing")
    _assert("documents.status" in capabilities, "documents_status_missing")
    _assert("documents.search" in capabilities, "documents_search_missing")
    return _task_evidence(completed)


def _resume_composed_task(api: Api, task_id: str) -> dict[str, Any]:
    detail = api.request(f"/api/tasks/{task_id}")
    if detail["task"]["status"] == "paused_user":
        continued = api.request(
            f"/api/tasks/{task_id}/continue",
            method="POST",
            body={
                "expected_revision": detail["task"]["revision"],
                "submission_id": str(uuid4()),
                "add_seconds": 0,
                "add_model_decisions": 0,
                "add_capability_calls": 0,
            },
        )
        _assert(continued.get("accepted") is True, "composed_user_continue_not_accepted")
        detail = api.request(f"/api/tasks/{task_id}")
    if detail["task"]["status"] == "waiting_input":
        api.request(
            f"/api/tasks/{task_id}/messages",
            method="POST",
            body={
                "expected_revision": detail["task"]["revision"],
                "submission_id": str(uuid4()),
                "content": (
                    "The existing authorized Documents skill is `skill.documents.local`. "
                    "Rediscover and load it, then continue the original task without asking again."
                ),
            },
        )
    completed = _wait_task(
        api,
        task_id,
        wanted={"completed", "failed", "paused_budget", "waiting_input"},
        timeout=1_200,
    )
    if completed["task"]["status"] == "paused_budget":
        steered = api.request(
            f"/api/tasks/{task_id}/messages",
            method="POST",
            body={
                "expected_revision": completed["task"]["revision"],
                "submission_id": str(uuid4()),
                "content": (
                    "Do not retry Documents status: its denial without a bound document is valid "
                    "evidence, and Documents search already succeeded. Do not rediscover or reload "
                    "skills. Complete only the remaining bounded Python boundary check, including "
                    "the Lists read through jarvis_task_api and the published acceptance-boundary.json "
                    "artifact, then finish with evidence."
                ),
            },
        )["task"]
        continued = api.request(
            f"/api/tasks/{task_id}/continue",
            method="POST",
            body={
                "expected_revision": steered["revision"],
                "submission_id": str(uuid4()),
                "add_seconds": 900,
                "add_model_decisions": 8,
                "add_capability_calls": 10,
            },
        )
        _assert(continued.get("accepted") is True, "composed_budget_continue_not_accepted")
        completed = _wait_task(
            api,
            task_id,
            wanted={"completed", "failed", "paused_budget", "waiting_input"},
            timeout=1_200,
        )
    return _validate_lists_documents(api, completed)


def _run_calendar(api: Api, label: str) -> tuple[dict[str, Any], str]:
    print("stage calendar", flush=True)
    fixture_title = f"{label} AYSO Recurrence"
    artifact_name = "calendar-checklist.json"
    goal = f"""
Use the Calendar skill and bounded Python for this non-inviting acceptance fixture.

1. Query the default calendar for the complete local day 2026-10-06 in America/New_York using
start `2026-10-06T00:00:00-04:00`, end `2026-10-07T00:00:00-04:00`, time_basis
`local_calendar`, oldest first, and limit 2. Omit the optional text argument entirely for this
unfiltered query; never send an empty optional string. Separately query from
`2026-09-29T00:00:00-04:00` through the exclusive end `2026-11-06T00:00:00-05:00` for text
`AYSO`, time_basis `local_calendar`, oldest first, limit 100. These must be two calls to the same
query capability with different arguments. Note truncation/coverage truthfully.
2. Create exactly one event series titled `{fixture_title}` on the default calendar, with no
attendees or invitees. First event: `2026-09-29T18:00:00-04:00` through
`2026-09-29T19:00:00-04:00`, timezone `America/New_York`. Recurrence: weekly,
interval 1, count 12, by weekdays TU and TH. Use logical operation id
`acceptance-calendar-create-{label.casefold()}`.
3. Query the exact fixture title over 2026-09-29 through exclusive end 2026-11-06 with limit 100
and verify all 12 expanded occurrences, including 18:00 local time after daylight-saving changes.
Read one exact occurrence back with calendar.get_event.
4. Use bounded Python to publish `{artifact_name}` containing only the fixture title, the twelve
expected ISO dates grouped into six weeks, and the verified occurrence count. Do not copy any
unrelated calendar titles into the artifact.
5. Finish with the fixture event reference, first start, verified count, and no-invitation evidence.
""".strip()
    task = _create_task(api, goal, title=f"{label} calendar recurrence", seconds=600, decisions=32, calls=100)
    completed = _wait_task(api, task["task_id"], wanted={"completed", "failed", "paused_budget", "waiting_input", "waiting_approval"}, timeout=1_200)
    return _validate_calendar(api, completed, fixture_title, artifact_name)


def _validate_calendar(
    api: Api,
    completed: dict[str, Any],
    fixture_title: str,
    artifact_name: str = "calendar-checklist.json",
) -> tuple[dict[str, Any], str]:
    _assert(completed["task"]["status"] == "completed", "calendar_task_not_completed")
    observations = _tool_observations(completed)
    query_calls = [item for item in observations if item["capability"] == "calendar.query_events"]
    _assert(len(query_calls) >= 3, "calendar_query_composition_missing")
    events = _fixture_events(completed, fixture_title)
    _assert(len(events) == 12, f"calendar_occurrence_count:{len(events)}")
    starts = [str(item.get("start") or "") for item in events]
    _assert(any("-05:00" in value and "T18:00" in value for value in starts), "calendar_dst_local_time_missing")
    create_effects = [
        item for item in completed.get("effects", [])
        if item.get("tool_id") == "calendar.create_event" and item.get("state") == "committed"
    ]
    _assert(len(create_effects) == 1, "calendar_create_effect_not_exactly_once")
    create_result = create_effects[0].get("result") or {}
    _assert("@" not in json.dumps(create_result.get("payload", {})), "calendar_fixture_contains_attendee")
    artifact = _artifact_json(api, task["task_id"], artifact_name)
    _assert(int(artifact.get("verified_occurrence_count", artifact.get("occurrence_count", 0))) == 12, "calendar_artifact_count_invalid")
    return _task_evidence(completed), fixture_title


def _resume_calendar_task(api: Api, task_id: str, label: str) -> tuple[dict[str, Any], str]:
    detail = api.request(f"/api/tasks/{task_id}")
    if detail["task"]["status"] == "waiting_input":
        api.request(
            f"/api/tasks/{task_id}/messages",
            method="POST",
            body={
                "expected_revision": detail["task"]["revision"],
                "submission_id": str(uuid4()),
                "content": (
                    "The existing Google Calendar account has been reauthorized and both Calendar "
                    "Events and Gmail read-only were verified. Retry the original Calendar workflow "
                    "now. Reuse all prior observations and do not ask again."
                ),
            },
        )
    completed = _wait_task(
        api,
        task_id,
        wanted={"completed", "failed", "paused_budget", "waiting_input", "waiting_approval"},
        timeout=1_200,
    )
    return _validate_calendar(
        api,
        completed,
        f"{label} AYSO Recurrence",
    )


def _run_calendar_cleanup(api: Api, label: str, fixture_title: str) -> dict[str, Any]:
    print("stage calendar-cleanup-approval", flush=True)
    task = _create_task(
        api,
        (
            f"Query the default calendar from 2026-09-29 through exclusive end 2026-11-06 for the "
            f"exact title `{fixture_title}`. Select only that clearly labeled fixture. Delete its "
            "entire recurring series with edit_scope series and stable logical operation id "
            f"`acceptance-calendar-cleanup-{label.casefold()}`. This exact deletion is authorized, "
            "but follow the local approval flow. Never delete any other event. After approval, "
            "verify the fixture no longer appears and finish."
        ),
        title=f"{label} calendar cleanup",
        seconds=420,
        decisions=24,
        calls=50,
    )
    waiting = _wait_task(api, task["task_id"], wanted={"waiting_approval", "completed", "failed", "paused_budget"}, timeout=900)
    _assert(waiting["task"]["status"] == "waiting_approval", "calendar_cleanup_not_waiting_approval")
    pending = next(
        (
            item for item in waiting.get("effects", [])
            if item.get("tool_id") == "calendar.delete_event" and item.get("state") == "waiting_approval"
        ),
        None,
    )
    _assert(pending is not None, "calendar_cleanup_effect_missing")
    result = pending.get("result") or {}
    review_id = str(result.get("review_id") or "")
    proposal_id = str(result.get("proposal_id") or "")
    _assert(bool(review_id and proposal_id), "calendar_cleanup_review_binding_missing")
    review = api.request(f"/reviews/{review_id}")["review"]
    decision = api.request(
        f"/reviews/{review_id}/local-action-decision",
        method="POST",
        body={
            "proposal_id": proposal_id,
            "decision": "approve",
            "bound_proposal_hash": review["item_hash"],
            "reason": "Approve deletion of the exact non-inviting acceptance fixture.",
            "idempotency_key": str(uuid4()),
        },
    )
    _assert(bool(decision), "calendar_cleanup_decision_missing")
    deadline = time.monotonic() + 180
    proposal_state = ""
    while time.monotonic() < deadline:
        proposal = api.request(f"/reviews/action-proposals/{proposal_id}")["proposal"]
        proposal_state = str(proposal.get("state") or "")
        if proposal_state in {"executed", "failed_terminal", "denied", "rejected"}:
            break
        time.sleep(1.5)
    _assert(proposal_state == "executed", f"calendar_cleanup_execution:{proposal_state}")
    current = api.request(f"/api/tasks/{task['task_id']}")
    api.request(
        f"/api/tasks/{task['task_id']}/continue",
        method="POST",
        body={
            "expected_revision": current["task"]["revision"],
            "submission_id": str(uuid4()),
            "add_seconds": 180,
            "add_model_decisions": 8,
            "add_capability_calls": 20,
        },
    )
    completed = _wait_task(api, task["task_id"], wanted={"completed", "failed", "paused_budget", "waiting_approval"}, timeout=600)
    _assert(completed["task"]["status"] == "completed", "calendar_cleanup_task_not_completed")
    effects = [item for item in completed.get("effects", []) if item.get("tool_id") == "calendar.delete_event"]
    _assert(len(effects) == 1 and effects[0].get("state") == "committed", "calendar_cleanup_effect_not_committed_once")
    return _task_evidence(completed)


def _run_cancel(api: Api, label: str) -> dict[str, Any]:
    print("stage cancel", flush=True)
    task = _create_task(
        api,
        "Publish a plan, then wait for user input before doing anything else.",
        title=f"{label} cancellation",
        seconds=120,
        decisions=8,
        calls=2,
    )
    current = api.request(f"/api/tasks/{task['task_id']}")
    cancelled = api.request(
        f"/api/tasks/{task['task_id']}/cancel",
        method="POST",
        body={"expected_revision": current["task"]["revision"]},
    )
    _assert(cancelled["status"] == "cancelled", "task_cancel_failed")
    return _task_evidence(api.request(f"/api/tasks/{task['task_id']}"))


def _static_checks(api: Api) -> dict[str, Any]:
    print("stage interface", flush=True)
    health = api.request("/health")
    root = api.request("/")
    workspace = api.request("/workspace")
    _assert(isinstance(root, bytes) and b"Jarvis Task Workspace" in root, "root_workspace_missing")
    _assert(isinstance(workspace, bytes) and b"/api/tasks" in workspace, "workspace_api_missing")
    unauthenticated = urllib.request.Request(api.base_url + "/api/tasks")
    try:
        urllib.request.urlopen(unauthenticated, timeout=10)
    except urllib.error.HTTPError as exc:
        _assert(exc.code == 401, f"unauthenticated_status:{exc.code}")
    else:
        raise AssertionError("unauthenticated_task_api_allowed")
    runtime = api.request("/api/task-runtime")
    _assert(runtime.get("worker") is not None, "task_worker_heartbeat_missing")
    return {
        "health_status": health.get("status") if isinstance(health, dict) else None,
        "root_bytes": len(root),
        "workspace_bytes": len(workspace),
        "worker_heartbeat_present": True,
        "unauthenticated_status": 401,
    }


def _cleanup_learning(api: Api, identifiers: dict[str, str]) -> None:
    api.request(f"/api/preferences/{identifiers['preference_id']}", method="DELETE")
    api.request(f"/api/skills/{identifiers['skill_id']}", method="DELETE")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--worker-container", default="jarvis-taskws-accept-task-worker-1")
    parser.add_argument("--allow-production", action="store_true")
    parser.add_argument(
        "--validate-learning-task",
        help="Validate an already-completed learning task without another model run.",
    )
    parser.add_argument(
        "--resume-composed-task",
        help="Resume and validate a waiting Lists/Documents/Python task.",
    )
    parser.add_argument(
        "--resume-calendar-task",
        help="Resume and validate a waiting Calendar acceptance task.",
    )
    parser.add_argument(
        "--phases",
        default="interface,learning,budget,interrupted_script,lists_documents_python,calendar,calendar_cleanup,cancel",
        help="Comma-separated acceptance phases; defaults to the complete campaign.",
    )
    args = parser.parse_args()
    if not args.label.startswith("ACCEPTANCE-"):
        raise SystemExit("label must start with ACCEPTANCE-")
    if ":8000" in args.base_url and not args.allow_production:
        raise SystemExit("refusing production-like port without --allow-production")

    api = Api(args.base_url, _operator_key(args.env_file))
    phases = {item.strip() for item in args.phases.split(",") if item.strip()}
    allowed_phases = {
        "interface",
        "learning",
        "budget",
        "interrupted_script",
        "lists_documents_python",
        "calendar",
        "calendar_cleanup",
        "cancel",
    }
    unknown = phases - allowed_phases
    if unknown:
        raise SystemExit(f"unknown phases: {','.join(sorted(unknown))}")
    evidence: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "label": args.label,
        "results": {},
    }
    learning_ids: dict[str, str] | None = None
    try:
        if args.validate_learning_task:
            evidence["results"]["learning"] = _validate_existing_learning(
                api, args.label, args.validate_learning_task
            )
            evidence["status"] = "passed"
            return_code = 0
            return return_code
        if args.resume_composed_task:
            evidence["results"]["lists_documents_python"] = _resume_composed_task(
                api, args.resume_composed_task
            )
            evidence["status"] = "passed"
            return_code = 0
            return return_code
        if args.resume_calendar_task:
            calendar, _ = _resume_calendar_task(api, args.resume_calendar_task, args.label)
            evidence["results"]["calendar"] = calendar
            evidence["status"] = "passed"
            return_code = 0
            return return_code
        if "interface" in phases:
            evidence["results"]["interface"] = _static_checks(api)
        if "learning" in phases:
            learning, learning_ids = _run_learning(api, args.label)
            evidence["results"]["learning"] = learning
        if "budget" in phases:
            evidence["results"]["budget"] = _run_budget(api, args.label)
        if "interrupted_script" in phases:
            evidence["results"]["interrupted_script"] = _run_interrupt(
                api, args.label, args.worker_container
            )
        if "lists_documents_python" in phases:
            evidence["results"]["lists_documents_python"] = _run_lists_documents_python(
                api, args.label
            )
        fixture_title = f"{args.label} AYSO Recurrence"
        if "calendar" in phases:
            calendar, fixture_title = _run_calendar(api, args.label)
            evidence["results"]["calendar"] = calendar
        if "calendar_cleanup" in phases:
            evidence["results"]["calendar_cleanup"] = _run_calendar_cleanup(
                api, args.label, fixture_title
            )
        if "cancel" in phases:
            evidence["results"]["cancel"] = _run_cancel(api, args.label)
        evidence["status"] = "passed"
        return_code = 0
    except Exception as exc:
        evidence["status"] = "failed"
        evidence["error"] = f"{type(exc).__name__}:{exc}"
        print(evidence["error"], file=sys.stderr, flush=True)
        return_code = 1
    finally:
        if learning_ids is not None:
            try:
                _cleanup_learning(api, learning_ids)
                evidence["learning_fixtures_retired"] = True
            except Exception as exc:
                evidence["learning_fixtures_retired"] = False
                evidence["learning_cleanup_error"] = type(exc).__name__
                return_code = 1
        evidence["finished_at"] = datetime.now(UTC).isoformat()
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"evidence={args.evidence} status={evidence['status']}", flush=True)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
