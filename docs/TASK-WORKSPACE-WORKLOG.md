# Task Workspace implementation worklog

**Started:** 2026-09-25
**Controlling plan:** `docs/JARVIS-TASK-WORKSPACE-IMPLEMENTATION-PLAN.md`
**Starting commit:** `93514d78656de3011b9acb9b228f74274760ef64`

## Current status

Implementation is feature-complete and the release candidate is being prepared for consolidated
acceptance. Authorized Ubuntu connectivity is restored. Deployment topology, running image, enabled
capability configuration, and non-secret model inventory were inspected without changing production.
One native `gpt-oss:20b` assistant-tool-result-assistant round trip passed through a disposable instance
of the real admission gateway; its temporary container and data were removed.

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
- Use an additive schema migration whose compatibility record permits the prior v14 binary to read the
  upgraded database during image rollback.
- The local workspace uses the existing exact-action proposal ledger for invitations/destructive work.
  Local decisions bind the operator and proposal hash; completed approval effects resolve from the
  action receipt and are never invoked again by the task worker.
- Only the trusted runner launcher receives Docker control. Generated Python is non-root, offline,
  credential-free, fixed-image, resource-capped, and calls providers through the fenced task broker.
- Calendar gained paginated reads, exact event readback, structured recurrence, and explicit
  single-event/occurrence/series write scope. No request-specific AYSO or schedule handler was added.
- Complete one consolidated acceptance campaign after implementation, per the controlling plan.

## Remaining work

- Run the single migration-copy check and consolidated candidate acceptance campaign.
- Fix only observed failures, then bind the release commit to the tested image IDs.
- Preserve the production backup/rollback image, deploy once, and complete live UI verification.
