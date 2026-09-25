# Jarvis Task Workspace: implementation and deployment plan

**Prepared:** September 25, 2026
**Repository:** https://github.com/hardyaf/hardyai
**Reviewed source:** `93514d78656de3011b9acb9b228f74274760ef64`
**Suggested repository location:** `docs/JARVIS-TASK-WORKSPACE-IMPLEMENTATION-PLAN.md`
**Status:** Ready for Codex execution. This document specifies work to implement and deploy; it does not claim that the changes are already built or deployed.

## 1. Product goal and completion standard

Build Jarvis as a local task assistant that can work for several minutes, use tools and small Python programs, inspect results, revise its approach, and complete the user's request. A skill should supply purpose, instructions, examples, credential/resource bindings, workspace access, and a few meaningful restrictions. A new procedure over existing capabilities should normally require instructions, not application code.

The user must be able to open the local interface, inspect a task's plan and completed work, redirect it, authorize more computation, and resume the same task. Corrections explicitly intended for future use must become editable, persistent preferences or skill instructions and influence later tasks.

**The release succeeds when an unfamiliar request over authorized capabilities works without adding an intent enum, phrase branch, or task-specific handler—and when that task can be supervised and resumed.** Calendar is the primary proving case, with Lists and an existing document capability establishing that the runtime is general.

Done means these behaviors run on the existing Ubuntu deployment through the actual local interface. Source changes, an inactive feature flag, a mock-only demonstration, or passing tests without deployment do not meet this standard.

## 2. Execution authority and scope

When the user instructs Codex to execute this plan, execute implementation, final acceptance, deployment, and post-deployment verification as one assignment. Work packages below organize the work; they are not separate permission gates. Make routine implementation decisions and necessary cross-module changes without repeatedly requesting architecture approval.

This plan supersedes conflicting requirements in the previous reasoning-led execution plan and related implementation instructions concerning:

- Named-phase-only authorization and changed-file allowlists.
- Mandatory characterization tests before every change and full certification at each phase.
- One typed wrapper as the only allowed model tool protocol.
- Exclusion of bounded Python execution and instruction-only skills.
- Fixed initial skill selection and banning a second successful use of a tool.
- Discord as the only place to review and approve work.
- Blanket restrictions that prevent owner-authorized private task continuity or useful cross-domain composition.
- Voice-style completion latency targets, repeated model benchmark runs, and mandatory multiday observation gates.

Read the current repository instructions and inspect current HEAD once at the start. Reconcile material changes since the reviewed snapshot. Update contradictory project documentation so later Codex sessions follow this direction. Preserve unrelated user changes and applicable requirements that do not conflict. This is scoped project authorization, not an instruction to bypass system permissions or access controls. The old restrictions are documented in the [prior plan](https://github.com/hardyaf/hardyai/blob/93514d78656de3011b9acb9b228f74274760ef64/docs/reasoning-led-capability-execution-plan.md#L9-L82).

Retain account/resource permissions, credential isolation, existing child/user boundaries, truthful reporting, and protection against duplicate effects. Routine reads, calculations, and explicitly requested writes within an existing grant should execute without another approval at every step. A novel combination of permitted operations is not itself a new permission request. Existing meaningful restrictions on invitations, destructive actions, and ungranted resources remain visible and usable through the local interface.

Do not add unrelated features, broaden account permissions, replace models, rebuild OCR, introduce cloud inference, or undertake a general cleanup. If required access is unavailable, complete everything that can proceed and report the exact observed blocker and smallest missing input. Do not assume credentials or host access are unavailable without checking the authorized environment.

## 3. Settled architecture

Use one native durable task runtime in the existing Python application. Keep FastAPI, SQLite, the local Ollama model, current provider implementations, the durable job ledger, and existing document/OCR services.

The source already has a repeated model/tool loop and useful leased jobs/checkpoints. The change is to remove restrictive orchestration and supply durable state, executable workspace capabilities, and useful context. A wholesale framework migration would still require integrating these same systems. See the [current loop](https://github.com/hardyaf/hardyai/blob/93514d78656de3011b9acb9b228f74274760ef64/app/core/main_tool_loop.py#L552-L610) and [job checkpoint support](https://github.com/hardyaf/hardyai/blob/93514d78656de3011b9acb9b228f74274760ef64/app/jobs/repository.py#L571-L705).

| Area | Implementation decision |
| --- | --- |
| Agent execution | One continuing model/tool conversation owned by a durable task worker. |
| Inference | Existing local model through accelerator admission, using native Ollama chat. |
| Durable state | Existing Core SQLite and job ledger; additive task/event/learning records. |
| Code execution | One bounded Python runner in isolated per-run containers. |
| Integrations | Reuse current domain implementations behind a generic capability interface. |
| Skills | Discoverable procedural instructions plus resource bindings and versioned user edits. |
| Memory | Explicit scoped preferences and useful history in SQLite; no vector database required. |
| Interface | Extend existing FastAPI-served HTML/JavaScript with a task workspace. |
| Discord | Optional task entry and notification adapter; no dependency for supervision. |

Do not make OpenClaw, Pi, Pydantic AI, LangGraph, MCP conversion, React, Redis, or another job queue a prerequisite for this release. Use the harness concepts established in the review. Reconsider the native choice only for a demonstrated blocking requirement, with evidence and a narrowly scoped alternative.

Keep capability discovery/invocation independent of transport so a future MCP adapter can use the same authority and receipts. MCP supplies an integration protocol; the application still needs task execution and supervision. It is not necessary to convert functioning providers to MCP to build this release. [MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture).

## 4. Task contract

### Task identity and lifecycle

A session contains conversation history and may contain multiple tasks. Each task has its own durable ID, owner, original goal, current revision, plan, conversation/observation references, workspace, applied skill/preference revisions, operation receipts, and budget. Resuming uses the same task and effect history. Opening another browser tab or restarting a worker must not create a new task accidentally.

Use task states equivalent to `queued`, `running`, `paused_budget`, `paused_user`, `waiting_input`, `waiting_approval`, `completed`, `failed`, and `cancelled`. These are task semantics; map them to the existing job ledger deliberately rather than creating a second competing queue. A paused task must not spin through retries or become a dead-letter job merely because the user has not answered.

The worker loads state, asks the model for the next action, executes authorized tools/code, records results, and continues. The model can discover/load skills and capabilities, update the visible plan, run a program, ask a necessary question, or finish with evidence. Useful errors return to the model with their actual reason and actionable fields.

Persist before and after side effects. Use the existing lease/fencing mechanism to ensure one worker owns a task. Store large results and scripts outside the bounded checkpoint payload and reference them by ID. A checkpoint must retain remaining work, unresolved effects, and pending user input—not just counts or a partial response.

### User controls

The API and interface must support task creation/listing, detail, events, messages, pause, cancellation, budget extension, and continuation. Proposed route names are `/api/tasks`, `/api/tasks/{id}`, `/api/tasks/{id}/events`, `/messages`, `/pause`, `/cancel`, and `/continue`; adapt naming to existing conventions.

Mutations use task revision checks and idempotent submission IDs. Persist steering immediately; apply it before the next effect begins. If the current provider call has already started, report that accurately and reconcile its result. Pause/cancel prevents further actions; it does not pretend to undo work already committed. Continuing a budget-paused task must not re-run its original request from scratch.

Keep explicit task selection in the local interface and durable channel/thread-to-task references where Discord is used. Do not rely on the current short in-memory channel timeout to identify a continuing task. Show which task a reply will affect when more than one is active.

### Initial operating defaults

These are adjustable implementation defaults, not performance claims:

| Setting | Initial value or rule |
| --- | --- |
| Task computation allowance | 5 minutes of active model/tool execution. |
| Model decisions | 32 per initial allowance. |
| Capability calls | 100 per initial allowance, including calls inside scripts. |
| Agent concurrency | One executing agent task initially; queue other tasks. |
| Python execution | 30 seconds per run, 512 MiB memory, one CPU, bounded processes/output. |
| Continue control | Add 5 minutes and the initial decision/call allowance, or enter custom amounts. |
| Waiting | Waiting for the user or admission does not consume active computation allowance. |

Track and display actual usage. Check soft budgets at action boundaries, with bounded per-call timeouts; an in-flight call can finish after the nominal task allocation. Always preserve state when pausing. A script timeout becomes a useful observation and can be retried with corrected code or an explicit increased limit. Script resource limits and task computation grants are separate controls.

Remove unrelated short request timeouts from the worker lifetime. API acknowledgement must not wait for the model to finish. Keep the effective deployed model/context settings; support useful reasoning/output within those limits. Compact old conversation while preserving the goal, user corrections, plan, key findings, and effect ledger, with full records retrievable. A context window cannot be extended simply by adding more task budget.

## 5. Implementation work packages

Proceed through these packages without intermediate acceptance ceremonies. Keep a short worklog with decisions, changed areas, remaining work, and any real blockers.

### A. Establish the execution path and model protocol

Inspect the actual deployment/configuration and identify affected entry points. Add the native task chat interface, initially with a harmless tool. Preserve assistant tool calls and corresponding tool results across requests; supply the user's task, history, skills, and current state.

Adapt `app/api/accelerator_admission_app.py` and the model client to accept the actual installed Ollama tool conversation format, including assistant tool calls, tool-result messages, and a useful bounded tool catalog. Keep authenticated admission, model selection, lane scheduling, and payload/resource bounds. Non-streaming inference is acceptable; task progress is streamed separately.

This is an early integration dependency: the existing gateway permits only `role`/`content` messages and one `submit_model_step` tool, so it rejects a normal tool transcript. Do not bypass the gateway or connect a framework directly to raw Ollama to hide that incompatibility. [Existing gateway validation](https://github.com/hardyaf/hardyai/blob/93514d78656de3011b9acb9b228f74274760ef64/app/api/accelerator_admission_app.py#L208-L278). Follow the installed version's native protocol; Ollama documents the assistant/tool exchange and continuing loop in its [tool-calling guide](https://docs.ollama.com/capabilities/tool-calling).

Run one early local-model tool round trip through admission. It should use a tool result to produce a subsequent decision or answer. This is a compatibility check, not a model benchmark. If access is unavailable, implement the transport against documented schemas and record that this check remains outstanding; continue unrelated work.

### B. Build durable task execution

Add the task aggregate, ordered events, conversation/observation records, budget grants, and script references through additive migrations. Use a job type such as `agent.task.v1` and the existing repository/worker infrastructure. Add it to dependency readiness where required.

Move long-running execution out of the synchronous turn path. Make local task submission the primary route; adapt existing entry points to submit or continue tasks. Replace the old commitment/selection/step-classifier chain with the continuing task conversation for this path. Keep one active execution authority after cutover.

Implement checkpointing, lease renewal, interruption, restart recovery, pending steering, and effect reconciliation. Results and plans must be readable without launching another model request. A budget pause should have a useful deterministic summary even if no model budget remains.

Remove the blanket `completed_tool_must_not_repeat` rule. Calls with different arguments, pagination, another event, or a verification read are valid. Detect repeated identical calls with no progress, and identify mutations by durable logical operation IDs. A read after a write must be able to fetch fresh provider state. The offending existing check is in [main_backend.py](https://github.com/hardyaf/hardyai/blob/93514d78656de3011b9acb9b228f74274760ef64/app/core/main_backend.py#L1054-L1062).

Inspect whether current approvals, operation receipts, and finalizers assume a synchronous turn; adapt them to task continuation. Do not let an old finalizer mark a budget-paused task completed.

### C. Expose composable capabilities and bounded Python

Provide discover/describe/call operations over the existing registry. Use stable provider primitives and meaningful schemas, with pagination and complete error information. Keep existing provider code where it works; do not rewrite every domain.

For Calendar, implement interval query with pagination, get, create/update with recurrence, and existing authorized deletion behavior. Support occurrence expansion/readback and timezone-aware boundaries. Fix truncated-page handling and incorrect global ordering. Distinguish single-event, occurrence, and series edits when applicable. Reuse deterministic identifiers, etags, and reconciliation already present in the provider adapter. Existing Calendar implementation: [calendar_live.py](https://github.com/hardyaf/hardyai/blob/93514d78656de3011b9acb9b228f74274760ef64/app/services/google/calendar_live.py).

The model can choose native recurrence or individual events as appropriate; it must verify the requested dates and local times. Do not implement a dedicated handler for “AYSO,” “Tuesdays and Thursdays,” or “six weeks.” Resolve whether AYSO means text, a calendar, a color, or another actual convention using context or a necessary question.

Add one Python runner. Default deployment mechanism: a dedicated trusted launcher creates fixed-image, per-run containers. Only the launcher may control Docker; the API, task worker, and generated program must not receive its socket. The launch API accepts a task/run reference, with image, mounts, and runtime flags fixed by trusted configuration rather than arbitrary model-supplied Docker options.

Run code as non-root with a read-only base filesystem, only the authorized task workspace writable, no general network access, no provider secrets, and resource limits. A task-scoped broker socket exposes authorized capabilities. The worker/broker retains credentials and checks the current task owner, resource scope, budget, pause state, worker lease, steering revision, and operation identity on each call. Suspend a script with stale steering before its next effect and incorporate the user's update; reconcile any provider call already in flight. Do not use Python `exec` in the privileged application process or an AST blacklist as the isolation mechanism.

The script surface should remain small: call/describe capabilities, read/write workspace files, and return artifacts. Programs may calculate dates, filter records, paginate, batch permitted actions, and transform data. New trusted dependencies belong in the fixed runner image; scripts do not install arbitrary packages at runtime.

Capture immutable script source, permitted inputs, stdout/stderr, artifacts, and broker call receipts under the task/source retention rules. Use references or redaction where contents cannot be retained. Intentionally volatile input must be reloaded from an authorized source or requested again after restart; do not claim it was durably checkpointed.

Use a per-run workspace with immutable input snapshots and separately published outputs so a failed program can replay from its original local state. If that state cannot be reconstructed, pause and reconcile. On restart, reconcile the existing run before launching another. Replay uses the same run ID and recorded calls/results, checks call identity and arguments, and stops on divergence. A corrected script revision inherits prior effect receipts and retains logical operation identities for retries of the same intended mutation. New script/run IDs must not turn committed or unresolved writes into fresh writes. A provider timeout after commit requires readback/reconciliation before another mutation. Do not claim distributed exactly-once execution.

### D. Implement skills, durable preferences, and learning

Allow instruction-only skills to be discovered and loaded even when they add no new tool descriptor. Load their actual procedural text into the execution conversation. Skills describe goals, conventions, examples, relevant capability names, credential binding references, workspace grants, and restrictions. Credential references identify existing grants; skill prose never contains raw credentials or grants itself new authority.

Provide skill creation/editing in the local app. Most new household procedures should be creatable there using existing capabilities. An entirely new external service can still require a provider/MCP adapter; changing how existing services are combined should not.

Use Core SQLite as the canonical store for user-authored skills and learned overlays, with stable IDs and revisions. Retain shipped Markdown as base skill content. The effective skill page renders the base plus the applicable user/project changes and supports edit, history, export, and undo. Ensure startup imports and caches do not overwrite or hide learned revisions. Existing loading/discovery behavior is in [registry_service.py](https://github.com/hardyaf/hardyai/blob/93514d78656de3011b9acb9b228f74274760ef64/app/skills/registry_service.py).

Store preferences separately from conversation summaries: owner, scope, rule, source instruction, revision, and active/retired state. Use explicit current-task directions over project preferences, then user skill-specific preferences, then general user defaults and shipped defaults. None of these overrides resource authority.

“For this task” changes this task. “Remember,” “from now on,” and clear standing corrections save a reversible rule and show what changed without requiring another confirmation for an ordinary preference edit. If scope is unclear, apply the correction now and ask only about persistence. Do not silently generalize a one-time request into a global rule.

Retrieve useful history and current preferences for execution, repair, and final response generation—not just initial routing. Retain task identity across browser sessions and process restarts. A formatting preference must influence final rendering; an interpretation correction must influence tool arguments and planning. The existing tool prompt does not render all context it is supplied, so merely writing another memory row is insufficient. [Current prompt builders](https://github.com/hardyaf/hardyai/blob/93514d78656de3011b9acb9b228f74274760ef64/app/core/main_backend.py#L2135-L2294).

Persist private task data in its authorized store and workspace. Allow an explicit owner request to move relevant information between approved private capabilities. Preserve stronger source-storage restrictions and explicit non-retention requests; use source references where copying is disallowed. A user's formatting instruction can be retained independently of sensitive document contents. Display and permit deletion of stored history and learned rules.

### E. Build the local task workspace and complete cutover

Extend the existing HTML/JavaScript interface with three useful areas: Tasks, Task detail, and Skills/preferences. Task detail shows the goal, editable plan/steering conversation, concise progress, tool/code activity, completed effects, remaining work, budget, questions/approvals, artifacts, and final result. Technical detail can be expanded instead of filling the normal user flow.

Use authenticated server-sent events with durable event IDs and replay, with polling as a simple fallback. Reloading the page must reconstruct state from the server. The worker continues when the browser closes. Show brief progress explanations and concrete activity, not a promise of access to hidden model reasoning.

Reuse existing operator login/session and CSRF machinery, mapping it to a trusted owner identity for resource checks. Do not require forged Discord fields or embed an operator secret in frontend code. The local interface must support pending approvals and budget grants using that identity.

Keep configured Discord entry/notifications usable through the same task API where practical; the complete supervision flow must work without Discord. Preserve existing document ingestion, Paperless, OCR, gateway ownership, and enabled domain jobs. Expose their existing capabilities through the task path without rebuilding their pipelines.

Update configuration, startup wiring, image packaging, readiness checks, README, and contradictory old design instructions. Make the new task path active in the deployed profile, with the intended existing capability grants actually usable. Do not finish with an empty operation allowlist or a demo-only route. Avoid maintaining two independent active reasoning systems.

### Suggested code ownership

These are starting points, not file allowlists. New paths are proposed; adapt them to current repository conventions.

| Work | Existing areas / proposed additions |
| --- | --- |
| Task aggregate and migrations | `app/db/`, `app/jobs/`, proposed `app/tasks/` |
| Native conversation/model adapter | `app/core/`, `app/services/`, `app/api/accelerator_admission_app.py` |
| Task worker | `app/workers/`, `app/runtime.py`, `scripts/check_worker_readiness.py` |
| Capability bridge and learning | `app/skills/`, `app/services/memory_service.py`, `app/prompts/skills/` |
| Calendar primitives | `app/services/google/calendar_live.py`, owning Calendar domain/contracts |
| Runner and broker | Proposed `app/tasks/code_runner/`, dedicated image/service in `deploy/docker/` |
| Task UI/API | `app/ui/`, `app/api/routes/`, existing identity/session services |
| Release automation | `deploy/docker/`, `scripts/`, concise worklog/release report under `docs/` |

## 6. Development testing policy

The user explicitly prefers building the complete system and running a substantial test batch at the end. Follow that preference.

During implementation, run only checks that answer a concrete question needed to continue safely or avoid building on a broken foundation:

1. Syntax, import, or narrowly scoped lint checks for changed runnable components.
2. The single early model/tool compatibility check through admission described above.
3. A migration check on a disposable database copy: migration succeeds, retained records remain readable, and reapplication is harmless.
4. A focused check for a newly introduced effect-replay or storage risk when waiting would make later work unsafe. State the risk; do not use this exception to run the whole suite routinely.

Write useful regression tests alongside code where they establish durable behavior, but batch their broad execution at the end. Do not write tests that only mirror implementation or cover trivial reversible edits.

Do not run full pytest, the complete lint tree, repeated live-model benchmarks, broad security sweeps, or production canaries after each work package. Do not add coverage targets, a benchmark tournament, repeated clean full runs, or a 24/48/72-hour waiting gate. Do not restart production for every milestone. There is no requirement to prove this release is faster than the old assistant.

Update tests that encode deliberately superseded behavior, including the blanket same-tool ban, wrapper-only transport, and loss of task context. Retain tests for identity/resource scope, provider effect integrity, truthful completion, stored-data compatibility, and existing functions. Explain material replacements; do not delete unrelated failing tests to obtain a green result.

## 7. One consolidated final acceptance campaign

Finish the implementation before starting this campaign. Build deployable candidate images and run acceptance in a separate Compose project with distinct ports, disposable database copies, and separate task workspaces. Disable normal Discord ingress, schedulers, and production job consumption there. Never mount production databases writable or run live migrations before the deployment backup. Only explicitly selected provider fixtures may receive candidate effects.

Run the retained automated suite and new behavior tests once in an environment matching the release dependencies. Run the integrated user scenarios against the candidate services with the actual configured local model and updated admission gateway. Coordinate a GPU test window and quiesce other GPU submissions where needed so candidate and production admission do not dispatch competing work. Do not install another model service for acceptance.

The production image currently omits tests and pytest. Use a matching test stage or disposable test environment; do not expect pytest inside the production image. Ensure all new runtime files are packaged by the Dockerfile and clean exporter. [Dockerfile](https://github.com/hardyaf/hardyai/blob/93514d78656de3011b9acb9b228f74274760ef64/deploy/docker/Dockerfile), [clean exporter](https://github.com/hardyaf/hardyai/blob/93514d78656de3011b9acb9b228f74274760ef64/scripts/export_clean_repo.py).

Use synthetic data, an authorized test calendar where available, or clearly labeled non-inviting fixtures in an authorized writable calendar. Use only fixture IDs for cleanup. Do not send invitations or alter real household events for acceptance. Record expected results before asking the model. Verify provider state and task records, not just a success message.

| Acceptance | Required evidence |
| --- | --- |
| Calendar day and AYSO query | Correct local-day results and relevant matches using the actual label/text convention. At least two uses of the same query tool with different arguments succeed. |
| Calendar pagination | Matching events beyond the first page are found; counts/order are correct, with no silent truncation. |
| Recurrence | Create 12 Tuesday/Thursday occurrences over six weeks and verify actual dates/times. Suggested fixed fixture: Sep 29–Nov 5, 2026, 6–7 p.m., `America/New_York`, crossing daylight-saving time. This is a test timezone, not an assumed user preference. |
| Unfamiliar composition | After code is frozen, ask for a novel combination such as selected AYSO occurrences transformed into a checklist grouped by week. Generated Python executes and produces the correct artifact/actions without a new handler. |
| Repair and uncertain writes | A useful tool error changes the next attempt. A simulated provider commit-then-timeout reconciles without duplicate effects; verify script and direct-call paths. |
| Budget and redirection | Force an early budget pause, inspect plan/effects, change remaining work, add allowance, and finish under the same task ID without repeating completed writes. |
| Restart and browser continuity | Close/reopen the browser and restart a task worker at a checkpoint; pending work, user steering, effects, and continuation remain correct. Include an interrupted script run. |
| Learning | Correct formatting and item interpretation, request persistence, inspect the changed rule/skill, restart, then use a fresh session. Verify both behavior and editable/undoable stored revision. |
| Instruction-only skill | Create a new procedural skill through the app using existing tools; discover and use it without registering another domain handler. |
| Local supervision | Inspect actual progress, answer a question, approve a bounded action where required, pause, resume, and cancel through the local app with Discord unavailable. |
| Workspace/resource boundary | Authorized scripts work; a small representative attempt to access another owner's resource, escape its workspace, or obtain a provider credential fails without side effects. |
| Existing household capability | Calendar/Lists records survive migration; one configured document ingestion/read workflow and relevant existing worker readiness still work. Do not re-benchmark the OCR stack. |

Combine related rows into a few substantial user tasks; do not multiply them into dozens of repeated live-model runs. Deterministic faults and boundary assertions can use controlled adapters, while the core task/skill/code/learning scenarios must use the real model. Advance a controllable clock for timeout cases where possible instead of waiting through long observation windows.

Fix failures and rerun the affected checks. Repeat a broad batch only when a fix changes shared behavior enough to create a concrete wider risk. All required new behaviors and relevant retained integrity checks must pass. Report unrelated pre-existing failures with evidence and disposition; they are not grounds for silently deleting tests or endless unrelated refactoring. Missing live integration evidence stays explicitly unverified.

After any source change to the candidate, rebuild affected images and bind acceptance evidence to that exact source/image. The final tested release images are the images to deploy.

## 8. Deployment and rollback

### Inspect and prepare

Use the actual Ubuntu 24.04 Compose installation. Discover its authorized connection method, repository/deployment location, running service/profile set, image IDs, pending jobs, data mounts, and non-secret effective configuration. The protected `.env` determines the running model; do not infer it from older documentation. Every production Compose command must use `docker compose --env-file .env -f deploy/docker/compose.yaml ...`. [Runtime decisions](https://github.com/hardyaf/hardyai/blob/93514d78656de3011b9acb9b228f74274760ef64/README.md#L13-L25).

Build from a clean source checkout/export excluding secrets and runtime data. Retain the old application image under a unique tag before changing the shared `jarvis-poc-app:local` tag. Inventory all services consuming that image. Keep selected model files, volumes, enabled integrations, GPU admission, and existing OCR/Paperless services intact.

Implement a small repeatable deploy/rollback helper or exact runbook using the discovered deployment facts. Record actual commands and service names rather than treating this document's ellipsis as a runnable script. Update packaging if any new directories or runner dependencies require it.

### Back up and switch once

Preserve the prior image/configuration and create an integrity-checked Core database backup before the live migration. Use the existing SQLite backup helper, not a file copy of a live WAL database. Rehearse the additive migration on a disposable copy and check prior-reader compatibility if rollback relies on it. [Database helper](https://github.com/hardyaf/hardyai/blob/93514d78656de3011b9acb9b228f74274760ef64/scripts/manage_database.py#L34-L50).

If the actual changes touch Documents state or require coordinated cross-store rollback, use the existing coordinated Documents backup. Do not add a full archive restoration exercise for an additive Core-only change. [Document backup helper](https://github.com/hardyaf/hardyai/blob/93514d78656de3011b9acb9b228f74274760ef64/scripts/manage_document_backup.py).

Drain or stop old task consumers before enabling replacements. Preserve pending jobs and receipts. Deploy the tested candidate images with `--no-build`; recreate accelerator admission first and wait for health, then Jarvis and affected/new task and runner services. Recreate other consumers only where their changed code/configuration requires it. Do not unnecessarily restart Ollama, Paperless, Docling, or OCR services.

Confirm the deployed configuration activates the new task path, correct capabilities, required workers, workspace mounts, local UI access, and model gateway. Verify the intended GPU-backed model/context and absence of an unexpected CPU fallback. Keep secrets out of logs, tracked configuration, and release reports.

### Final live verification

After cutover, run a short check through the actual user interface: one useful composed task, one budget-pause/continue interaction, persisted preference recall, and service/worker health. This confirms deployment; it is not a second full acceptance campaign. Record the actual private UI URL or access method for the user without exposing it in a public repository.

If deployment fails, stop new task intake/claims and restore the retained compatible image/configuration. Preserve effect receipts and task history. Do not routinely restore an old database over provider writes committed since the backup; that loses the record needed to prevent duplicates. Database restore is reserved for demonstrated corruption/incompatibility with an explicit reconciliation plan. Do not erase volumes or reset live data as a deployment shortcut.

## 9. Final handoff and stopping rules

Keep one short implementation worklog and one release report. The final report must contain:

- Implemented behavior and meaningful design decisions.
- Commit and deployed image identifiers, affected services, and actual local app access instructions.
- Final acceptance results, relevant failures resolved, and any genuinely unverified requirement.
- Backup location, retained rollback image, and usable rollback instructions without secrets.
- Known limitations and any remaining blocker with the exact missing access or decision.

Continue automatically between work packages, through final acceptance, and into deployment when the required access is available. Do not end with “ready to deploy” if deployment can be completed under the user's execution instruction. Do not mark the release complete when a required live behavior is merely mocked, disabled, or inaccessible.

If the Codex session approaches its own context or execution limit, checkpoint the worklog with the current commit, completed work, precise next action, and outstanding verification. A later Codex session should resume there without redoing the architectural review or rerunning successful broad tests without cause.

The task ends when the local Jarvis workspace is deployed and the user can assign, inspect, redirect, resume, and teach tasks using it—or when a specific external blocker is demonstrated after all independent work has been completed.
