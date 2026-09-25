# Task Workspace implementation worklog

**Started:** 2026-09-25
**Controlling plan:** `docs/JARVIS-TASK-WORKSPACE-IMPLEMENTATION-PLAN.md`
**Starting commit:** `93514d78656de3011b9acb9b228f74274760ef64`

## Current status

Implementation, consolidated acceptance, deployment, and post-deployment verification are complete.
The deployed release commit is `8d7f7d3f6a07c895561af7486a98b555df7933eb`. Exact images:

- application: `sha256:da89146d907614c35ad5f6a347aa783f458856135adef41d5a1eb1813de950d6`
- bounded runner: `sha256:3b7d27d24413b2874fc0de46db67dfde93393d62609e2a0348cf701bafe11d61`

The local workspace is active at `http://192.168.1.127:8000/`. Authenticate with the existing local
operator key; the browser exchanges it for the protected operator session cookie and CSRF token.

The existing Google account was reauthorized without expanding scopes, and both Calendar Events and
Gmail read-only access were verified. Live Calendar acceptance then passed using one clearly labeled,
non-inviting recurring series. The series was deleted through the hash-bound local approval flow, the
provider returned a deletion receipt, and an exact-title readback found no remaining fixture. No real
household event was altered and no invitation was sent.

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
  optional text. A content-free diagnostic isolated the subsequent provider failure to Google OAuth
  `RefreshError: invalid_grant`; the account was reauthorized with its existing scopes, and Calendar
  Events plus Gmail read-only were verified before provider acceptance resumed.
- Live Calendar acceptance created exactly one clearly labeled, non-inviting weekly TU/TH series,
  expanded and verified all 12 occurrences across the daylight-saving transition, read one exact
  occurrence through `calendar.get_event`, and published the six-week JSON checklist with bounded
  Python. Google normalized RRULE component order; canonical recurrence comparison now recognizes that
  equivalent provider representation and reconciles the deterministic retry without a duplicate.
- Cleanup exposed two approval-boundary defects before release. The approved-action executor now
  recognizes only the exact authenticated local operator/Jarvis/task-workspace tuple instead of trying
  to resolve it as an external Discord identity, while external approvals retain their existing
  identity reauthorization. The task worker also deterministically reconciles terminal approval
  outcomes into task effect receipts at restart/resume, rather than depending on another model call.
  Focused Ubuntu checks passed all 16 task-workspace and approval-restart tests after these fixes.
- The exact acceptance Calendar series was deleted through local approval. Its proposal reached
  `executed` with a provider receipt, and the final exact-title query returned zero fixture events.
  The temporary OAuth callback/tunnel and diagnostic helpers were stopped/removed; the protected
  pre-reauthorization token backup remains available to the operator.
- During acceptance, Ollama exposed a stale NVIDIA container handle and fell back to CPU. Recreating
  only the existing Ollama service restored RTX 3090 visibility; the same paused durable task then
  completed successfully on GPU. Verify GPU execution again during post-deployment smoke testing.
- Content-minimized evidence is retained in the authorized Ubuntu acceptance staging directory. Key
  records include the affected pytest rerun, migration rehearsal, learning validation, composed task,
  budget/interruption aggregate, and cancellation results.
- Final release packaging used a fresh public-tree-checked export. The app and runner passed their
  syntax/import checks, the exact final source passed the 16 focused approval/task-workspace tests,
  and the exact images passed candidate interface/authentication/worker smoke before production.
- Production deployment first stopped before cutover when the host-only deployment helper used
  `python`; commit `8d7f7d3` corrected it to `python3`. No container or release image had switched at
  that point. The guarded rerun completed the online database backup and cutover.
- Post-deployment verification through the LAN interface and its operator cookie/CSRF endpoints passed:
  HTTP 200 rendered the workspace, a task durably paused for budget and resumed, bounded Python
  published the exact JSON artifact, a persistent preference revision was read back and retired, and
  the task-worker heartbeat remained visible. Core SQLite is at schema v15.
- `gpt-oss:20b` was resident at 100% GPU on the RTX 3090 during the production task. Jarvis,
  accelerator admission, task runner launcher, task worker, and action approval worker were healthy
  or running on the exact application image after cutover.
- Existing document/OCR services were not recreated or otherwise changed. Their pre-existing health
  condition remains: Docling and PaddleOCR are healthy, while document-gateway and PaddleOCR-VL report
  unhealthy and document-worker is restarting. This is compatibility debt outside this release, not
  a regression attributed to the task workspace deployment.

## Backup and rollback

- Online database backup:
  `/home/codex/jarvis-poc/backups/task-workspace/releases/20260925T204016Z/jarvis_v2-20260925T204016Z.sqlite3`
- Previous environment:
  `/home/codex/jarvis-poc/backups/task-workspace/pre-ff69d8c-runtime.env`
- Previous source archive:
  `/home/codex/jarvis-poc/backups/task-workspace/pre-ff69d8c-source.tar.gz`
- Rollback image: `jarvis-poc-app:rollback-20260925T204016Z`
  (`sha256:6e289896975c7eb76f5b0a280441a4bbf2436d21628b187ba881d1a03048aa7c`)

The normal rollback is image/config-only because the old reader was explicitly rehearsed against the
v15 additive database. From `/home/codex/jarvis-poc`, stop the task profile services with the protected
env file, restore `pre-ff69d8c-runtime.env` to `.env`, retag the rollback image as
`jarvis-poc-app:local`, and recreate the prior Jarvis/admission services with
`docker compose --env-file .env -f deploy/docker/compose.yaml ... --no-build`. Restore the database
backup with `python3 scripts/manage_database.py --database data/jarvis_v2.db restore --replace <backup>`
only for a full data rollback, after stopping all SQLite-writing Jarvis services.
