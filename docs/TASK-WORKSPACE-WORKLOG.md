# Task Workspace implementation worklog

**Started:** 2026-09-25
**Controlling plan:** `docs/JARVIS-TASK-WORKSPACE-IMPLEMENTATION-PLAN.md`
**Starting commit:** `93514d78656de3011b9acb9b228f74274760ef64`

## Current status

Implementation is feature-complete at commit `b3b98de4e92b67625804f7ab4eb76a7ec63030f5`.
The exact candidate application image is
`sha256:c72a4d165a7cc5e477a2493f31fd283614702df53fbe255a0a640aa1daf8191d`; the runner image is
`sha256:4fa3a3e587cd04b3de18396a89c6d758a35fa0f6357e8c66157d8ca32756cd3c`.

The consolidated acceptance campaign is complete except for live Calendar provider scenarios.
The candidate and production containers both reach the existing Google configuration, but refreshing
the existing token returns `invalid_grant`. No Calendar fixture was created and no household event was
altered. Deployment has not started because the controlling plan requires the primary Calendar proving
case to pass before cutover. The existing Google account must be reauthorized; then rerun only the
Calendar create/readback/cleanup scenarios, bind the final source commit to rebuilt image IDs, and
continue with backup, deployment, and post-deployment UI verification.

## Reuse map

| Concern | Decision | Authority / reason |
| --- | --- | --- |
| Work scheduling, claims, leases, retries | **reuse** | `DurableJobRepository`; task runs use `agent.task.v1` and do not add another queue. |
| Provider authorization and typed calls | **adapt** | `SkillRegistryService`, `AuthorizedSkillExecutor`, and domain handlers remain the execution authority; the task bridge adds discovery and durable receipts. |
| Identity and local authentication | **reuse** | Existing operator session, CSRF, and `RequestPrincipal` map the local operator to one trusted owner. |
| Provider effect integrity | **adapt** | Existing deterministic provider operation IDs and receipts are retained; task logical operation IDs add durable replay/reconciliation state. |
| Conversation/session history | **adapt** | Existing sessions remain transport conversation history. Durable tasks add task-local messages because a task can outlive or span sessions. |
| Assistant memory | **adapt** | Existing memory remains conversational memory. Explicit preferences use a separate revisioned table because they are user-editable policy inputs, not summaries. |
| Skill catalog | **adapt** | Shipped Markdown/SQL registry remains the base catalog. Revisioned user instruction overlays add editable instruction-only skills without granting capabilities. |
| Task aggregate and events | **new** | No durable, resumable multi-step task aggregate exists. It is a platform boundary shared by Calendar, Lists, Documents, and future domains. |
| Python execution | **new** | No isolated code runner exists. A fixed-image launcher is isolated from provider credentials and is the only service given Docker control. |
| Local supervision | **adapt** | Existing FastAPI operator UI/API is extended; Discord remains optional. |

## Data ownership

| Data / effect | Canonical owner | Projection / consistency / deletion |
| --- | --- | --- |
| Task state, messages, events, budgets, script metadata | Core SQLite task tables | UI and SSE are read-through projections. Owner cancellation retains audit records. |
| Task execution claim | Core SQLite `durable_jobs` | `agent_tasks.active_job_id` is a pointer; the durable-job lease/fencing token is authoritative. |
| Task files and artifacts | Task workspace volume | SQLite stores bounded metadata and references. Owner deletion removes the task-scoped files through the trusted workspace service. |
| User preferences | Core SQLite preference revisions | Effective prompt context reads active revisions. Retirement is reversible and auditable. |
| Skill learning | Core SQLite user skill revisions + shipped Markdown base | Effective content is base plus active overlay. Undo creates a new revision; startup sync never overwrites revisions. |
| Calendar/List/Document state | Existing domain/provider authority | Task records store bounded observations and receipts, never a competing canonical copy. |
| External side effects | Existing provider/domain adapter | Task effect receipts reference the deterministic operation and reconciliation state. Provider truth is read back after uncertain writes. |

## Decisions and progress

- Preserve existing document/OCR pipelines and all account/resource/credential boundaries.
- Use an additive schema migration whose compatibility record permits the actually deployed v12 binary
  to read the upgraded database during image rollback. The first disposable rehearsal caught and
  corrected the candidate's initially over-strict v14 reader floor before deployment; the final
  additive floor remains compatible with the existing version-10 reader boundary as well.
- The local workspace uses the existing exact-action proposal ledger for invitations/destructive work.
  Local decisions bind the operator and proposal hash; completed approval effects resolve from the
  action receipt and are never invoked again by the task worker.
- Only the trusted runner launcher receives Docker control. Generated Python is non-root, offline,
  credential-free, fixed-image, resource-capped, and calls providers through the fenced task broker.
- Calendar gained paginated reads, exact event readback, structured recurrence, and explicit
  single-event/occurrence/series write scope. No request-specific AYSO or schedule handler was added.
- Complete one consolidated acceptance campaign after implementation, per the controlling plan.

## Acceptance and deployment evidence

- One native `gpt-oss:20b` assistant/tool/result/assistant round trip passed through a disposable
  instance of the real admission gateway.
- The additive migration was rehearsed twice on a disposable copy of the deployed v12 database:
  v12 to v15 preserved record counts, the migration was idempotent, and the deployed v12 reader
  accepted the v15 database for image rollback.
- The one consolidated automated run initially reported 866 passing and 31 failing tests. Focused
  fixes passed 130 affected tests; 17 remaining failures are unchanged legacy `/ask` phrase-router
  expectations superseded by the reasoning-led Main boundary. Do not rerun the full suite unless a
  later fix creates a concrete wider risk.
- Integrated candidate acceptance passed for local interface authentication, durable task creation,
  instruction-only skill discovery/load/edit/version restore, ask/resume, persistent preferences,
  budget pause/continue, user redirection, interrupted-script recovery, browser-session reconstruction,
  Lists and Documents composition, provider receipts, bounded Python, published artifacts, workspace
  escape/credential/root-filesystem boundaries, and cancellation.
- The composed task reused one task across input, budget, user pause, worker restart, and continuation.
  Its verified artifact reported all four isolation checks true even though the model's final prose
  misstated one field; acceptance correctly trusted the artifact and task records rather than prose.
- Candidate Calendar tool schema enforcement correctly rejected timezone-free timestamps and empty
  optional text. With corrected arguments, the live provider returned a retryable unavailable result.
  A content-free diagnostic isolated this to Google OAuth `RefreshError: invalid_grant` in both the
  candidate and the unchanged production container. No create effect or external fixture exists.
- During acceptance, Ollama exposed a stale NVIDIA container handle and fell back to CPU. Recreating
  only the existing Ollama service restored RTX 3090 visibility; the same paused durable task then
  completed successfully on GPU. Verify GPU execution again during post-deployment smoke testing.
- Content-minimized evidence is retained in the authorized Ubuntu acceptance staging directory. Key
  records include the affected pytest rerun, migration rehearsal, learning validation, composed task,
  budget/interruption aggregate, and cancellation results.

## Remaining work

- Reauthorize the existing Google Calendar OAuth account without changing account permissions.
- Rerun only Calendar query/recurrence/readback, bounded artifact, exact-series approval cleanup, and
  the already-written cancellation phase if Calendar changes touch shared task execution.
- Commit the final acceptance-controller/worklog updates, rebuild and record exact final image IDs,
  and run only packaging/readiness checks needed to bind those images to the accepted source.
- Preserve the production online database backup and rollback image, deploy once with the protected
  env file, activate the task profile, and complete the short actual-interface verification.
