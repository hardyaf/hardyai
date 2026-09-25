# Native durable task workspace

The task workspace is the primary local supervision surface for multi-step Jarvis work. It extends the
existing platform; it does not create a second authorization system, provider layer, queue, or document
pipeline.

## Reuse and ownership

| Concern | Decision | Authority |
| --- | --- | --- |
| Claims, leases, retries, cancellation | Reuse | `DurableJobRepository`, job type `agent.task.v1` |
| Capability discovery and execution | Adapt | `SkillRegistryService` and `AuthorizedSkillExecutor` |
| Provider identity and authorization | Reuse | Existing domain handlers and protected configuration |
| External effect integrity | Adapt | Provider operation IDs plus task logical-operation receipts |
| Task lifecycle and messages | New shared boundary | Core SQLite `agent_tasks`, task events/messages/budgets |
| Explicit preferences and skill learning | New revisioned records | Core SQLite; shipped Markdown remains the base |
| Code execution | New isolated adapter | Fixed runner container and task-scoped Unix-socket broker |
| Local identity | Reuse | Operator session, CSRF, and trusted `operator` owner |

Calendar, Lists, Documents, Email, and future domains remain owners of their state and operations. Task
records store bounded observations, plans, and receipts. They are not provider mirrors. The workspace
volume owns task-local script inputs, work files, and published artifacts; paths are scoped below a hash
of the owner ID.

The operator key is exchanged once for a signed HTTP-only, SameSite session cookie; mutations also
require the bound CSRF token. `JARVIS_OPERATOR_SESSION_COOKIE_SECURE` must match the actual browser
transport: false for the existing local HTTP endpoint and true when an HTTPS reverse proxy is used.

## Execution flow

1. The API commits a task and `agent.task.v1` job before returning `202`.
2. The dedicated worker atomically claims one job, renews its fenced lease, and restores the same task,
   messages, plan, budget, effects, preferences, and recent useful history.
3. The local model returns native Ollama tool calls. Jarvis can discover/load skills, invoke any currently
   authorized typed capability, update the plan, ask the user, save an explicitly requested rule, run
   bounded Python, or finish.
4. Every capability call re-resolves its descriptor and authorization. Writes require a stable logical
   operation ID and persist a task receipt around the provider's existing idempotent operation.
5. Steering increments a durable revision immediately. The worker and script broker check it before the
   next effect. Budget, user-input, approval, and user pauses complete the current job without retry spin;
   continuation enqueues a new generation for the same task.

## Python boundary

Only `task-runner-launcher` mounts the Docker socket. It runs as the deployment UID with only the
socket's supplemental group. Its API accepts task/run/workspace references and a one-run broker token;
image, command, mounts, network mode, UID, memory, CPU, process, filesystem, and timeout policy are
fixed by trusted configuration. Generated code is non-root, has a read-only root,
no network, no secrets, immutable source input, a per-run writable work directory, and the task's
published-artifact directory. Provider calls cross a Unix socket to the worker and repeat task owner,
scope, budget, pause, steering, lease, schema, authorization, approval, and operation-identity checks.

This provides bounded replay and duplicate-effect prevention, not distributed exactly-once execution.
Uncertain provider outcomes remain visible for readback/reconciliation.

## Learning order

Current task directions override project preferences, then active skill-specific preferences, general
preferences, and shipped defaults. User skill revisions contain instructions only and cannot grant a
tool, credential, account, or resource. Editing, retirement, history, export, and restoration are local
operator operations; restoration creates a new auditable revision.

## Operations and rollback

The `tasks` Compose profile starts the worker and trusted launcher. The app image also serves the task UI
and API. Deployments build and acceptance-test immutable candidate app/runner images, retain the prior app
image, use SQLite's online backup API, and then recreate admission before the API and task services with
`--no-build`. Image rollback should retain the migrated additive database and task/effect history. Restore
an older database only for demonstrated corruption and only with effect reconciliation.
