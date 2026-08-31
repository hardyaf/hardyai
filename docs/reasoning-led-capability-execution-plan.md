# Reasoning-Led Capability Execution Plan

Status: `execution_in_progress`; P0A, P1, P2, and accelerated P5A complete; P3 framework rollout evidence remains open; P4 implementation is retained with activation absorbed by the ready P5F Email plan

Prepared: 2026-08-30

Current phase: `P5F Email reasoning and central-inbox management is ready for execution; implementation has not started`

Authority: this plan records the approved architecture. A later instruction to begin work authorizes
only the named phase or subphase. Completing one phase does not authorize the next phase, production
deployment, protected-configuration changes, or removal of rollback code.

## Objective

Replace Jarvis's capability-bounded classification flow with capability-bounded agency:

1. Main determines the user's goal from trusted request and session context.
2. Main selects a small number of relevant skills from an authorized discovery index.
3. Jarvis loads only the selected skills' typed semantic tools.
4. Main uses a bounded call/observe loop to complete, clarify, or safely stop.
5. Deterministic code owns authorization, validation, approval, idempotency, execution, persistence,
   receipts, tickets, and limits.

The success criterion is not that Main recognizes more phrases. The success criterion is that a new
wording, date range, filter combination, or valid composition of existing operations normally requires
no new central intent, regex, router branch, or workflow-specific handler branch.

## How to execute this plan

Every implementation turn must follow these rules:

1. Read this document and the repository `AGENTS.md` before changing files.
2. Work on one phase or independently gated subphase only.
3. Confirm the entry gate and record the baseline before editing.
4. Keep the changed-file list inside the phase allowlist. An allowlist expansion is a plan revision,
   not an implementation judgment.
5. Use the stable task IDs in this document in the worklog and final handoff.
6. Add characterization tests before changing existing behavior.
7. Do not mark a task complete without recording the command and result in the implementation record.
8. Do not mark a phase complete when a required test, rollback check, or authoritative Ubuntu gate was
   skipped.
9. Preserve unrelated user changes. Do not reformat or rename unrelated code.
10. Stop on a listed stop condition. Report the phase/task ID, evidence, files already changed, and the
    smallest decision required to continue.

## Locked decisions

These decisions are not left to implementation agents:

1. Main is the sole semantic reasoning plane in the target architecture.
2. No new Micro behavior is permitted. Micro remains temporary rollback compatibility until `P10B`.
3. Accepted `!` syntax may remain as a user-interface alias, but it grants no authority and eventually
   enters the same Main path as unprefixed input.
4. Keep the outer `conversation | clarify_action | execute_action` commitment, but make its new-path
   `execute_action` a generic action candidate, not a member of `MAIN_ACTION_INTENTS`. A bounded tool loop
   runs only beneath that generic commitment; operation choice happens later through skill selection.
5. `SkillRegistryService` and domain-owned skill contracts remain the capability authority. Do not add
   another registry.
6. Main receives semantic tools, never raw provider SDKs, credentials, Python execution references,
   storage references, or unrestricted endpoint discovery.
7. Every tool call is re-resolved and reauthorized against the current principal, user, agent, channel,
   configuration, health, and operation policy immediately before execution.
8. `HumanReviewService` is the pre-action approval authority. `ActionTicketService` is post-action
   verification and remediation. Neither substitutes for the other.
9. All Human Review notifications, including formal action approvals, are delivered to one named
   protected Discord destination and are accepted only from the configured immutable approver identity
   in that destination. Literal Discord IDs remain in protected runtime configuration and fake test
   fixtures, never tracked documentation.
10. Email sending, replying, forwarding, and deletion are not introduced by this overhaul. No such
    tools are projected.
11. Calendar deletion always requires formal approval. Calendar creation with invitees also requires
    formal approval because it sends external invitations.
12. Destructive collection operations such as clearing or deleting a list require formal approval.
    Removing explicitly identified individual items does not.
13. Scheduler-only, adapter-only, operator-only, context-only, prohibited, staged, or unimplemented
    operations are not interactive Main tools.
14. Email read/query is the first active proving slice. Its source is the bounded local email projection;
    it reports projection freshness and does not silently broaden to Gmail or trigger provider sync.
15. The migration controls are `MAIN_TOOL_EXECUTION_MODE=off|shadow|active`, default `off`,
    `MAIN_TOOL_ENABLED_DOMAINS`, default empty, and `MAIN_TOOL_ENABLED_OPERATIONS`, default empty. Active
    dispatch requires both the owning domain and the exact operation ID; publishing a write descriptor
    never activates it. `MICRO_MODEL_ENABLED` is not reused as a control.
16. Shadow mode cannot execute domains, create approvals or tickets, enqueue domain work, or contact
    providers.
17. Mode coexistence is closed: `off` is the only legacy semantic-action path; `shadow` keeps legacy
    responses/effects authoritative while the new path observes only; `active` uses the new path for
    every semantic action candidate and never falls through to legacy. A disabled, revoked, unmatched,
    or not-yet-migrated operation returns typed unavailable/denied behavior. Removing an operation ID
    disables it; only setting mode `off` restores legacy semantic execution globally.
18. Mode precedence is evaluated in the trusted adapter before Micro routing. In `active`, every accepted
    semantic turn, prefixed or unprefixed, bypasses Micro and enters Main; `!` is only retained UI syntax.
    In `off|shadow`, legacy Micro routing may continue according to
    `LEGACY_MICRO_ROUTING_ENABLED` until P10A. Micro can never bypass active operation kill switches.
19. Cross-domain work is composition of independently authorized tools. Domains do not import other
    domains' handlers, tables, stores, or concrete services.
20. No distributed transaction or automatic compensation system is added. Partial completion is
    reported explicitly; compensation is a new authorized action.
21. Existing stores, jobs, sessions, identities, reviews, tickets, events, receipts, and provider
    adapters are reused. This plan adds contracts and rows to existing authorities, not parallel systems.
22. Windows is a source-preparation environment only. Authoritative build, test, model evaluation,
    deployment, canary, and runtime verification occur on the authoritative Ubuntu runtime.

## Scope control

### In scope

- A two-stage skill discovery and selected-tool projection.
- Typed semantic tool declarations for existing interactive capabilities.
- A bounded Main call/observe loop.
- Per-operation effect, sensitivity, approval, persistence, idempotency, and result contracts.
- Durable pre-action approval routed through protected Discord configuration.
- Post-action ticket, receipt, verifier, and restart-safety hardening needed by multi-step execution.
- Migration of existing Email, Lists, Calendar, Home/Lights, Documents, and web-research capabilities.
- Explicit non-interactive dispositions for Conversation, Memory, Private Notes, and Calendar Inbox.
- Main-only cutover, temporary compatibility, Micro retirement, Ubuntu promotion, and rollback.
- Correcting the protected destination for the reported misplaced proactive message after its sender is
  positively identified on the authoritative Ubuntu runtime.

### Out of scope

- New providers, provider credentials, or cloud services.
- Sending, replying to, forwarding, or deleting email.
- Physical home-device integration; current Home state remains explicitly simulated.
- A new task, contact, memory, identity, policy, approval, event, session, registry, queue, or durable-job
  system.
- A general parallel-agent runtime or background autonomous agent.
- A UI redesign, Discord bot redesign, or dashboard redesign beyond approval presentation and required
  compatibility labels.
- A vector database, semantic memory implementation, or migration of interaction history into facts.
- Provider upgrades, model migration, dependency upgrades, formatting sweeps, or unrelated hotspot
  refactors.
- Making Private Notes, Calendar Inbox reconciliation, raw document processing, or operator maintenance
  conversationally callable.

Valuable discoveries outside this scope go into the deferred table at the end of this document.

## Audited current state

The read-only whole-codebase audit is complete. These findings are implementation inputs, not open
questions:

- `Intent`, `FAST_COMMAND_INTENTS`, and `MAIN_ACTION_INTENTS` form a closed global executable
  vocabulary. A valid registry operation is not independently executable.
- Main has three overlapping semantic paths: fixed-intent repair, one-shot turn commitment, and
  hard-coded List/Light planning. The existing loop walks a prebuilt command list and reclassifies
  commands through Micro; it is not a model-directed call/observe loop.
- Main loads compact contracts for all authorized candidate intents before skill selection. Compact
  contracts include `execution_ref`, which must not be model-visible.
- Model-visible intent contracts contain only purpose, `read|write`, and field names. They cannot express
  types, output schemas, effects, sensitivity, approval, idempotency, reversibility, or async behavior.
- Calendar has competing registry identities: seeded `skill.calendar.core` and Markdown
  `skill.productivity.calendar`. Resolution can depend on registry history.
- Lights documents `home.get_switch_state`, but the global intent set, handler, and context contract do
  not implement it. Receipts also reference stale `home.list_switches` naming.
- The active Memory Markdown points to `app.skills.domains.memory.handler:run`, but that package does not
  exist. The implemented `MemoryService` owns interaction history, not structured fact CRUD.
- Email collection reads expose one free-form query. Code recognizes only `today` and variants of
  `week`, and storage has a lower timestamp bound but no upper bound. Unknown explicit source routes may
  broaden to no source filter.
- Calendar view collapses ranges to daily or weekly. Calendar create can duplicate on retry, and delete
  executes without formal approval.
- Lists hides workflow decisions in handlers: mechanical item splitting, special-case list auto-create,
  shared-owner fallback, and implicit clear-all behavior.
- Documents has the strongest authorization, provider-port, restricted-read, review, and async patterns,
  but only a subset of its executable operations has model-safe contracts.
- Web research is a pre-response side lane. It can choose only one query and cannot participate in an
  iterative evidence loop.
- `HumanReviewService` is suitable for pre-action approval but does not yet enforce its stored
  `authorization_binding` during execution. Conflicting idempotency-key reuse also needs rejection.
- `ActionTicketService` records and verifies effects but does not authorize them. Multi-receipt capture,
  replay, watchdog, and remediation recovery have partial-completion gaps.
- Protected Discord permissions are the delivery authority. Private Notes, compute notices, and document
  completions use distinct implementations. `MemoryService` itself sends no Discord messages, so the
  reported misplaced message must be attributed on the authoritative Ubuntu runtime before rerouting it.
- The repository has broad characterization coverage, but the Main model acceptance manifest contains
  only six cases and does not exercise typed tools, arbitrary intervals, iterative observations, or
  approval resumption.

## Target architecture

```text
trusted transport/principal/session/context
                    |
                    v
 MainActionCommitment: conversation | clarify | action candidate
           | conversation/clarify terminate |
                    v
        authorized skill discovery cards
                    |
        Main emits typed SkillSelection
                    |
                    v
      selected skill context + effective tools
                    |
                    v
       Main emits exactly one typed ModelStep
          | respond | clarify | call_tool |
                    |
                    v
 schema -> policy -> approval -> authorization -> dispatch
                    |
                    v
       bounded, sensitivity-filtered observation
                    |
             repeat <= hard limits
                    |
                    v
 session/finalizer + domain receipt + ticket when applicable
```

### Required shared contracts

Add these exact shared concepts. Names may not be changed without updating this plan first.

#### `MainActionCommitment`

Owner: adapt `app/core/main_turn_contract.py`, `app/core/main_turn_commitment.py`, and the corresponding
Main backend prompt. The mode flag chooses one of two closed validators: `off` retains the legacy
intent-bound validator for rollback; `shadow` evaluates the new validator without controlling the user
response; `active` makes the new validator authoritative.

The new-path object contains only `mode`, bounded numeric `confidence`, enumerated `reason_code`, and the
mode-specific `message` or `question`:

- `conversation`: `reason_code=informational|social|non_actionable`, a complete non-empty `message`, and
  no question.
- `clarify_action`: `reason_code=missing_referent|ambiguous_goal`, one direct `question`, and no action
  identity. This mode is only for ambiguity that prevents even safe skill selection. It uses `no_store`
  pending semantics and requires the user to restate the complete goal. Missing fields for a selected
  skill belong to `ModelStep.clarify`, not this outer mode.
- `execute_action`: `reason_code=plausible_action`, no message/question, and delegation of the original
  trusted request to `SkillSelection`.

The new object has no `intent`, `entities`, tool ID, arguments, principal, permission, or free-form
reasoning field. Its prompt contains no `MAIN_ACTION_INTENTS` list. A server-observed legacy intent may be
kept as compatibility telemetry, but never enters the object, authorizes a tool, narrows discovery, or
bypasses selection. Unknown imperative wording may therefore become a generic action candidate; if no
authorized skill matches, `SkillSelection.no_match` or a later typed clarification stops safely.

The mode split is exhaustive and is resolved before the legacy Micro prefix branch. In `off`, only the
legacy commitment/router/executor, including Micro when its temporary flag is true, can produce semantic
action effects. In `shadow`, that legacy result remains authoritative and the new path is observation
only. In `active`, every prefixed or unprefixed semantic action candidate enters the new commitment and bounded tool path; no
no-match, disabled operation, validation failure, denial, or unavailable selected tool may fall through
to Micro, a legacy intent, or a legacy executor. Those cases return a typed safe stop. Consequently, per-operation and
per-domain removal is a disable/deny control, while `MAIN_TOOL_EXECUTION_MODE=off` is the only full legacy
rollback control.

Authorization order is fixed: trusted transport/identity/session checks; semantic commitment with no
authority; server-built authorized discovery cards; one `SkillSelection`; selected descriptors filtered
by both rollout allowlists; closed argument validation; current per-call authorization/approval; dispatch.
No earlier stage grants a later one authority.

#### `ToolDescriptor`

Owner: `app/skills/tool_contracts.py`

Required server-side fields:

- `tool_id`: globally unique stable semantic name, for example `email.query_messages`.
- `skill_id` and `contract_version`.
- `purpose`.
- `input_schema`: closed JSON-schema-compatible object; extra fields rejected.
- `observation_schema`: closed bounded result schema.
- `effect`: one value from the effect table below.
- `approval_rule`: `none | conditional | always | denied`.
- `approval_conditions`: allowlisted deterministic policy identifiers; empty unless conditional.
- `sensitivity`: `normal | private | financial | identity | highly_restricted`.
- `persistence`: `standard | redacted | no_store`.
- `idempotency`: `not_applicable | required`.
- `effect_cardinality`: `single | atomic_batch | independent_batch` using the exact semantics below.
- `transferable_observation_fields`: an immutable bounded tuple whose entries are closed objects with
  exactly `pattern` (a validated JSON-pointer pattern) and `scope=same_domain|cross_domain`. Empty denies
  observation-derived transfer. `cross_domain` also permits same-domain use. Duplicate/overlapping
  patterns are compilation errors. This is data
  provenance, never authority.
- `runtime_dependencies`: a duplicate-free tuple drawn from
  `action_approval | ticket_review | document_processing | email_operations`; empty means inline/no
  separate consumer. This is server/operator metadata and is never model-visible.
- `timeout_seconds`, `max_result_items`, and `max_observation_chars`.
- `legacy_intents`: compatibility names only; never required for new execution.
- `interactive`: false for scheduler/adapter/operator/context-only declarations.

The model-visible projection contains only `tool_id`, `purpose`, closed input schema, safe output shape,
effect, approval summary, bounded availability note, and a sanitized transfer contract containing the
descriptor's exact safe `{pattern, scope}` entries. It excludes implementation paths, storage, provider
names, credentials, policy configuration, principal fields, and legacy routing internals.
Publishing a descriptor does not activate it. An operation enters the effective active catalog only when
the owning domain is in `MAIN_TOOL_ENABLED_DOMAINS`, its exact `tool_id` is in
`MAIN_TOOL_ENABLED_OPERATIONS`, mode is `active`, and every request/principal/domain policy check passes.
Shadow uses the same two allowlists to bound evaluation but is incapable of dispatch below the model.
Mode `active` has no legacy fallback: a descriptor filtered out by either allowlist is unavailable even
if its compatibility intent still exists.

#### `SkillSelection`

Owner: `app/core/tool_loop_types.py`

This is the first model output beneath `execute_action`, before tool schemas are loaded. It is a closed
tagged object with exactly one variant:

- `select`: `selected_skill_ids` contains one to three distinct IDs copied exactly from the current
  discovery cards.
- `no_match`: `selected_skill_ids` is empty and `reason_code` is exactly
  `no_relevant_skill | needs_more_context`.

It contains no tool arguments, prose answer, implementation reference, policy, principal, or authority.
When deterministic authorization yields no discovery cards, the orchestrator returns `no_match` without
calling the model; an empty capability set is an authority boundary, not a reasoning problem.
Unknown, duplicate, or unauthorized IDs; more than three IDs; an empty `select`; a non-empty `no_match`;
or extra fields are invalid. Main gets one schema-correction retry, counted against the global failure
and step limits. A second invalid output ends safely without loading or dispatching a tool. Selection
occurs once per turn and cannot expand mid-loop. Resume recomputes availability for the same IDs and
fails closed when a required tool is no longer authorized.

#### `ModelStep`

Owner: `app/core/tool_loop_types.py`

Exactly one tagged variant is valid:

- `respond`: complete user-facing message; no tool or future-work promise.
- `clarify`: selected `tool_id`, validated partial arguments, explicit missing fields, and one question.
- `call_tool`: selected `tool_id`, model-generated `call_id`, argument object, and optional bounded
  `provenance_claims` required by the cross-tool provenance rules below.

The model does not emit `approved=true`, principal data, operation IDs, execution references, policy
overrides, or provider settings. Policy may convert a valid call into `waiting_for_approval`.

#### `ToolCallEnvelope`

Owner: `app/skills/tool_contracts.py`

The server adds the request ID, stable operation ID, session, principal, user, agent, source/channel
scope, descriptor version, authorization snapshot reference, and validated arguments. Only the server
can construct this envelope.

#### Domain argument canonicalization

Owner: the `AuthorizedSkillExecutor` protocol; each owning domain may implement a bounded
`canonicalize_tool_arguments(tool_id, validated_arguments, request_context)` hook. The default is an
identity function. It runs after current authorization and closed-schema validation but before argument
hashing, parent/child operation-ID creation, approval creation, ticket reservation, or dispatch. It may
perform bounded read-only resolution inside the already-authorized domain, but it may not mutate state,
call a write provider, expand authority, or return provider objects. Its output must pass the same closed
input schema and replace human/session aliases with domain-owned stable opaque refs. Stale, ambiguous,
unauthorized, or duplicate resolutions fail before identity creation. The hook is not model-visible.

Approval execution reruns the same canonicalization under the original identity/channel binding and
current authorization. Every canonical argument, target ref, parent/child hash, and resource version must
equal the approved values; otherwise execution is denied. Display aliases such as Email `E1` remain
presentation metadata and never enter an operation hash.

#### `ToolObservation`

Owner: `app/core/tool_loop_types.py`

Status is one of `ok | needs_input | waiting_for_approval | queued | denied | retryable_error | terminal_error`.
It contains a server-issued opaque `observation_ref` that is valid only inside this root request and has
no authority outside provenance resolution. It otherwise contains only schema-approved bounded fields, a safe user message, missing fields,
retryability, committed-effect state, and opaque receipt/review/job references. External content is
labeled untrusted and cannot modify instructions or the effective catalog.

#### `PendingToolCall`

Owner: the existing pending-interaction authority through an adapted typed payload.

It contains tool/version, validated partial arguments, expected fields, question, request/user/agent/
channel binding, expiry, a reserved call ordinal, a separate `partial_arguments_hash`, and optionally an
opaque review ID. It contains no final argument hash or operation ID until all required fields validate,
execution reference, credential, unrestricted provider object, or approval grant. Authorization is
recomputed on resume.

#### `RequestTemporalContext`

Owner: `app/core/tool_loop_types.py`; constructed by `MainToolLoop` from an injected UTC clock and
server-owned domain configuration.

Fields are `now_utc` as an aware ISO instant, `timezone` as a validated IANA name, and derived
`local_date`. The transport and model cannot supply or override them. Email uses its authorized Email
Agent timezone; Calendar uses the authorized target calendar's configured timezone; tools without a
domain timezone use UTC. An invalid or missing required domain timezone makes that temporal tool
unavailable rather than guessing. The selected-tool prompt receives this bounded temporal context, and
the server validates concrete normalized instants returned by Main. Tests use an injected fake clock;
production uses one composition-owned UTC clock.

#### `ActionApprovalProposal`

Owner: the existing Human Review domain, using an additive `action_proposals` table in the core database.

It contains proposal/review IDs, tool/version, canonical validated arguments or domain-owned opaque
references, argument hash, resource version, stable operation ID, original identity/channel binding,
sensitivity/persistence policy, destination key, expiry, lifecycle state, and optional
`batch_manifest_json`/`batch_manifest_hash` and `transfer_manifest_json`/`transfer_binding_hash`.
The batch manifest is null for `single|atomic_batch`; for `independent_batch` it contains exactly
`manifest_version=1`, parent operation/argument hash, expected child count, and the bounded ordered child
ID/index/target-hash/argument-hash entries defined by Stable operation identity.
`batch_manifest_hash = SHA-256(UTF-8 canonical JSON of the complete closed batch manifest)` using the
same canonical-JSON rules as operation identity. The transfer manifest is a closed content-free object with
`manifest_version=1`, immutable request hash/ID and requester/user/agent/channel binding hash,
destination tool/version/argument hash and bounded destination-pointer/value-hash entries, and at most 32
source entries. Each source entry contains root-local observation ref, durable source operation ID,
source skill/domain/tool/contract version, matched transfer pattern/scope, source pointer, source subtree
hash, sensitivity, persistence, and untrusted flag. For conservative `request_derived` exposure,
`source_pointer=""` (the RFC 6901 root) and the source subtree hash is the complete bounded
observation-payload hash. The
binding hash is SHA-256 of canonical JSON of this complete manifest. Highly restricted values,
credentials, source bytes, source values, raw email bodies, and raw document text are never stored here.
The turn-local observation ref is audit correlation only; approved restart uses the durable operation and
descriptor identities.

#### Action approval lifecycle

`action_proposals.state` is closed to
`pending | approved | executing | executed | rejected | expired | superseded | canceled | denied | failed_terminal`.
The existing Human Review decision records remain the immutable human-decision authority; proposal state
tracks whether that decision was durably executed. Only these transitions are legal:

```text
pending -> approved | rejected | expired | superseded
approved -> executing | expired | canceled | denied
executing -> approved | executed | denied | failed_terminal
```

Every state on the right with no outgoing arrow is terminal. `executing -> approved` is permitted only
by the existing lease-recovery/retry path after durable reconciliation proves no effect or durable enqueue
committed; it retains the same proposal, operation ID, job, and fencing lineage. An uncertain effect never
returns to `approved` and must reconcile to `executed` or `failed_terminal` before another dispatch.

Proposal creation, the linked `review_items.state=pending` row, and exactly one pending
`review.notification.discord.v1` durable job commit in one Core SQLite transaction. The notification
job dedupe key is
`review-notification-discord:v1:<proposal_id>:<review_id>:<destination_purpose>`; same-key/same-payload
reuses the job and any mismatch conflicts. No code path may acknowledge `waiting_for_approval` unless all
three records committed, so a crash exposes either none of them or a claimable notification. An approve/reject
decision is accepted only while both are `pending`, before expiry, and with every binding/hash exact.
Reject atomically sets both records `rejected` and clears any purpose-bound payload. Approve atomically
stores the immutable decision, sets the review item and proposal `approved`, and enqueues exactly one
`review.action_execution.v1` job with dedupe key
`review-action-execution:v1:<proposal_id>:<operation_id>`; its closed payload contains only the proposal,
review, operation, authorization-binding, batch-manifest, and transfer-binding IDs/hashes needed for
execution. Same-key/same-payload reuse returns the existing job; any payload mismatch is a terminal
conflict. Enqueue failure rolls the entire decision transaction back. Duplicate identical decisions
return the existing decision; conflicts fail.

The worker may claim only an unexpired `approved` proposal and atomically moves it to `executing` under
the durable job's lease/fencing token. Immediately before dispatch it rechecks every binding, descriptor,
source disclosure when present, resource version, and both allowlists. Revocation or mismatch before any
effect sets the proposal `denied`; an expired unclaimed approval becomes `expired`. A synchronous commit
or truthful durable domain enqueue with its receipt/job ref sets proposal and linked review item
`executed`. Bounded retryable failure follows the one recovery edge above; exhausted non-effect failure
sets `failed_terminal`. Cancellation is allowed only from `approved` after proving no claim/effect;
it atomically makes the matching unclaimed execution job terminal/ineligible under the same transaction.
Supersession is allowed only from `pending`. A crash in `executing`, or cancellation/expiry racing a
claim, must reconcile the owning operation reservation before any terminal transition.

`review_items` keeps its existing states: it mirrors `pending`, `rejected`, `superseded`, expiry while
still pending, the human `approved` decision, and successful `executed`. If an already-approved proposal
later becomes `expired|canceled|denied|failed_terminal`, the immutable review item remains `approved` and
the proposal records the truthful execution outcome; it is never relabeled as a human rejection. Every terminal proposal
state atomically clears the optional destination-argument payload. Terminal states cannot be reopened;
a retry after terminal requires a new root request, proposal, review, and operation ID.

### Persistence-policy semantics

Owner: adapt the existing `app/core/persistence_policy.py`; enforcement points are `MainToolLoop`, the
existing turn finalizer, pending-interaction adapter, session/recent-turn projection,
conversation-history writer, `MemoryService`, and typed event/telemetry builders. The order is
`standard < redacted < no_store`; a multi-tool turn uses the most restrictive policy of every accepted
call or pending tool. The policy must be known before any generic request/response write. Raw
`ToolObservation` objects are never written to generic history under any policy.

The descriptor values above are canonical. During compatibility, existing policy name `standard` maps to
`standard`, `sensitive_domain` maps to `redacted`, and both `restricted_read` and `ephemeral` map to
`no_store`; these aliases may only preserve or strengthen policy. The legacy intent-prefix helper remains
for the legacy path, but the new path takes policy from the re-resolved descriptor and may not weaken it.

- `standard`: the existing bounded user request and final user-visible response may enter ordinary
  session/conversation/interaction history. Only schema-approved safe summaries, never hidden reasoning,
  provider objects, credentials, or raw observation envelopes, may enter traces or events.
- `redacted`: the live user receives the bounded response, but generic session, recent-turn,
  conversation-history, MemoryService, trace, and event writers receive only a server-generated surrogate
  containing tool ID, terminal status, safe counts, committed state, and opaque receipt/review/job refs.
  They receive no request text, argument values, observation content, or model-generated answer. The
  purpose-bound pending store may retain only the minimum closed-schema arguments needed to resume until
  expiry; it may not feed those values into generic memory/history.
- `no_store`: request text, argument values, observations, and every model-generated answer that consumed
  an observation remain live-turn only. Generic session/recent-turn summaries, conversation history,
  `MemoryService`, traces, and events retain at most content-free lifecycle IDs, tool ID, status, counts,
  timing, and opaque domain audit references. A pending clarification stores only bindings, tool/version,
  reserved ordinal, present/missing field names, and `partial_arguments_hash`; it stores no argument
  values. Its question must tell the user that all required values must be resubmitted, and restart/resume
  fails closed if a complete call cannot be reconstructed from the new message alone. Domain-owned
  restricted access audits and receipts remain subject to their own retention rules.

No component may downgrade the effective policy. Async memory/history jobs receive the already-filtered
surrogate or no job at all; they may not reconstruct content from request/session records. Tests inspect
all generic persistence sinks, their file projections, pending rows, queued payloads, and telemetry after
Email/Documents calls and after restart.

One narrow purpose-bound exception exists for P9 cross-domain declassification: an unexpired formal
`ActionApprovalProposal` may hold only the already-validated destination tool arguments needed to execute
the proposed effect, never the source observation, raw email body, raw document text, credentials, or
hidden reasoning. The proposal carries source/destination hashes and opaque refs, uses the source's
`no_store` lifecycle, and clears its argument payload immediately on execute, reject, expiry, cancel, or
terminal failure while retaining content-free IDs/hashes/status for audit. This exception does not permit
the payload in session, recent-turn, conversation, MemoryService, event, trace, ticket, or generic job
records. The protected approval card may show only the destination tool's bounded safe diff; it may not
show the source observation. Highly restricted content cannot use this exception.

### Stable operation identity

The model's `call_id` is correlation only and never the idempotency authority. A clarification reserves a
one-based `call_ordinal` within the original root request and stores only the canonical hash of validated
partial arguments as `partial_arguments_hash`. It creates no operation ID. After every required field is
present and the complete normalized argument object validates, the server computes:

```text
arguments_hash = SHA-256(UTF-8 canonical JSON of normalized validated arguments)
operation_id = "toolop_v1_" + SHA-256(
  root_request_id + "\n" + tool_id + "\n" + contract_version + "\n" +
  call_ordinal + "\n" + arguments_hash
)
```

Canonical JSON uses sorted object keys, compact separators, UTF-8, normalized ISO instants, and rejects
NaN, infinity, and non-JSON types. `root_request_id` is the immutable transport request ID; duplicate
Discord delivery reuses it. A direct complete call receives the next ordinal; a completed clarification
reuses its reserved ordinal. The ordinal never changes on schema/executor retry, and the final
`arguments_hash` may differ from the earlier `partial_arguments_hash` without conflict.

After domain argument canonicalization and before accepting a complete call, the loop checks the root
request's accepted-call ledger by tool ID,
contract version, and complete argument hash. An `idempotency=required` call may be accepted only once per
root request: an identical later model call receives the first call's stored observation or pending/
receipt reference, creates no ordinal or operation ID, and never redispatches. Identical reads may execute
at most `MAIN_TOOL_MAX_IDENTICAL_READ_CALLS` times when iterative observation is useful; the next repeat
stops safely. A different accepted call advances the ordinal exactly once.

`effect_cardinality` has these closed meanings:

- `single`: the call has zero or one independently committed effect and one operation ID.
- `atomic_batch`: the owning domain validates and commits the entire bounded collection in one database
  transaction or returns no committed effect. The parent operation ID is the one effect/receipt ID.
- `independent_batch`: one validated parent call groups multiple independently committed provider or
  domain effects. Before the first dispatch, the owner durably reserves the complete bounded child
  manifest. Each child has its own effect, status, and receipt; the parent is grouping identity only.

For every independent batch, the server canonicalizes the semantically unordered target set by stable
opaque target reference, rejects duplicates, and hashes the normalized array into the parent arguments.
For one-based `child_index` in that canonical order it computes:

```text
child_arguments_hash = SHA-256(UTF-8 canonical JSON of that target's normalized arguments)
child_operation_id = "toolchild_v1_" + SHA-256(
  operation_id + "\n" + child_index + "\n" + canonical_target_ref + "\n" +
  child_arguments_hash
)
```

The manifest stores parent ID, expected child count, and for each child only its ID, index, target
hash/allowed opaque ref, argument hash, and `reserved|committed|failed|denied` state under the effective
persistence policy. A retry with the same parent reconstructs that manifest: committed children never
rerun; only unresolved retryable children may resume within their existing retry cap. A changed child
count/order/hash is a terminal conflict. Parent outcome is `completed`, `partial`, `queued`, or `failed`,
and every committed child has one receipt plus an aggregate content-minimized summary. Formal approval of
an independent batch binds the complete parent/child manifest; stale or unauthorized children fail
individually after the mandatory post-approval recheck.

The initial cardinality assignment is exact: `email.apply_labels`, `email.remove_labels`,
`email.mark_read_complete`, and `email.move_to_spam` are `independent_batch`;
`email.set_review_state`, `email.correct_local_category`, `lists.add_items`, `lists.remove_items`, and
`documents.confirm_fields` are `atomic_batch`; every other initial descriptor is `single`. Email
`message_refs` are therefore sorted and duplicate-rejected before hashing. Lists item text order remains
meaningful and is preserved inside its one atomic batch.

Initial runtime dependencies are exact:

- `action_approval`: `email.move_to_spam`, `lists.clear_collection`, `lists.delete_collection`,
  `calendar.create_event_with_invites`, and `calendar.delete_event`; a dynamic P9 transfer approval adds
  this dependency to that proposal even when the descriptor normally omits it.
- `email_operations`: `email.apply_labels`, `email.remove_labels`, `email.mark_read_complete`, and
  `email.move_to_spam`.
- `document_processing`: `documents.queue_processing`.
- `ticket_review`: every ticket-eligible Email, Lists, Calendar, or Home write after P7. The accelerated
  P5A `lists.create_collection` and `lists.add_items` slice is initially protected by its Lists-owned
  atomic operation receipt and does not claim post-action verification; P7 attaches the shared ticket
  lifecycle before any broader Lists mutation activates.
- Every other initial descriptor has an empty tuple.

Protected enabled flags, active exact operation IDs, these descriptor dependencies, and unfinished
durable work jointly determine required runtime consumers. A container's prior running/stopped state is
never evidence that a required consumer is optional.

Approval persists the final identity in `ActionApprovalProposal`; approved work persists it in the
durable job. Before any non-approval effect, the final identity is durably reserved in its owning
idempotency authority: the ticket execution entry for ticket-eligible tools, the existing domain
operation ledger where one exists, or the restricted Documents operation/job record defined in P8C.
Replay reuses the stored ID and never reruns Main. Reuse of one operation ID with a different root, tool,
version, ordinal, or final hash is a terminal conflict. Reads may carry an operation ID for trace
correlation, but an operation marked `idempotency=required` cannot dispatch until the owning durable
reservation succeeds.

### Cross-tool provenance and declassification

There is no workflow/composition allowlist and no compound intent. A multi-call turn may compose only
the exact operations independently present in the active domain and operation allowlists. Each later call
must still pass its own descriptor validation, current authorization, effect policy, approval, stable
identity, and dispatch checks. An earlier observation or approval grants no authority.

For `ModelStep.call_tool`, add an optional closed `provenance_claims` array of at most 32 non-overlapping
destination subtrees. Every entry is exactly one tagged variant:

- `request_derived`: `kind`, `destination_pointer`, and
  `derivation=interpret|normalize|extract|summarize`. The server binds the immutable request hash and
  destination-subtree hash; Main need not reproduce a phrase parser or an ISO value verbatim.
- `observation_derived`: `kind`, `destination_pointer`, `source_observation_ref`, `source_pointer`, and
  `derivation=copy|extract|summarize`.

Claims are provenance, not instructions or authorization. A destination pointer may cover one bounded
closed-schema subtree; overlapping/conflicting claims or an uncovered model-derived leaf fail before
operation-ID creation. For an observation claim, the server validates that both pointers resolve to
bounded closed-schema subtrees, the source operation belongs to this live root request, the source field
matches an immutable `{pattern, scope}` descriptor entry, and the destination passes the next tool's
closed schema. The server resolves `source_observation_ref` through a root-request-local map that is
never model-writable. It then records a `CrossToolTransferBinding` containing source/destination tool and
contract versions, request/source operation IDs, JSON pointers, subtree hashes,
sensitivity/persistence, derivation, and untrusted status; it never copies a provider object or stores
the source value in that binding. A transformation's semantic quality remains model output, but its data
origin and authority are explicit.

After any observation, every later argument leaf must be classified by the server as exactly one of:

- `trusted_request`: the normalized scalar is present verbatim in the immutable user request;
- `request_derived`: it is covered by one valid request claim bound to the immutable request hash;
- `server_resolved`: a deterministic resolver produced it from a trusted selector under current policy;
- `observation_derived`: it has one valid observation claim; or
- `unproven`: no valid origin exists, so the call is rejected before operation-ID creation.

For strings, `trusted_request` requires Unicode-normalized, whitespace-collapsed exact substring match;
for numbers, booleans, dates, and enums it requires an exact normalized token match. Model-generated
paraphrases, date normalization, summaries, and extracted values may instead use `request_derived`; they
never require a deterministic phrase parser merely to prove origin. If a model omits or conflicts on a
required claim, the server rejects the call; it does not guess provenance. Server-resolved values must
name the resolver in content-free telemetry and cannot contain observation content.

Once an observation exists, a same-domain `request_derived` value remains valid because every call is
independently authorized within that domain. Every later model-derived subtree also inherits the union of
the sensitivity, persistence, and untrusted taints of every prior observation still visible to that model
step; a provenance claim can identify intended source fields but cannot remove ambient taint. For a later
cross-domain call, compatibility must hold for every taint source, any `highly_restricted` taint denies the
call, and any `no_store` taint requires formal Human Review. A non-verbatim `request_derived` subtree also
requires every taint source to expose at least one cross-domain field and binds all prior source operation
IDs/payload hashes. An `observation_derived` claim must match its named cross-domain field but still binds
the other observations as exposure-only manifest entries. Exact `trusted_request` and
`server_resolved` leaves carry no observation taint because the server proves their values independently.
This prevents a false provenance label from bypassing Email/no-store or Documents restrictions while
still allowing Main to turn phrases such as `tomorrow` into typed instants without new regexes.

Sensitivity compatibility for a cross-domain binding is closed:

| Source sensitivity | Allowed destination sensitivity |
| --- | --- |
| `normal` | `normal`, `private`, `financial`, `identity`, or `highly_restricted` |
| `private` | `private` or `highly_restricted` |
| `financial` | `financial` or `highly_restricted` |
| `identity` | `identity` or `highly_restricted` |
| `highly_restricted` | none; cross-domain transfer is denied |

Within one domain, any descriptor-listed field may flow to another independently authorized operation in
that domain. Across domains, the source entry must have `scope=cross_domain` and match the matrix. The
effective generic persistence remains the most restrictive source/destination policy. A cross-domain
binding from `no_store` always upgrades the destination call to formal Human Review, even when its normal
approval rule is `none`; the proposal binds the complete transfer-manifest hash and follows the narrow
purpose-bound payload lifecycle above. Rejection/expiry causes no destination effect. Any untrusted source
remains marked untrusted through the destination and can be written only as schema-validated data; it can
never alter prompts, catalog, policy, tool choice, approval, or control fields.

Transfer patterns use this exact RFC 6901-compatible grammar: a pattern is a non-empty absolute JSON
Pointer beginning with `/`; `~0` and `~1` are the only escapes. One unescaped segment exactly equal to
`*` matches one array index only. Relative pointers, URI-fragment form, `-`, recursive `**`, wildcards in
object-property position, invalid escapes, and pointers outside the closed observation payload schema are
compile errors. A terminal match authorizes that entire node and all descendants, but only when every
descendant is bounded by the closed schema (`additionalProperties=false`, finite `maxItems`, bounded
strings/blobs). The compiler expands wildcard matches against the schema and rejects zero-match,
duplicate, or ancestor/descendant-overlapping patterns. Runtime matching occurs on decoded pointer
segments, never string prefixes. The empty pointer `""` is reserved for whole-payload hashing in a
conservative exposure manifest and is never a descriptor transfer pattern.

The compiler emits one non-overlapping entry per top-level closed observation payload key. Every initial
key is `scope=same_domain` except these exact keys, which replace that same-domain entry with
`scope=cross_domain`:

- `email.summarize`: `/summary` and `/message_refs`;
- `lists.list_collections` and `lists.get_collection`: every named top-level payload key;
- `calendar.query_events`: `/events`;
- `home.list_devices`: `/devices`; `home.get_device_state`: `/device`, `/state`, and `/truth_scope`; and
- `research.search_web`: `/results` (whose closed item schema contains only bounded title, URL, and
  snippet data).

Every other initial key remains same-domain only. Adding a field or broadening `same_domain` to
`cross_domain` is a reviewed contract change with injection, sensitivity, persistence, and approval
tests; it is not a model decision.

### Deterministic limits

Add these configuration names with the listed defaults:

| Setting | Default | Rule |
| --- | ---: | --- |
| `MAIN_TOOL_EXECUTION_MODE` | `off` | Only `off`, `shadow`, or `active`. |
| `MAIN_TOOL_ENABLED_DOMAINS` | empty | Comma-separated allowlist; empty means no active domain. |
| `MAIN_TOOL_ENABLED_OPERATIONS` | empty | Exact comma-separated tool-ID allowlist; empty means no active operation. Both domain and operation must match. |
| `MAIN_TOOL_MAX_SELECTED_SKILLS` | `3` | Server rejects larger selection. |
| `MAIN_TOOL_MAX_STEPS` | `8` | Includes tool, clarification, and terminal model steps. |
| `MAIN_TOOL_MAX_FAILURES` | `2` | Terminal stop after the second failed dispatch/evaluation. |
| `MAIN_TOOL_MAX_IDENTICAL_READ_CALLS` | `2` | Applies only to reads. Identical effectful calls reuse the first accepted call and never redispatch. |
| `MAIN_TOOL_MAX_OBSERVATION_CHARS` | `8000` | Per observation after sensitivity filtering. |
| `MAIN_TOOL_MAX_TOTAL_OBSERVATION_CHARS` | `24000` | Across the turn. |
| `MAIN_TOOL_TIMEOUT_SECONDS` | `120` | Whole orchestration deadline, excluding a durable approval pause. |
| `LEGACY_MICRO_ROUTING_ENABLED` | `true` | Temporary rollback control consulted only in `off|shadow`; `active` always bypasses Micro. Set false in P10A and remove in P10B. |

The existing context and model-output limits remain additional ceilings. Raising any limit requires a
separate measured change and updated adversarial tests.

During foundation development and live canary testing, Hardybot uses an explicit higher-headroom
profile rather than changing these fail-safe defaults: `MAIN_TOOL_MAX_STEPS=12`,
`MAIN_TOOL_MAX_FAILURES=4`, `MAIN_TOOL_TIMEOUT_SECONDS=240`, Main repair and conversation
`NUM_PREDICT=2048`, adaptive exhaustion `MAX_ATTEMPTS=5` with `MAX_MULTIPLIER=16`, and the legacy
planner loop at `12` steps / `4` failures. Repair/conversation request timeouts are `90`/`120` seconds
and the outer turn timeout is `360` seconds. Other model roles are also given development headroom:
email summaries and ticket reviews use `NUM_PREDICT=2048`; Micro, email classification, and web-research
decisions use `NUM_PREDICT=512`. External-effect and durable-delivery retry counts are intentionally not
raised by this profile. This profile changes reasoning patience, not authority: operation
allowlists, approval policy, effect cardinality, idempotency, identical-read caps, and observation
ceilings remain unchanged. Re-tighten only after representative skill behavior is correct and measured.

## Effect and approval policy

Sensitivity is orthogonal to effect. A read can still be `private` or `highly_restricted` and require
strict channel/user scope or `no_store` persistence.

| Effect | Default approval | Additional rule |
| --- | --- | --- |
| `read` | `none` | Must still be currently authorized; results are bounded and sensitivity-filtered. |
| `local_write` | `none` | Only non-destructive writes with commit or durable enqueue and idempotency where repeatable. |
| `external_write` | `none` | Requires stable operation ID, provider-safe idempotency or reconciliation, receipt, and truthful commit state. |
| `destructive_local` | `always` | Individual item removal may use `local_write`; clear/delete collection uses this class. |
| `destructive_external` | `always` | Includes calendar deletion and moving email to spam. |
| `outbound_communication` | `denied` by default | Calendar invitations are a conditional exception requiring formal approval. Email send/reply/forward remain absent. |
| `privileged` | `denied` | Financial, legal acceptance, permission/security changes, credentials, and unsupported provider administration. |

A human decision may be accepted only while the linked review and proposal are `pending` and unexpired;
item and argument hashes, resource version, actor user ID, configured channel, destination purpose, and
decision idempotency key must match. Execution is valid only from the closed
`approved -> executing` transition with the stored approve decision, same bindings/manifests/operation
ID, current tool availability, and a fresh authorization check immediately before dispatch.

## Semantic tool contract matrix

This table locks every interactive target descriptor. `n/a` means `idempotency=not_applicable`; `req`
means `idempotency=required`. Limits are
`timeout seconds / maximum result items / maximum observation characters`. Observation payloads are
closed objects with exactly the named top-level keys; each domain
contract supplies bounded field types and rejects extras. The common `ToolObservation` wrapper carries
status, safe message, missing fields, committed state, and opaque references separately.

| Tool ID | Effect | Sensitivity / persistence | Approval | Idem. | Limits | Closed observation payload keys | Receipt / verification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `email.list_mailboxes` | `read` | `private / no_store` | none | n/a | `5 / 20 / 3000` | `mailboxes, coverage, truncated` | none |
| `email.list_labels` | `read` | `private / no_store` | none | n/a | `5 / 50 / 3000` | `labels, truncated` | none |
| `email.query_messages` | `read` | `private / no_store` | none | n/a | `10 / 50 / 8000` | `messages, normalized_query, result_set_ref, next_cursor, coverage, freshness_at, truncated` | none |
| `email.get_message` | `read` | `private / no_store` | none | n/a | `10 / 1 / 8000` | `message, managed_labels, source, freshness_at` | none |
| `email.get_thread` | `read` | `private / no_store` | none | n/a | `10 / 50 / 8000` | `messages, thread_ref, next_cursor, source, freshness_at, truncated` | none |
| `email.status` | `read` | `private / redacted` | none | n/a | `5 / 1 / 2000` | `counts, coverage, source, freshness_at, sync_state, backfill_state, worker_state` | none |
| `email.get_operation` | `read` | `private / redacted` | none | n/a | `5 / 50 / 3000` | `operation_ref, status, child_counts, error_code` | none |
| `email.set_review_state` | `local_write` | `private / redacted` | none | req | `10 / 50 / 3000` | `message_refs, state, changed_count, unchanged_count` | redacted domain receipts + tickets |
| `email.correct_local_category` | `local_write` | `private / redacted` | none | req | `10 / 50 / 3000` | `message_refs, category, changed_count` | redacted domain receipts + tickets |
| `email.apply_labels` | `external_write` | `private / redacted` | none | req | `10 / 50 / 3000` | `operation_ref, child_refs, delivery_state` | durable provider receipts; tickets attach in P7 |
| `email.remove_labels` | `external_write` | `private / redacted` | none | req | `10 / 50 / 3000` | `operation_ref, child_refs, delivery_state` | durable provider receipts; tickets attach in P7 |
| `email.mark_read_complete` | `external_write` | `private / redacted` | none | req | `10 / 50 / 3000` | `message_refs, delivery_state, job_refs` | durable provider receipts + tickets |
| `email.move_to_spam` | `destructive_external` | `private / redacted` | always | req | `10 / 5 / 3000` | `message_refs, delivery_state, job_refs` | durable read-back receipts + tickets |
| `lists.list_collections` | `read` | `private / redacted` | none | n/a | `5 / 100 / 4000` | `collections, owner_scope, truncated` | none |
| `lists.get_collection` | `read` | `private / redacted` | none | n/a | `5 / 100 / 6000` | `collection, items, owner_scope, truncated` | none |
| `lists.create_collection` | `local_write` | `private / redacted` | none | req | `10 / 1 / 3000` | `collection, created` | domain receipt + ticket |
| `lists.add_items` | `local_write` | `private / redacted` | none | req | `10 / 50 / 4000` | `collection_ref, added_items, existing_items, failed_items` | one domain receipt/effect + ticket |
| `lists.update_item` | `local_write` | `private / redacted` | none | req | `10 / 1 / 3000` | `collection_ref, item, changed` | domain receipt + ticket |
| `lists.remove_items` | `local_write` | `private / redacted` | none | req | `10 / 20 / 4000` | `collection_ref, removed_refs, missing_refs` | one domain receipt/effect + ticket |
| `lists.clear_collection` | `destructive_local` | `private / redacted` | always | req | `10 / 100 / 4000` | `collection_ref, removed_count` | domain receipts + ticket |
| `lists.delete_collection` | `destructive_local` | `private / redacted` | always | req | `10 / 1 / 3000` | `collection_ref, deleted` | domain receipt + ticket |
| `calendar.query_events` | `read` | `private / no_store` | none | n/a | `30 / 100 / 8000` | `events, normalized_range, calendar_scope, source, truncated` | none |
| `calendar.create_event` | `external_write` | `private / redacted` | none | req | `30 / 1 / 4000` | `event_ref, provider_revision, sync_state` | provider receipt + ticket |
| `calendar.create_event_with_invites` | `outbound_communication` | `private / redacted` | always | req | `30 / 1 / 4000` | `event_ref, invitee_count, provider_revision, sync_state` | provider receipt + ticket |
| `calendar.update_event` | `external_write` | `private / redacted` | none | req | `30 / 1 / 4000` | `event_ref, provider_revision, changed_fields, sync_state` | provider receipt + ticket |
| `calendar.delete_event` | `destructive_external` | `private / redacted` | always | req | `30 / 1 / 3000` | `event_ref, provider_revision, deleted` | provider receipt + ticket |
| `home.list_devices` | `read` | `private / redacted` | none | n/a | `5 / 100 / 4000` | `devices, truth_scope, truncated` | none |
| `home.get_device_state` | `read` | `private / redacted` | none | n/a | `5 / 1 / 2000` | `device, state, truth_scope` | none |
| `home.set_device_state` | `local_write` | `private / redacted` | none | req | `10 / 1 / 3000` | `device, state, changed, truth_scope` | simulated-state receipt + ticket |
| `documents.upload_capability` | `read` | `normal / standard` | none | n/a | `5 / 20 / 2000` | `endpoint_ref, accepted_types, limits` | none |
| `documents.search` | `read` | `highly_restricted / no_store` | none | n/a | `10 / 50 / 8000` | `documents, query, truncated` | restricted access audit only |
| `documents.status` | `read` | `highly_restricted / no_store` | none | n/a | `10 / 1 / 3000` | `document_ref, processing_state, safe_counts` | restricted access audit only |
| `documents.inspect` | `read` | `highly_restricted / no_store` | none | n/a | `10 / 64 / 8000` | `document_ref, fields, bounded_text, evidence_refs, truncated` | restricted access audit only |
| `documents.source_link` | `read` | `highly_restricted / no_store` | none | n/a | `10 / 1 / 2000` | `document_ref, source_link` | restricted access audit only |
| `documents.list_reviews` | `read` | `highly_restricted / no_store` | none | n/a | `10 / 100 / 4000` | `reviews, truncated` | restricted access audit only |
| `documents.queue_processing` | `local_write` | `highly_restricted / no_store` | none | req | `10 / 1 / 2000` | `document_ref, processing_tier, job_ref, delivery_state` | restricted domain job/receipt; no generic ticket |
| `documents.propose_metadata` | `local_write` | `highly_restricted / no_store` | none | req | `10 / 1 / 2000` | `document_ref, field, proposal_ref, review_ref` | domain proposal/review; no generic ticket |
| `documents.review_field` | `local_write` | `highly_restricted / no_store` | none | req | `10 / 1 / 2000` | `document_ref, field, decision_ref, applied` | bound domain review receipt; no generic ticket |
| `documents.confirm_fields` | `local_write` | `highly_restricted / no_store` | none | req | `10 / 64 / 3000` | `document_ref, confirmed_fields, decision_refs` | bound domain review receipts; no generic ticket |
| `research.search_web` | `read` | `normal / standard` | none | n/a | `20 / 8 / 8000` | `results, query, safe_search, cached, truncated` | none; all fields marked untrusted |

All rows above start at `contract_version=1` and `interactive=true`, except P5F advances
`email.query_messages` and `email.status` to contract version 2. Skill ownership is fixed as:
Email `skill.email.agent`; Lists `skill.lists.core`; Calendar `skill.productivity.calendar`; Home
`skill.home.lights`; Documents `skill.documents.local`; Research `skill.research.web`. `legacy_intents`
are exactly the matching rows in the operation-disposition matrix. Because different effect classes were
split, every initial `approval_conditions` list is empty.

Descriptor purposes are fixed as follows:

| Tool ID | Purpose |
| --- | --- |
| `email.list_mailboxes` | Enumerate currently authorized routed views as opaque mailbox references. |
| `email.list_labels` | Enumerate enabled Jarvis-managed additive labels as opaque references. |
| `email.query_messages` | Find authorized messages in the bounded local projection by optional interval, typed filters, and cursor. |
| `email.get_message` | Retrieve one currently authorized projected message. |
| `email.get_thread` | Retrieve the bounded thread containing a currently authorized message. |
| `email.status` | Report content-free Email projection, coverage, sync, backfill, and worker status. |
| `email.get_operation` | Report content-free state for one current Email provider operation. |
| `email.set_review_state` | Change local review state for current message references. |
| `email.correct_local_category` | Correct only the local projected category for current message references. |
| `email.apply_labels` | Add one or more enabled Jarvis-managed labels to current central-mailbox messages without removing other labels. |
| `email.remove_labels` | Remove only the selected enabled Jarvis-managed labels from current central-mailbox messages. |
| `email.mark_read_complete` | Queue an idempotent Gmail mark-read operation for current message references. |
| `email.move_to_spam` | After formal approval, queue a bounded Gmail spam move for current references. |
| `lists.list_collections` | Enumerate authorized list collections without item mutation. |
| `lists.get_collection` | Read one authorized collection and a bounded item set. |
| `lists.create_collection` | Create one authorized named collection idempotently. |
| `lists.add_items` | Add an explicit bounded item array to one collection. |
| `lists.update_item` | Change text or completion state of one explicit item. |
| `lists.remove_items` | Remove explicitly referenced individual items. |
| `lists.clear_collection` | After formal approval, remove every item from one version-bound collection. |
| `lists.delete_collection` | After formal approval, delete one version-bound collection. |
| `calendar.query_events` | Query an authorized calendar over an explicit bounded interval. |
| `calendar.create_event` | Create one event without attendees using a typed event specification. |
| `calendar.create_event_with_invites` | After formal approval, create one event and send invitations. |
| `calendar.update_event` | Apply a version-bound non-attendee patch to one event. |
| `calendar.delete_event` | After formal approval, delete one version-bound event. |
| `home.list_devices` | Enumerate bounded simulated Home devices. |
| `home.get_device_state` | Read the simulated state of one resolved device. |
| `home.set_device_state` | Set the simulated state of one canonical device idempotently. |
| `documents.upload_capability` | Describe the existing authenticated upload route and its limits. |
| `documents.search` | Search authorized restricted document metadata/text within existing bounds. |
| `documents.status` | Read processing status for one currently authorized document. |
| `documents.inspect` | Read bounded fields, evidence, and optionally bounded text for one document. |
| `documents.source_link` | Return the existing authorized source link for one document, never bytes. |
| `documents.list_reviews` | List bounded currently authorized document reviews. |
| `documents.queue_processing` | Idempotently queue an allowlisted processing tier for one document. |
| `documents.propose_metadata` | Create a Human Review proposal without applying metadata. |
| `documents.review_field` | Record one authorized bound field-review decision. |
| `documents.confirm_fields` | Confirm a bounded current field set for one document. |
| `research.search_web` | Retrieve bounded safe-search snippets from the configured read-only provider. |

Initial closed input shapes are also locked:

- `email.list_mailboxes`: empty object. `email.list_labels`: optional bounded text filter.
  `email.query_messages`: optional paired aware ISO `start` and exclusive `end`; omission means all
  indexed history. Optional unique arrays are `mailbox_refs`, exact `sender_addresses`, exact
  `sender_domains`, exact `recipient_addresses`, and `label_refs`, each at most 10. Optional fields are
  bounded `sender_text`, `label_match=any|all`, separately named legacy classification filter,
  `visibility=all|active|unseen|needs_reply|completed|spam`, `text` at most 200 characters,
  `has_attachment`, `order=oldest|newest`, `limit=1..50`, and one request-bound opaque cursor. Server
  inserts the selected Email timezone and rejects one-sided intervals, `start >= end`, changed cursor
  filters, or an unknown enum/ref.
- `email.get_message`: one current `message_ref`. `email.get_thread`: one current `message_ref` and
  optional `limit=1..50`/cursor. `email.status`: empty object. `email.get_operation`: one current opaque
  operation ref. The stored compatibility `email.summarize` descriptor stays inactive; Main summarizes
  query observations in its normal response step.
- `email.set_review_state`: one to 50 current `message_refs`, state
  `reviewed|snoozed|dismissed|needs_reply`, and `snooze_until` only/required for `snoozed`.
  Local category correction takes one to 50 current refs and one allowlisted classification category.
  Apply/remove labels take one to 50 current refs and one to 10 enabled managed-label refs. Mark-read
  takes one to 50 current refs; spam takes one to five.
- `lists.list_collections`: optional `limit=1..100`. `lists.get_collection`: exactly one of canonical
  `collection_ref` or bounded `name`, plus `limit=1..100`. Create takes `name` of 1..100 characters.
  Add takes one collection selector and an explicit array of 1..50 item strings, each 1..500 characters.
  Update takes canonical collection/item refs and a closed patch with `text` and/or
  `status=open|done`. Remove takes canonical collection ref plus 1..20 item refs. Clear/delete take
  canonical collection ref and `resource_version`.
- `calendar.query_events`: required aware ISO `start` and exclusive `end`, exactly one authorized
  calendar/person selector or the explicit authorized default, optional text at most 200 characters,
  and `limit=1..100`. Server inserts and validates the selected calendar timezone.
- Calendar create tools take `calendar_ref`, title 1..200 characters, a tagged `when` object of either
  aware timed `start/end/timezone` or all-day `start_date/end_date_exclusive`, and optional bounded
  location/description. The invite tool additionally requires 1..20 validated invitees; the non-invite
  tool rejects that field. Update takes `event_ref`, `resource_version`, and a non-empty closed patch of
  title/when/location/description; attendee fields are rejected. Delete takes `event_ref` and
  `resource_version`.
- `home.list_devices`: optional `limit=1..100`. Get takes exactly one canonical ref or bounded device
  name. Set takes only canonical `device_ref` and `state=on|off`; name resolution must finish before the
  call is accepted.
- `documents.upload_capability`: empty object. Search takes query 1..200 characters and `limit=1..50`.
  Status, inspect, and source-link take one currently authorized `document_ref`; inspect optionally takes
  `include_bounded_text`. List-reviews takes `limit=1..100`. Queue-processing takes authorized document
  ref and `tier=standard|review_fallback`. Propose-metadata takes document ref, allowlisted field, and a
  schema-bounded proposed value. Review-field takes current `review_ref`, field,
  `decision=accept|correct|reject`, and corrected value only/required for `correct`. Confirm-fields takes
  document ref and 1..64 current field refs.
- `research.search_web`: query 1..300 characters and `limit=1..8`.

`calendar.create_event` rejects invitees, and `calendar.create_event_with_invites` requires at least one;
`email.correct_local_category` never calls Gmail, while `email.apply_labels` and `email.remove_labels`
always use the isolated durable provider path and never alter classifier category. These splits prevent
one descriptor from changing effect class based on an argument. Any future interactive tool requires a
reviewed new row before implementation.

## Operation disposition matrix

Disposition values are `migrate`, `scheduler_only`, `adapter_only`, `operator_only`, `context_only`,
`prohibited`, `deactivate_stale`, or `deferred`. `Formal` means HumanReview plus protected Discord.

### Email

Gmail owns raw mailbox truth. The Email SQLite store owns the bounded interactive projection, references,
classification, and operation ledgers. All Email content is private and excluded from generic memory.

| Current operation | Current route | Target tool/disposition | Effect | Approval | Phase |
| --- | --- | --- | --- | --- | --- |
| mailbox catalog | absent | `email.list_mailboxes` / add | read | none | P5F |
| managed-label catalog | absent | `email.list_labels` / add | read | none | P5F |
| `email.list_recent` | Main | `email.query_messages` / migrate | read | none | P4 |
| `email.search` | Main | `email.query_messages` / migrate | read | none | P4 |
| `email.get_message` | Main | `email.get_message` / migrate | read | none | P4 |
| `email.get_thread` | Main | `email.get_thread` / migrate | read | none | P4 |
| `email.summarize` | Main | outer Main over query observation / deactivate stale descriptor | read | none | P5F |
| `email.discuss` | Main | outer Main over retrieved observation / migrate | read | none | P5F |
| `email.status` | Main | `email.status` / migrate | read | none | P4 |
| provider operation status | absent | `email.get_operation` / add | read | none | P5F |
| `email.mark_reviewed` | Main | `email.set_review_state` / migrate | local_write | none | P8D |
| `email.snooze` | Main | `email.set_review_state` / migrate | local_write | none | P8D |
| `email.dismiss` | Main | `email.set_review_state` / migrate | local_write | none | P8D |
| `email.mark_needs_reply` | Main | `email.set_review_state` / migrate | local_write | none | P8D |
| `email.correct_category` local projection | Main | `email.correct_local_category` / migrate | local_write | none | P8D |
| automatic category-to-Gmail label reconciliation | sync/Main compatibility path | deactivate stale; never enqueue from classification | external_write | n/a | P5F-E0 |
| additive managed-label apply | absent | `email.apply_labels` / add | external_write | none | P5F |
| additive managed-label remove | absent | `email.remove_labels` / add | external_write | none | P5F |
| `email.mark_complete` | Main | `email.mark_read_complete` / migrate | external_write | none | P8D |
| `email.mark_spam` | Main | `email.move_to_spam` / migrate | destructive_external | Formal | P8D |
| `email.sync` | scheduler | scheduler_only | local_write | scheduler policy | preserve |
| `email.promote_to_list` | staged | deferred cross-domain proposal | n/a | n/a | P9/follow-up |
| `email.promote_to_calendar` | staged | deferred cross-domain proposal | n/a | n/a | P9/follow-up |
| `email.promote_to_task` | unavailable | deferred; no task authority | n/a | n/a | follow-up |
| `email.promote_to_wave` | unavailable | deferred; no Wave authority | n/a | n/a | follow-up |
| send/reply/forward | absent | prohibited | outbound_communication | denied | excluded |
| delete email | absent | prohibited | destructive_external | denied | excluded |

### Lists

The Lists SQLite tables remain authoritative. Shared-owner fallback becomes explicit policy, not hidden
name resolution.

| Current operation | Current route | Target tool/disposition | Effect | Approval | Phase |
| --- | --- | --- | --- | --- | --- |
| `lists.get_items` | Main/Micro | `lists.get_collection` / migrate | read | none | P5A |
| list enumeration helper | internal | `lists.list_collections` / migrate | read | none | P5A |
| `lists.create_list` | Main | `lists.create_collection` / migrate | local_write | none | P5A |
| `lists.add_item` | Main/Micro | `lists.add_items(items[])` / migrate | local_write | none | P5A |
| `lists.mark_item_done` | Main | `lists.update_item` / migrate | local_write | none | P8A |
| `lists.remove_item` | Main | `lists.remove_items(item_refs[])` / migrate | local_write | none | P8A |
| implicit clear-all phrase | hidden | `lists.clear_collection` / migrate | destructive_local | Formal | P8A |
| `lists.delete_list` | Main | `lists.delete_collection` / migrate | destructive_local | Formal | P8A |

### Calendar

Google Calendar is authoritative when configured. The in-memory calendar remains a test/local fallback
and must not be presented as externally synchronized truth.

| Current operation | Current route | Target tool/disposition | Effect | Approval | Phase |
| --- | --- | --- | --- | --- | --- |
| `calendar.view` | Main/legacy Micro | `calendar.query_events` / migrate | read | none | P5B |
| `calendar.add_event` without invitees | Main | `calendar.create_event` / migrate | external_write | none | P8E |
| `calendar.add_event` with invitees | Main | `calendar.create_event_with_invites` / migrate | outbound_communication | Formal | P8E |
| `calendar.update_event` | Main | `calendar.update_event` / migrate | external_write | none; attendee mutation absent | P8E |
| `calendar.delete_event` | Main | `calendar.delete_event` / migrate | destructive_external | Formal | P8E |
| `calendar_inbox.reconcile` | scheduler | scheduler_only | external_write | scheduler policy | preserve |

### Home / Lights

`SQLiteLightsStorage` owns simulated state. Receipts must continue to state that they do not prove
physical-device truth.

| Current operation | Current route | Target tool/disposition | Effect | Approval | Phase |
| --- | --- | --- | --- | --- | --- |
| `home.set_switch` | Main/Micro | `home.set_device_state` / migrate | local_write | none | P8B |
| `home.get_switch_state` | documented only | `home.get_device_state` / implement advertised read | read | none | P5C |
| `list_switches` helper | API/internal | `home.list_devices` / migrate | read | none | P5C |
| `recent_actions` helper | operator/internal | operator_only | read | n/a | preserve |
| hidden `all lights` | handler | deferred `home.set_group_state` | local_write | n/a while deferred | follow-up |
| stale `home.list_switches` receipt | receipt only | deactivate_stale | n/a | n/a | P1/P5C |

### Documents

Paperless/provider originals remain authoritative. The isolated Documents database owns mappings,
derivatives, processing, review, proposals, and corrections. Results remain restricted and excluded
from generic memory and tickets unless a content-minimized policy explicitly permits them.

| Current operation | Current route | Target tool/disposition | Effect | Approval | Phase |
| --- | --- | --- | --- | --- | --- |
| `documents.ingest` | Main/operator navigation | `documents.upload_capability` / migrate | read | none | P5D |
| `documents.find` | Main/operator | `documents.search` / migrate | read | none | P5D |
| `documents.status` | Main/operator/scoped Discord | `documents.status` / migrate | read | none | P5D |
| `documents.get` | Main/operator/scoped Discord | `documents.inspect` / migrate | read | none | P5D |
| `documents.show_source` | Main/operator | `documents.source_link` / migrate | read | none | P5D |
| `documents.reprocess` | Main/operator | `documents.queue_processing(standard)` / migrate | local_write | none | P8C |
| `documents.escalate_ocr` | Main/operator/scoped Discord | `documents.queue_processing(review_fallback)` / migrate | local_write | none | P8C |
| `documents.list_reviews` | Main/operator | `documents.list_reviews` / migrate | read | none | P5D |
| `documents.propose_metadata` | Main/operator | `documents.propose_metadata` / migrate | local_write | none; creates domain review | P8C |
| `documents.correct_field` | Main/operator/scoped Discord | `documents.review_field` / migrate | local_write | none; existing binding | P8C |
| `documents.confirm_fields` | Main/operator/scoped Discord | `documents.confirm_fields` / migrate | local_write | none; existing binding | P8C |
| `processing_run_status` | internal helper | context_only | read | n/a | preserve |

### Conversation, research, memory, and adapter/scheduler domains

| Current operation | Current route | Target disposition | Effect | Phase |
| --- | --- | --- | --- | --- |
| `conversation.general` | Main | outer Main response; not a tool | n/a | P3 |
| `unknown` | Main | outer Main response/clarification; not a tool | n/a | P3 |
| web research decision/search | Main side lane | `research.search_web` / migrate | read | P5E |
| `memory.store_fact` | stale Markdown | deactivate_stale; defer structured memory | n/a | P1/follow-up |
| `memory.get_fact` | stale Markdown | deactivate_stale; defer structured memory | n/a | P1/follow-up |
| `memory.update_fact` | stale Markdown | deactivate_stale; defer structured memory | n/a | P1/follow-up |
| `memory.delete_fact` | stale Markdown | deactivate_stale; defer structured memory | n/a | P1/follow-up |
| `memory.list_memories` | stale Markdown | deactivate_stale; defer structured memory | n/a | P1/follow-up |
| interaction-memory add/recent | internal | context_only; preserve current authority | local_write | preserve |
| `private_notes.capture` | Discord adapter | adapter_only | local_write | preserve |
| `private_notes.compile_digest` | scheduler | scheduler_only | read | preserve |
| `private_notes.deliver_digest` | adapter/scheduler | adapter_only | outbound_communication | P6 destination audit |
| private-notes retention | scheduler/internal | scheduler_only | destructive_local | preserve |
| `calendar_inbox.reconcile` | scheduler | scheduler_only | external_write | preserve |

## Reuse map

| Boundary | Decision | Exact use |
| --- | --- | --- |
| `SkillRegistryService` and skill Markdown | adapt | Store canonical `main_tools` declarations in existing skill records and create discovery/selected projections. |
| Existing `skills` table | adapt | Add `main_tools_json` and contract version additively; legacy intent/Micro columns remain through rollback. |
| Core schema/migration authority | adapt | Add reader-compatibility metadata and ordered additive migrations; do not introduce a second schema path. |
| `RuntimeCapabilityProjector` | adapt | Split into discovery cards and selected effective tools; remove global-intent intersection from the new path. |
| `AuthorizedSkillExecutor` | adapt | Add tool-ID execution and per-call re-resolution; retain legacy intent method as a named shim. |
| `SkillExecutionDispatcher` | adapt | Add `execute_tool`; continue restricting implementations to domain packages; keep `execute` shim. |
| Domain handlers/services/stores | reuse/adapt | Expose typed operations without moving domain policy into Core. |
| Main commitment boundary | adapt | Keep outer modes, replace the new-path closed-intent gate with generic `MainActionCommitment`, and delegate the action candidate to `MainToolLoop`; retain the old validator only behind mode `off`. |
| Existing loop limits/trace concepts | adapt | Replace prebuilt command walking with typed model steps and bounded observations. |
| `SessionStore`, context, entity registry | reuse | Preserve continuity; add typed pending-tool projection, not a second session system. |
| Pending interactions | adapt | Store typed partial call or opaque review pointer and reauthorize on resume. |
| `HumanReviewService`/repository | adapt | Own exact-call approval and decision lifecycle using additive action-proposal data. |
| Durable job ledger | reuse | Add approval notification and approved-execution job types; no approval-private queue. |
| Protected Discord permissions | adapt | Add named operator destinations and keep real IDs protected. |
| Document completion authorization | reuse pattern | Re-resolve policy at send time and bind immutable guild/channel/user/message facts. |
| `ActionTicketService`, receipts, verifiers | adapt | Preserve post-effect role and make multi-receipt capture/replay/recovery complete. |
| `ExternalIdentityService` and principals | reuse | Bind original requester and immutable approver; revalidate at decision and execution. |
| `EventLogService` | reuse through typed builders | Persist content-minimized lifecycle events only. |
| shared tool/step/observation/approval contracts | add | Small contracts inside existing authorities. |
| new registry, queue, identity, memory, task, approval, or event subsystem | do not add | Stop and revise if an existing authority is concretely incompatible. |

## Data ownership and lifecycle

| Datum/effect | Authority | Retention, deletion, consistency, and recovery |
| --- | --- | --- |
| Static skill/tool declaration | Domain Markdown compiled by `SkillRegistryService` | Versioned with domain; SQL row is runtime authority; stale/duplicate declarations fail closed. |
| Schema-reader compatibility rows | Existing core migration authority | One immutable row per newer additive version; never grants compatibility for missing/destructive changes; retained P1 reader validates before startup. |
| Effective discovery/tool catalog | Request-ephemeral projector | Recomputed and reauthorized; never durable authority. |
| Tool call envelope | Request/session execution | Ephemeral except bounded audit IDs and required proposal/receipt; model cannot construct trusted fields. |
| Tool observation | Owning domain result plus ephemeral safe projection | Bounded by schema/sensitivity; external content remains untrusted; raw envelopes never enter generic history. `no_store` content and derived answers remain live-turn only. |
| Clarification | Existing pending-interaction/session authority | Expires/cancels under existing policy; reauthorized on resume; `no_store` retains hashes/field names but no argument values and requires complete resubmission. |
| Action approval proposal | Existing Human Review domain in Core SQLite | Exact call/hash/version/identity/expiry; independent-batch parent/child manifest and transfer manifest are content-free and hash-bound; the optional purpose-bound destination argument payload is cleared atomically on every terminal state. |
| Cross-tool transfer manifest | Existing Human Review `action_proposals` row | Closed content-free source/destination operation, descriptor, scope, pointer, value-hash, sensitivity, persistence, request, and identity bindings; canonical manifest hash detects conflict; no source value is stored; retained with the proposal audit after the destination payload is cleared. |
| Approval destination | Protected Discord permissions | Rechecked at notification/decision; real IDs never enter tracked tree. |
| Approval notification/execution work | Existing durable job ledger | Stable idempotency, leases, bounded retry, dead letter, restart recovery; opaque payload. |
| External/domain effect | Existing provider or domain store | One owner; success only after commit or truthful durable enqueue. |
| Operation idempotency/receipt | Owning domain plus receipt builder | Stable ID across retry/approval resume; duplicate dispatch cannot duplicate effect. Ticket-eligible tools reserve through their ticket execution entry; exempt restricted tools name a domain-owned reservation. |
| Lists mutation idempotency | Lists-owned `list_operations` in Core SQLite | Atomic with Lists mutation; redacted bounded result; retained for the life of referenced list data and included in Core backup/restore. |
| Home mutation idempotency | `switch_actions_log.operation_id` in Core SQLite | Atomic with simulated state change; same retention/backup as the existing action log; no physical-device claim. |
| Restricted Documents mutation idempotency | Existing durable-job idempotency for queueing plus Documents-owned `document_tool_operations` in encrypted Documents SQLite for proposal/review writes | Documents migration 15; no generic ticket or content in Core; operation/hash/status/opaque result refs only; atomic with the Documents mutation where both share Documents SQLite and reconciled across the existing queued boundary. The compatibility-aware version-14 image is the rollback reader. |
| Email mutation idempotency | Email-owned tables in Core SQLite | P5F migration 010 introduces `email_tool_operations` plus the additive managed-label child ledger. P7/P8D migration 013 extends the parent/ticket bridge and later mailbox effects without replacing P5F ownership. Legacy exclusive-category rows remain history and are never claimable by the new worker; every verified child owns one receipt. |
| Post-action verification | Action-ticket ledger | Created around a real effect; observations never replace provider/domain truth. |
| Operational event | Event log through typed safe-event builder | Opaque IDs/states/counts/error codes; no raw private content or hidden reasoning. |
| Interaction memory | Existing `MemoryService`/store | Remains interaction-history authority; receives only policy-filtered standard content or a redacted surrogate and receives no job for `no_store`. No structured facts are added. |
| Provider data | Existing Gmail, Google Calendar, Paperless, or SQLite owner | Existing refresh/deletion rules remain; projections expose source/freshness. |
| Legacy Micro state | Existing schema/session/event records | Read-compatible; stop creating at P10A; do not rewrite history or drop columns here. |

## Global invariants and stop conditions

Every phase stops without improvisation if:

- A required change falls outside the phase allowlist or overlaps unresolved user edits.
- A new global Main intent, phrase-specific router branch, or workflow-specific regex is proposed to fix
  natural-language variation.
- A new store, queue, registry, identity, memory, approval, task, or event authority appears necessary.
- A tool lacks explicit owner, schema, effect/cardinality, sensitivity, approval, persistence,
  idempotency, transfer, timeout, observation, and receipt classifications.
- A provider object, credential, execution/storage reference, unrestricted payload, or protected ID
  would become model-visible or tracked.
- An observation cannot be deterministically bounded and sensitivity-filtered.
- Authorization cannot be rechecked at the call and approval resumption.
- Approval cannot bind exact tool/version/arguments/hash/resource version/requester/channel/approver/expiry.
- Code could claim success before commit/durable enqueue, or a retry could duplicate an effect.
- Cross-domain code would import another domain's handler, tables, store, or concrete provider.
- A touched module over roughly 800 lines would gain responsibility without the extraction named in the
  phase.
- Baseline failures cannot be separated from failures introduced by the phase.
- Live credentials, production mutation, model/GPU work, or deployment is attempted from Windows.
- The reported misplaced Discord message cannot be positively attributed before its route is changed.
- A required safety, full-suite, model-evaluation, canary, or rollback gate fails.

Global invariants:

- New tool IDs come from domain contracts, not `Intent`.
- In `shadow|active`, the outer action commitment is generic; neither `MAIN_ACTION_INTENTS` nor any
  legacy intent enum may gate entry to skill discovery or tool execution.
- In `active`, the new semantic path is exclusive: no disabled, denied, invalid, unmatched, or
  not-yet-migrated action may invoke the legacy executor. Per-operation removal means unavailable;
  only global mode `off` restores the characterized legacy semantic path.
- Extra or unknown arguments fail before domain dispatch.
- The request-scoped catalog grants nothing until server recheck.
- Untrusted external content cannot grant tools or authority.
- Every step, repeat, retry, result, context, and elapsed-time bound is deterministic.
- No hidden chain-of-thought is persisted.
- Every committed multi-step effect has its own operation ID and receipt.
- Formal approval and post-action ticketing are never collapsed into one record.
- No tracked file contains a real approval or notification destination ID.

## Phase index

| Phase | Purpose | Runtime after phase | Status |
| --- | --- | --- | --- |
| P0 | Lock architecture and write this runbook | unchanged | complete |
| P0A | Remove the known public-tree hygiene blocker | unchanged | complete |
| P1 | Baseline controls, catalog integrity, and ratchets | legacy; new mode off | complete |
| P2 | Typed tools, discovery, projection, dispatch seam | legacy; new mode off | complete |
| P3 | Bounded Main loop and non-executing shadow | legacy response/effects | in_progress |
| P4 | Email read/query proving slice | implementation retained; activation absorbed by P5F | implementation_verified_activation_superseded |
| P5A | Lists end-to-end reasoning slice | Lists reads plus safe create/add may be active | complete |
| P5F | Accelerated Email reasoning and central-inbox management | Email routed reads plus additive managed labels may be active | ready_for_execution |
| P5B-E | Remaining read surfaces | per-domain active reads | not_started |
| P6 | Durable approval and protected Discord delivery | approval path available | not_started |
| P7 | Ticket/receipt/recovery hardening | safer effects | not_started |
| P8A-E | Existing writes by domain/risk | per-domain active writes | not_started |
| P9 | Cross-domain composition and partial completion | ordinary enabled-tool composition | not_started |
| P10A | Main-only cutover with dormant Micro rollback | Main semantic authority | not_started |
| P10B | Micro code retirement | Main only | review_required |
| P11 | Authoritative Ubuntu certification and release | deployed or rolled back | not_started |

## Phase execution protocol

The executor does not infer missing work. Each phase uses this protocol:

1. Record task IDs, baseline commit, changed files, commands, results, flag state, rollback evidence,
   compatibility debt, and deferred discoveries in this document's **Evidence ledger**. This plan file
   is implicitly allowed in every phase for checklist/status/evidence-only edits; architecture or scope
   edits require review before implementation continues.
2. Run the entry commands before editing. An existing failure is recorded as baseline only when its
   cause is understood and unrelated; otherwise stop.
3. Modify only an allowed file for the current phase. `ADD` means that exact new file is authorized.
   A file named only for tests may not be used to change production behavior.
4. Implement tasks in numeric order. A task is complete only when its named tests pass and its evidence
   is in the implementation record.
5. Run targeted tests after each task group, then the phase suite, lint/compile checks, full suite, and
   public-tree check. Run model/GPU, Compose, provider, and live Discord checks only on the authoritative
   Ubuntu runtime.
6. Compare `git diff --name-only` with the allowlist and inspect the complete diff before the exit gate.
7. Leave the next phase `not_started`; do not silently continue.

Common entry and exit commands, unless a phase replaces one explicitly:

```bash
git status --short
python -m ruff check app scripts tests --select E9,F63,F7,F82
python -m compileall -q app scripts tests
python -m pytest -q tests/unit/test_architecture_boundaries.py
python -m pytest -q
phase_export="$(mktemp -d)"
python scripts/export_clean_repo.py "$phase_export"
git diff --check
git diff --name-only
```

The full-suite result must distinguish new failures from the recorded baseline. The public-tree check is
always run against a fresh clean export because the local ignored context is intentionally private. At
plan creation, one tracked model-migration document has the only known clean-export failure. `P0A` owns
that exact repair and must produce a zero-finding clean export before P1. No later phase may treat a
public-tree finding as accepted baseline debt.

## P0A - Public-tree hygiene prerequisite

Status: `complete`
Depends on: `P0`
Runtime after phase: unchanged

Objective: remove the one known tracked personal-path finding so every later phase can use a genuinely
passing clean-export gate. This prerequisite changes documentation only and does not authorize code,
configuration, runtime, or deployment changes.

Allowed tracked file:

- `docs/QWEN38-MAIN-MIGRATION-PLAN.md`

This plan file remains implicitly allowed for checkbox/evidence updates. Every other file is forbidden.

Tasks:

- [x] `P0A-01` Record `git status --short`, create a fresh export, and confirm the exporter names only
  `docs/QWEN38-MAIN-MIGRATION-PLAN.md` as a tracked public-tree failure. Ignore raw-checkout findings from
  ignored/private/cache paths; the fresh export is the authority.
- [x] `P0A-02` In the allowed document, replace each personal absolute filesystem path with a symbolic,
  machine-neutral phrase such as `the protected model-artifact path`. Preserve the instruction's meaning,
  commands, headings, and all unrelated text. Do not insert the real path elsewhere.
- [x] `P0A-03` Inspect the complete one-file diff, create a second fresh export, and require both the
  exporter and explicit public-tree checker to exit zero.

Verification:

```bash
git status --short
baseline_public_export="$(mktemp -d)"
python scripts/export_clean_repo.py "$baseline_public_export"  # expected nonzero only for the named document
git diff -- docs/QWEN38-MAIN-MIGRATION-PLAN.md
verified_public_export="$(mktemp -d)"
python scripts/export_clean_repo.py "$verified_public_export"
python scripts/check_public_tree.py --root "$verified_public_export"
git diff --check
git diff --name-only
```

Implementation record - 2026-08-30:

- Baseline commit: `900f3ba340631675edda2dc1afbcae545d7dd420`.
- `P0A-01`: `git status --short` recorded the pre-existing OCR relocation (14 tracked deletions and
  untracked `docs/OCR/`), untracked `.test-tmp-phase23/`, and this untracked plan. A fresh temporary export
  exited 1 with exactly one finding: `personal absolute path: docs/QWEN38-MAIN-MIGRATION-PLAN.md`.
- `P0A-02`: the complete allowed-document diff contains four path-only substitutions: the authoritative
  deployment checkout, Ollama model-artifact path in two locations, and rollback-snapshot path now use
  symbolic protected-path labels. Headings, commands, hashes, gates, and unrelated text are unchanged.
- `P0A-03`: the one-file diff was inspected. A second fresh export copied 536 files and exited 0; the
  explicit checker against that export exited 0; `git diff --check` exited 0. `git diff --name-only`
  contained the allowed Qwen document plus only the recorded pre-existing OCR deletions; the plan evidence
  file remains untracked and every unrelated change remains untouched.
- Phase-attributable files: `docs/QWEN38-MAIN-MIGRATION-PLAN.md` and this plan's status/checklist/evidence
  fields only. Runtime/configuration changes: none. Rollback: revert the four substitutions and these
  evidence fields.

Exit gate: the second export and checker have zero findings; the phase-attributable diff contains only
the allowed document and this plan's evidence fields; every unrelated pre-existing worktree change is
unchanged and recorded separately; no personal path was moved to another tracked file. Rollback is the
one-file documentation revert. Stop if the exporter identifies any second tracked finding or preserving
the document's meaning requires a broader edit.

## P1 - Baseline controls, catalog integrity, and architecture ratchets

Status: `complete`
Depends on: `P0A`
Runtime default after phase: legacy path; new execution mode `off`

Objective: introduce inert rollout controls and make the current registry/catalog truthful and
deterministic without adding capability behavior.

Entry gate:

- [x] `P1-ENTRY-01` Record current commit, dirty files, Python/dependency versions, and common command
  results. Do not touch the unrelated OCR document changes already present at plan creation.
- [x] `P1-ENTRY-02` On the authoritative Ubuntu runtime, record the full-suite and six-case Main
  benchmark baseline before any runtime promotion. The immutable latency cohort
  `legacy_latency_v1` is exactly `conversation_stable_fact`, `turn_general_conversation`,
  `turn_authorized_list_add`, `turn_missing_switch_clarifies`,
  `turn_unauthorized_action_fails_closed`, and `repair_authorized_list_add`. Record commit, manifest
  SHA-256, current `jarvis-poc-app:local` image ID, model name/digest, context size, each case latency,
  overall p50/p95, and output path. Because these are the complete pre-change manifest, its overall p95
  is the `legacy_latency_v1` baseline p95. This is read-only and does not authorize deployment.

Exact P1 baseline command:

```bash
baseline_checkout="$(pwd)"
baseline_admission_container="$(docker compose --env-file .env -f deploy/docker/compose.yaml ps -q accelerator-admission)"
test -n "$baseline_admission_container"
test "$(docker inspect --format '{{.State.Health.Status}}' "$baseline_admission_container")" = "healthy"
git rev-parse HEAD
sha256sum benchmarks/models/main_acceptance_cases.json
docker image inspect jarvis-poc-app:local --format '{{.Id}}'
docker compose --env-file .env -f deploy/docker/compose.yaml run --rm --no-deps --no-build -T \
  --volume "$baseline_checkout/benchmarks:/opt/jarvis/benchmarks:ro" \
  jarvis python scripts/benchmark_main_models.py \
  --base-url http://accelerator-admission:8040 \
  --model gpt-oss:20b \
  --cases /opt/jarvis/benchmarks/models/main_acceptance_cases.json \
  --min-pass-rate 1.0 --max-p95-seconds 60 \
  --output /opt/jarvis/data/main-legacy-baseline.json
```

Allowed production/configuration files:

- `app/config.py`
- `app/db/migrations.py`
- `app/db/sqlite_store.py`
- `app/skills/registry_service.py`
- `app/skills/execution_dispatcher.py`
- `app/prompts/skills/SKILL_TEMPLATE.md`
- `app/prompts/skills/calendar_skill.md`
- `app/prompts/skills/lights_skill.md`
- `app/prompts/skills/memory_skill.md`
- `.env.example`
- `deploy/docker/compose.yaml`
- `benchmarks/models/main_acceptance_cases.json`
- `scripts/manage_database.py`
- `scripts/manage_document_backup.py`

Mechanical execution addendum discovered by the P1 full-suite gate: the following checked-in generated
artifacts are also allowed only as deterministic outputs of the three allowed skill-contract changes.
The install verifier requires their embedded sources and content hashes to match; no hand-authored
behavior belongs in these files.

- `app/prompts/skills/critical_skills.md`
- `app/prompts/skills/critical_skills.md.meta.json`
- `app/prompts/micro_jarvis_skills.md`
- `app/prompts/micro_jarvis_skills.md.meta.json`

Allowed tests:

- ADD `tests/unit/test_config.py`
- ADD `tests/unit/test_core_schema_migrations.py`
- `tests/unit/test_skill_registry_service.py`
- `tests/unit/test_skill_execution_dispatcher.py`
- `tests/unit/test_skill_context_contracts.py`
- `tests/unit/test_architecture_boundaries.py`
- `tests/unit/test_main_model_acceptance.py`
- `tests/unit/test_document_backup.py`
- `tests/unit/test_document_schema_migrations.py`

Forbidden in this phase: database-column additions; tool-loop code; domain service/handler changes;
provider calls; approval behavior; Micro removal; protected configuration changes.

Tasks:

- [x] `P1-01` Add and validate the eleven locked settings from **Deterministic limits** in `app/config.py`.
  Mirror safe defaults in `.env.example` and `deploy/docker/compose.yaml`. Reject unknown mode values,
  malformed/duplicate domain or operation IDs, non-positive bounds, and selected-skill counts above the
  configured server maximum. Tests must prove defaults, exact operation matching, and invalid-value
  failure.
- [x] `P1-02` Add a registry integrity report that identifies duplicate active operation ownership,
  active declarations with unimportable handlers, unknown legacy intent names, interactive declarations
  without safe contracts, and stale execution references. Diagnostics may not expose implementation
  references to model projections.
- [x] `P1-03` Make `skill.productivity.calendar` the canonical Calendar declaration for new and upgraded
  databases. Deactivate, but do not delete, `skill.calendar.core`; preserve rollback metadata. Prove
  resolution is independent of historical usage/success counters.
- [x] `P1-04` Mark the unimplemented Memory Markdown declaration inactive/non-interactive. Preserve
  `MemoryService` interaction-history behavior and do not create structured memory operations.
- [x] `P1-05` Reconcile Lights metadata: do not advertise an operation as executable until implemented;
  identify the canonical future names `home.list_devices` and `home.get_device_state`; reject stale
  receipt-only operation names from projection.
- [x] `P1-06` Extend `SKILL_TEMPLATE.md` with required typed-tool metadata placeholders and disposition
  guidance. Existing Markdown remains legacy-compatible; P2 owns compilation into descriptors.
- [x] `P1-07` Expand the Main acceptance manifest with stable IDs for arbitrary Email intervals, exact
  dates, iterative observation, clarification, unauthorized-tool refusal, approval pause/resume, repeat
  detection, and partial completion. Add `benchmark_group=legacy_latency_v1` to exactly the six immutable
  P1 baseline IDs without changing their text, context, expectations, or `max_seconds`; label new cases
  `benchmark_group=reasoning_tools_v1`. Mark tool cases non-executing until their owning phase.
- [x] `P1-08` Add architecture tests that fail on duplicate active tool/intent ownership, projected
  `execution_ref`/`storage_ref`, an active unimportable handler, new imports of `app.runtime` outside the
  approved composition path, or new cross-domain handler/store imports.
- [x] `P1-09` Add and deploy a schema-reader compatibility bridge before any version-8 migration. When a
  database `user_version` is newer than the binary, the bridge starts only if an existing
  `schema_reader_compatibility` table has one row for every intervening version, each row says
  `change_class=additive`, and `minimum_reader_version` is not greater than the binary's reader version.
  Missing table/row, destructive classification, or a higher minimum fails closed exactly as today.
  P1 creates no table and changes no `user_version`; tests use fixture databases to prove both allow and
  deny paths. Add `scripts/manage_database.py reader-check --source PATH`: it opens the supplied Core
  database with SQLite URI `mode=ro&immutable=1`, runs the binary's exact startup reader-compatibility
  decision without migration or journal creation, emits only version/result/reason code, and exits nonzero
  on incompatibility. Retain the deployed P1 image as the database-compatible rollback image for later
  phases; every later rollback drill invokes this command from that exact retained image before any writer.
- [x] `P1-10` Add `scripts/manage_document_backup.py reader-check --source PATH` for the current Documents
  version 14 boundary. It opens only the supplied Documents SQLite file read-only/immutable, invokes the
  binary's exact startup reader decision without migration, journal creation, archive access, or provider
  calls, emits only version/result/reason code, and exits nonzero for a newer version. This changes no
  Documents schema or runtime behavior. Retain it in the P1 image so pre-P8C rollbacks can prove version-14
  compatibility; P8C-05 later extends that same command through its additive reader bridge.

Execution split for the two deployment-bearing tasks:

- [x] `P1-09-source` Core reader bridge, immutable CLI, fixture allow/deny paths, and exact subprocess CLI
  coverage are implemented and verified. No compatibility table or schema version was created.
- [x] `P1-09-deploy` Promote the verified source, retain the exact P1 rollback-reader image, and run its
  reader check before any version-8 writer.
- [x] `P1-10-source` Documents version-14 immutable CLI and exact subprocess CLI coverage are implemented
  and verified with no Documents runtime/schema change.
- [x] `P1-10-retain` Prove the Documents CLI from the retained promoted P1 image.

Verification:

```bash
python -m pytest -q tests/unit/test_config.py tests/unit/test_core_schema_migrations.py tests/unit/test_skill_registry_service.py tests/unit/test_skill_execution_dispatcher.py tests/unit/test_skill_context_contracts.py tests/unit/test_architecture_boundaries.py tests/unit/test_main_model_acceptance.py tests/unit/test_document_backup.py tests/unit/test_document_schema_migrations.py
python -m compileall -q app scripts tests
python -m pytest -q
phase_export="$(mktemp -d)"
python scripts/export_clean_repo.py "$phase_export"
git diff --check
```

Exit gate:

- [x] Every active catalog entry has one canonical owner and a truthful executable disposition.
- [x] All new controls are default-off/empty and produce no runtime behavior change.
- [x] Calendar legacy data remains recoverable; Memory and Lights no longer overstate capability.
- [x] All source-verification evidence and the exact changed-file list are recorded.

Implementation record - 2026-08-30:

- Entry baseline: local commit `900f3ba340631675edda2dc1afbcae545d7dd420`; the authoritative clean
  deployment source matched that commit through a 535-file manifest with SHA-256
  `6385880435d0d4ceeb333910b0c2e6c6fb5bffe919627c1d38084b1ee9da6974`. The pre-change
  benchmark manifest SHA-256 was `eaa78e5a5df59ba74827a536df5fd01f2203c778f773fedb9c793343a0e17994`.
  The production image ID was
  `sha256:0cc178ebc3da0428b399612292f68518543bbe6a26d09530d88fcb50312af509`.
- Baseline verification: 658 tests passed with six existing deprecation warnings. The immutable six-case
  `legacy_latency_v1` run passed 6/6 with zero failed token loops, p50 `2.4775s`, p95 `5.5186s`, and
  per-case latencies `3.9628`, `1.5532`, `2.4661`, `5.5186`, `2.4775`, and `3.0452` seconds in manifest
  order. Model `gpt-oss:20b` used digest
  `17052f91a42e97930aa6e28a6c6c06a983e6a58dbb00434885a0cf5313e376f7` and context `32768`.
- `P1-01` through `P1-08`: implemented inert strict settings; content-free integrity diagnostics;
  deterministic counter-independent ownership; canonical Calendar plus inactive preserved legacy row;
  inactive/non-interactive stale Memory; truthful Lights/future naming; typed-tool template placeholders;
  exactly six `legacy_latency_v1` labels plus eight disabled `reasoning_tools_v1` cases; and architecture
  ratchets. Calendar's existing Micro read path was preserved because P1 forbids Micro removal.
- `P1-09-source` and `P1-10-source`: implemented the Core additive-reader decision and both immutable
  reader-check CLIs. Tests prove complete-chain acceptance and missing-table, missing-row, destructive,
  too-new-reader, and newer-Documents refusal; exact CLI subprocesses emit only version/result/reason and
  create no WAL/SHM files. Core remains version 7 and Documents remains version 14.
- A full-suite gate discovered that the allowed skill-contract edits necessarily stale the checked-in
  generated artifacts. The recorded mechanical addendum allowed deterministic regeneration only. Final
  artifact hashes are `9d2d8b0ad93a0e9a9ac35319568df14092a46c5e42284b7694bf9c4a808376d8`
  for Critical and `e83389d3b1fa7c65a106183e8a63406123401d531ca29e94e39026fed8e85adf`
  for Micro. The final acceptance manifest SHA-256 is
  `5f69fb0d5e9fb2f5dbfd04d6380e4d464822ae0f02a5ed8e8415e371947af2a2`.
- Final authoritative isolated gates: focused P1 suite 98 passed; final full suite 689 passed with the same
  six deprecation warnings; `compileall` passed; quiet Compose parsing passed; pinned Ruff `0.14.10`
  passed on every P1-owned Python file; `git diff --check` passed; and a fresh sanitized export copied 538
  files and passed its public-tree checker. The full-tree Ruff run still exposes pre-existing findings in
  files outside the P1 allowlist; P1 introduced none after its targeted gate.
- Phase-attributable files: `.env.example`; `app/config.py`; `app/db/migrations.py`;
  `app/db/sqlite_store.py`; `app/skills/registry_service.py`; `app/prompts/skills/SKILL_TEMPLATE.md`;
  `app/prompts/skills/calendar_skill.md`; `app/prompts/skills/lights_skill.md`;
  `app/prompts/skills/memory_skill.md`; `app/prompts/skills/critical_skills.md` and metadata;
  `app/prompts/micro_jarvis_skills.md` and metadata; `benchmarks/models/main_acceptance_cases.json`;
  `deploy/docker/compose.yaml`; `scripts/manage_database.py`; `scripts/manage_document_backup.py`;
  `tests/unit/test_config.py`; `tests/unit/test_core_schema_migrations.py`;
  `tests/unit/test_skill_registry_service.py`; `tests/unit/test_architecture_boundaries.py`;
  `tests/unit/test_main_model_acceptance.py`; and `tests/unit/test_document_backup.py`. This plan changed
  only for status, the mechanical-output addendum, checklists, and evidence. P0A's Qwen document is not a
  P1 change. The pre-existing OCR relocation and `.test-tmp-phase23/` remain untouched.
- Data ownership impact: none. P1 adds no durable table, column, queue, provider call, approval, ticket,
  or second authority. Rollback is the exact P1 file set with mode `off`, empty domain/operation lists,
  and legacy Micro routing true. Protected configuration and database schemas were unchanged.
- Production promotion evidence: a 538-file sanitized export passed the public-tree checker and produced
  candidate image `sha256:e3cfd0879cbfb9caaec68aafdcbf23c0e1ec526432f6a6dfb4a0c2b0d01fa0eb`.
  The live `jarvis` service was recreated alone with `--no-build --pull never --no-deps`, became healthy,
  and reported mode `off`, empty domain and operation allowlists, and legacy Micro enabled. The exact prior
  image remains tagged as `jarvis-poc-app:rollback-pre-p1-20260830T221503Z`; the promoted image remains
  tagged as `jarvis-poc-app:retained-p1-reader-20260830T221104Z`.
- Rollback-reader evidence: the retained tag read the verified live Core version-7 backup, accepted the
  controlled additive Core version-8 fixture, and read the Documents version-14 fixture. A version-8 Core
  fixture missing its compatibility row failed closed. No writer used version 8 during P1.
- Post-promotion canary: the unchanged six-case `legacy_latency_v1` cohort passed 6/6 with zero failed
  token loops, p50 `3.0457s`, and p95 `5.529s` against the same `gpt-oss:20b` digest as the baseline.

Stop and escalate if an existing registry row cannot be deactivated additively, a protected deployment
value is needed, or resolving drift requires domain behavior. Rollback: set mode `off`, keep legacy Micro
enabled, and revert only P1 catalog/configuration changes; no domain data migration is allowed.

## P2 - Typed tools, discovery, projection, and dispatch seam

Status: `complete`
Depends on: `P1`
Runtime default after phase: legacy path; new execution mode `off`

Objective: compile domain-owned semantic tool declarations into a safe request-scoped catalog and a
server-authorized dispatch seam without changing Main or executing a new path.

Allowed production files:

- ADD `app/skills/tool_contracts.py`
- `app/db/core_schema.py`
- `app/db/migrations.py`
- `app/db/sqlite_store.py`
- `app/skills/registry_service.py`
- `app/skills/authorized_executor.py`
- `app/skills/execution_dispatcher.py`
- `app/skills/context_contracts.py`
- `scripts/compile_skill_artifacts.py`
- `app/prompts/skills/SKILL_TEMPLATE.md`

Allowed tests:

- ADD `tests/unit/test_tool_contracts.py`
- `tests/unit/test_core_schema_migrations.py`
- `tests/unit/test_skill_registry_service.py`
- `tests/unit/test_authorized_skill_executor.py`
- `tests/unit/test_skill_execution_dispatcher.py`
- `tests/unit/test_skill_context_contracts.py`
- `tests/unit/test_architecture_boundaries.py`

Forbidden in this phase: Main/router behavior; domain handlers/services; approvals/tickets; provider
access; active/shadow execution; legacy column removal.

Tasks:

- [x] `P2-01` Implement immutable `ToolDescriptor` and `ToolCallEnvelope` types with the exact fields in
  this plan. Schema objects must be closed, bounded, JSON-serializable, size-limited, and reject unknown
  fields/effect/policy/cardinality/transfer-scope/runtime-dependency values. Every descriptor must
  explicitly compile its effect cardinality, duplicate-free runtime dependencies, and same-/cross-domain
  transferable JSON-pointer patterns; omission is invalid.
- [x] `P2-02` Add nullable `main_tools_json` and `main_tools_contract_version` to the baseline `skills`
  definition in `app/db/core_schema.py`. Ordered core migration 008 in `app/db/migrations.py` adds those
  columns, creates
  `schema_reader_compatibility(schema_version PRIMARY KEY, minimum_reader_version, change_class, description)`,
  and records version 8 as additive with minimum
  reader 7. Tests cover fresh creation, upgrade from populated version 7, idempotent reopen, P1-reader
  startup against version 8, and pre-P1-reader refusal. Before migration 008, adapt the migration runner so
  each new-version migration from 008 onward executes its individual SQL statements, compatibility-row
  insert, and `PRAGMA user_version` update inside one explicit transaction. These migrations may not call
  `executescript` or any helper that implicitly commits. Inject failure after each step and prove the prior
  version/schema remains intact and the next open retries cleanly; versions 1-7 retain characterized
  behavior.
- [x] `P2-03` Compile Markdown `main_tools` into validated server descriptors. Compilation fails closed
  per invalid tool and emits a content-free diagnostic; it does not activate an invalid declaration.
- [x] `P2-04` Add `discovery_cards(...)`: return only authorized skill ID, title, purpose, safe tags, and
  availability for the current request. It must not return tool schemas or implementation/storage refs.
- [x] `P2-05` Add `effective_tools(selected_skill_ids, request_context)`: enforce the three-skill cap,
  intersect current registry/user/agent/channel/domain/configuration policy plus both exact rollout
  allowlists, and return model-safe projections only for selected skills. Tests prove that enabling a
  domain without its operation, or an operation without its domain, exposes nothing and that removing a
  write operation leaves same-domain reads available.
- [x] `P2-06` Add unique `tool_id` resolution to `AuthorizedSkillExecutor`. Construct trusted envelope
  fields server-side, validate arguments, re-resolve the current descriptor/version, and reauthorize
  immediately before dispatch.
- [x] `P2-07` Add `SkillExecutionDispatcher.execute_tool(envelope)`. Route through the owning domain
  handler contract. Retain `execute(intent, ...)` as a named legacy shim; no new tool may require a new
  `Intent` enum member.
- [x] `P2-08` Add a model-projection redaction test covering execution refs, storage refs, provider
  names/settings, credentials, principal internals, policy internals, and protected IDs.
- [x] `P2-09` Add a synthetic second-consumer contract fixture proving the shared seam works without a
  domain-to-domain import or a registry behavior branch.
- [x] `P2-10` Implement the locked canonical argument hash, `toolop_v1_` parent ID, and
  `toolchild_v1_` child-ID helpers in `app/skills/tool_contracts.py`. Tests must prove stability across
  key ordering, normalized Email target ordering, duplicate transport delivery, serialization/reopen,
  and identical retry; duplicate Email targets are rejected; and changed root, tool, version, ordinal,
  normalized arguments, child index, target, or child arguments conflict.
- [x] `P2-11` Add the typed domain argument-canonicalization hook at the exact pre-identity boundary. The
  P2 default is identity-only; a synthetic domain fixture proves bounded read resolution can replace an
  alias with an opaque stable ref, while stale/ambiguous/unauthorized/duplicate results, mutation, a
  schema-changing result, or a provider object fail before operation-ID creation. No P2 production domain
  behavior changes.

Verification:

```bash
python -m pytest -q tests/unit/test_tool_contracts.py tests/unit/test_core_schema_migrations.py tests/unit/test_skill_registry_service.py tests/unit/test_authorized_skill_executor.py tests/unit/test_skill_execution_dispatcher.py tests/unit/test_skill_context_contracts.py tests/unit/test_architecture_boundaries.py
python -m compileall -q app scripts tests
python -m pytest -q
phase_export="$(mktemp -d)"
python scripts/export_clean_repo.py "$phase_export"
git diff --check
```

Exit gate:

- [x] Fresh and version-7 databases compile the same valid descriptors; the retained P1 reader starts
  against version 8 with new execution disabled, while a pre-P1 reader still fails closed.
- [x] Discovery, selected loading, projection, authorization, and dispatch are independently tested.
- [x] Unknown/duplicate/stale/unauthorized tools and invalid arguments fail before domain execution.
- [x] No feature flag value can cause Main to call the new seam yet.

Implementation record - 2026-08-30:

- Reuse decisions: adapted `SkillRegistryService`, the existing `skills` table, the Core migration
  authority, `AuthorizedSkillExecutor`, `SkillExecutionDispatcher`, and the existing domain-handler
  boundary. Added only the shared immutable contracts module; no second registry, queue, identity,
  approval, memory, ticket, or persistence authority was introduced. Because `registry_service.py` was
  already oversized, schema validation, pointer checking, canonical JSON, immutable types, and identity
  helpers were extracted into `app/skills/tool_contracts.py`; registry additions remain thin catalog and
  persistence adapters.
- `P2-01` through `P2-11`: implemented closed bounded descriptors/envelopes, per-tool Markdown
  compilation with content-free diagnostics, schema-free discovery cards, exact selected-tool projection,
  unique current tool resolution, server-built envelopes, immediate reauthorization, generic domain
  dispatch, stable parent/child IDs, normalized duplicate-free Email target identity, and the bounded
  pre-identity domain canonicalization hook. The legacy intent executor remains the named authoritative
  runtime shim; no Main/router or production domain file changed.
- Migration 008 is additive and transactionally owns both nullable skill columns, the reader-
  compatibility table/row, and `PRAGMA user_version=8`. Failure injection after every migration step
  proves rollback to an intact version-7 schema and clean retry. Fresh and reconstructed version-7
  databases compile identical descriptor JSON.
- Source gates: final focused suite 108 passed; final local full suite 728 passed and 2 skipped with the
  six existing deprecation warnings; the exact final tree passed the authoritative isolated focused/full
  gates with 108 and 730 tests; compileall, pinned Ruff `0.14.10`, fatal Ruff classes,
  `git diff --check`, protected-ID scan, and a 540-file sanitized export passed. The first export correctly
  caught two protected-ID test literals; they were replaced with obvious placeholders before release.
- Production gate: candidate image
  `sha256:55edbb37aa5f3b5c5c0e16e9bc467343c55003cb2ef4ef216cdbb99f2736d9ff`
  was built only from the sanitized export and promoted as
  `jarvis-poc-app:retained-p2-typed-seam-20260830T224737Z`. The exact prior P1 image remains available as
  `jarvis-poc-app:rollback-pre-p2-20260830T224737Z` and by its retained P1 reader tag.
- Live Core migrated from version 7 to 8 and passed `quick_check`; the two new skill columns are present,
  the compatibility row is `minimum_reader_version=7/change_class=additive`, and verified pre/post
  migration backups were created. P2 and retained P1 both read the exact version-8 backup; the pre-bridge
  image refused it. The live service is healthy with mode `off`, both rollout allowlists empty, legacy
  Micro enabled, and zero compiled production tool rows.
- Post-promotion `legacy_latency_v1` passed 6/6 with no failed token loops, p50 `2.5338s`, p95 `5.106s`,
  and the unchanged `gpt-oss:20b` digest. P2 performed no provider call, approval, ticket, domain effect,
  or Main/shadow/active tool execution.
- Phase-attributable files: `app/skills/tool_contracts.py`; `app/db/core_schema.py`;
  `app/db/migrations.py`; `app/db/sqlite_store.py`; `app/skills/registry_service.py`;
  `app/skills/authorized_executor.py`; `app/skills/execution_dispatcher.py`;
  `app/skills/context_contracts.py`; `app/prompts/skills/SKILL_TEMPLATE.md`;
  `tests/unit/test_tool_contracts.py`; `tests/unit/test_core_schema_migrations.py`;
  `tests/unit/test_skill_registry_service.py`; `tests/unit/test_authorized_skill_executor.py`;
  `tests/unit/test_skill_execution_dispatcher.py`; `tests/unit/test_skill_context_contracts.py`; and
  `tests/unit/test_architecture_boundaries.py`. This plan changed only for status/checklist/evidence.
- Data ownership impact: compiled tool JSON remains a nullable projection owned by the existing SQL skill
  row and refreshed only from its domain Markdown; request-scoped cards, projections, and envelopes remain
  ephemeral. Compatibility rows remain owned by Core migrations. Rollback retags the retained P1 image;
  its verified bridge reads Core v8, so the additive columns/table may remain unused. Remaining debt is
  intentional: production domains publish no typed tools until their later proving phases, and P3 is not
  authorized.

Stop and escalate if compatibility requires rewriting existing registry rows, if a second registry is
suggested, or if a model needs an implementation reference. Rollback: leave mode `off`; the additive
columns may remain unused and the legacy executor/dispatcher shim remains authoritative.

## P3 - Bounded Main call/observe loop and shadow evaluation

Status: `implementation_complete_shadow_gate_in_progress`
Depends on: `P2`
Runtime default after phase: legacy response and effects; optional non-executing shadow

Objective: let Main make a generic conversation/clarification/action-candidate commitment, then select
skills and emit typed respond/clarify/call steps under hard limits, while shadow mode records only
content-free comparison telemetry and never dispatches.

Allowed production files:

- `.env.example` - approved P3 repair-budget scope increase
- `deploy/docker/compose.yaml` - approved P3 repair-budget scope increase
- ADD `app/core/tool_loop_types.py`
- ADD `app/core/main_tool_loop.py`
- `app/core/main_backend.py`
- `app/core/main_turn_contract.py`
- `app/core/main_turn_commitment.py`
- `app/core/domain_context.py`
- `app/core/pending_interaction.py`
- `app/core/persistence_policy.py`
- `app/core/request_flow.py` - mode precedence only; no phrase/domain branch
- `app/core/turn_finalizer.py`
- `app/core/router.py` - seam invocation only; no phrase or domain branch
- `app/services/conversation_history_service.py`
- `app/services/durable_write_service.py`
- `app/services/memory_service.py`
- `app/config.py`
- `app/runtime.py` - composition only
- `scripts/benchmark_main_models.py`

Allowed tests and benchmark data:

- ADD `tests/unit/test_main_tool_loop.py`
- `tests/unit/test_main_backend.py`
- `tests/unit/test_main_turn_contract.py`
- `tests/unit/test_pending_interaction_manager.py`
- `tests/unit/test_router_pending_interaction.py`
- `tests/unit/test_router_context_contracts.py`
- `tests/unit/test_router_discord_micro_gate.py`
- `tests/unit/test_discord_adapter.py`
- `tests/unit/test_agent_loop.py`
- `tests/unit/test_authorized_skill_executor.py`
- `tests/unit/test_persistence_memory.py`
- `tests/unit/test_conversation_history_service.py`
- `tests/unit/test_durable_write_service.py`
- `tests/unit/test_main_model_acceptance.py`
- `benchmarks/models/main_acceptance_cases.json`

Forbidden in this phase: domain/provider changes; actual tool dispatch in shadow; approvals/tickets;
cross-domain effects; Micro removal; raw model reasoning persistence.

Tasks:

- [x] `P3-01` Implement strict `SkillSelection`, `ModelStep`, `ToolObservation`, and
  `RequestTemporalContext` types. Reject multiple/no modes, unknown fields, non-JSON values, oversized
  fields, model-supplied authority, and unrecognized skills/tools. Wire one injected UTC clock and the
  locked domain-timezone resolution through `app/core/domain_context.py` and `app/runtime.py`.
- [x] `P3-02` Implement `MainToolLoop`: discover, obtain and validate one `SkillSelection`, load only its
  one to three skills, request one step, validate it, and either respond, persist a typed clarification,
  or dispatch through the authorized executor. Enforce every locked selection/step/failure/repeat/
  observation/time rule, provenance-claim shape, and exact domain-plus-operation activation predicate.
- [x] `P3-03` Define terminal behavior: invalid model output retries only within the existing bounded
  policy; denial is not retried; an identical effectful call reuses the first accepted call without a
  second dispatch; identical reads stop after the configured second execution; deadline/failure limits
  return truthful partial-completion prose and committed receipt IDs.
- [x] `P3-04` Resume typed clarification from the existing pending-interaction store. Bind request/user/
  agent/channel, reserve but do not advance the call ordinal, store the separate partial hash, merge only
  expected fields, compute the final hash/operation ID only after complete validation, expire normally,
  and recompute catalog/authorization. Apply the locked pending behavior for `standard`, `redacted`, and
  `no_store`; a follow-up cannot change tool ID or smuggle authority.
- [x] `P3-05` Implement the locked `MainActionCommitment` compatibility split. In `off`, retain the
  current intent-bound validator. In `shadow|active`, remove `MAIN_ACTION_INTENTS` from the prompt and
  validator, require `intent`/`entities` to be absent, treat `execute_action` as a generic candidate, and
  enter discovery with the original trusted request. Outer `clarify_action` is limited to pre-skill
  missing-referent/ambiguous-goal questions with `no_store`; selected-tool missing fields use
  `ModelStep.clarify`. The router chooses this seam only from mode plus the closed commitment, never an
  intent membership test, keyword, or legacy hint. In `active`, every no-match, disabled/revoked tool,
  invalid step, denial, or unavailable operation ends on this path with a typed safe stop and cannot call
  Micro or the legacy router/executor. Request flow evaluates mode before
  `micro_command_explicit`: `active` preserves the trusted prefix envelope for audit but forces Main
  ownership for both prefixed and unprefixed semantic turns. Only `off|shadow` may consult the temporary
  legacy Micro flag, and only `off` may restore legacy semantic action execution.
- [x] `P3-06` Implement `shadow` so the new path evaluates discovery/selection/steps against fixtures or
  read-only catalog data but skips executor dispatch and writes no pending call, approval, ticket, job,
  provider request, domain mutation, or user response. Persist only allowed IDs/status/counts/timings.
- [x] `P3-07` Add scripted model tests for response, clarification/resume, one and multiple observations,
  invalid schema, unauthorized tool, catalog change between steps, denial, duplicate call, timeout,
  bounded failure, untrusted prompt injection in observations, truthful partial completion, and stable
  call ordinal across schema retry/clarification while distinct accepted calls advance it exactly once.
  Tests must prove two identical effectful model calls cause one dispatch/operation ID/receipt, while two
  allowed identical reads have distinct ordinals and a third does not dispatch. A synthetic authorized
  tool absent from `Intent` and `MAIN_ACTION_INTENTS` must pass generic commitment, selection, and dispatch;
  a legacy-intent hint with no authorized selected tool must not dispatch; informational prose must still
  terminate as conversation. In active-mode tests, disabled, revoked, no-match, malformed, and
  not-yet-migrated legacy actions must produce zero legacy-executor calls; switching the same fixtures to
  `off` must restore only the characterized legacy result. Prefixed and unprefixed versions of every
  safety case must prove zero Micro classifier/model/handler calls in `active`; `off|shadow` must preserve
  the characterized legacy prefix boundary while its temporary flag remains true.
- [x] `P3-08` Extend the existing persistence policy rather than adding another authority. Map descriptor
  `standard`, `redacted`, and `no_store` to the exact semantics in this plan and pass the most restrictive
  effective policy through finalization before any generic write. Inspect Core session/recent-turn/event/
  memory rows, conversation-history files, pending rows, durable memory payloads, and traces after normal,
  failure, clarification, restart, and multi-tool turns. Email and Documents `no_store` observations,
  request/argument text, and derived answers must be absent from every generic sink; asynchronous writers
  receive a prefiltered surrogate or no job. Reserve the documented purpose-bound approval-payload
  exception for P9: outside an unexpired transfer-bound `ActionApprovalProposal`, `no_store` argument or
  derived content must still be absent everywhere.
- [x] `P3-09` Extend `scripts/benchmark_main_models.py` and the manifest to score skill selection and
  typed tool-step sequences while keeping legacy cases valid. Its exit code must enforce 100% mandatory
  and safety cases, at least 95% overall, zero failed loops, every case's `max_seconds`, and the configured
  overall p95 ceiling. Validate `benchmark_group`, report p50/p95/count/pass rate per group, and add exact
  final-certification inputs `--latency-comparison-group`, `--baseline-p95-seconds`, and
  `--max-p95-regression-ratio`. The ratio applies only to the named unchanged group; the overall suite and
  new multi-step cases use absolute ceilings. Fail for a missing/empty comparison group or any threshold;
  unit tests prove each nonzero exit.

Verification:

```bash
python -m pytest -q tests/unit/test_main_tool_loop.py tests/unit/test_main_backend.py tests/unit/test_main_turn_contract.py tests/unit/test_pending_interaction_manager.py tests/unit/test_router_pending_interaction.py tests/unit/test_router_context_contracts.py tests/unit/test_router_discord_micro_gate.py tests/unit/test_discord_adapter.py tests/unit/test_agent_loop.py tests/unit/test_authorized_skill_executor.py tests/unit/test_persistence_memory.py tests/unit/test_conversation_history_service.py tests/unit/test_durable_write_service.py tests/unit/test_main_model_acceptance.py tests/unit/test_architecture_boundaries.py
python -m compileall -q app scripts tests
python -m pytest -q
phase_export="$(mktemp -d)"
python scripts/export_clean_repo.py "$phase_export"
git diff --check
```

Authoritative Ubuntu shadow gate: run three consecutive acceptance passes and at least 24 hours of
representative Discord traffic with mode `shadow` and empty domain and operation allowlists; scripted
fixtures supply the candidate catalog. Required: zero side effects, zero approval/ticket/job creation,
zero private-content telemetry, and no legacy-response change.

Exit gate:

- [x] All terminal paths and every deterministic cap have direct model-free tests.
- [x] Shadow inability to execute is enforced below Main, not merely prompted.
- [x] The old path remains the sole source of user responses and effects.
- [ ] Authoritative Ubuntu shadow evidence meets the gate before P4 starts.

Rollback: set mode `off`; the loop becomes unreachable. Stop if shadow causes any mutation, a router
phrase branch appears necessary, or an observation cannot be safely projected.

## P4 - Email read/query proving slice

Status: `implementation_verified_activation_superseded_by_P5F`
Depends on: `P3`
Runtime default after phase: Email reads may be independently active; every non-enabled semantic action
is unavailable while mode remains `active`; `off` is the global legacy rollback

Objective: prove that one semantic query surface handles new date ranges and filter combinations without
new central intents or phrasing branches, while preserving Email's exact private-channel authorization.

Allowed production/contract files:

- `app/core/router.py` - explicit typed-handler injection only; no domain or phrase branch
- `app/core/main_backend.py` - generic temporal, observation-completion, and provenance prompt rules only
- `app/runtime.py` - composition-only binding for the existing generic dispatcher slot
- ADD `app/skills/domains/email_agent/query.py`
- `app/skills/domains/email_agent/context.py`
- `app/skills/domains/email_agent/handler.py`
- `app/skills/domains/email_agent/storage.py`
- `app/skills/domains/email_agent/service.py` - delegate query work; do not add another responsibility
- `app/skills/domains/email_agent/summarization.py`
- `app/prompts/skills/email_agent_skill.md`

Allowed tests and acceptance data:

- `scripts/benchmark_main_models.py` - fixture-only expected argument-subset scoring
- ADD `tests/unit/test_email_agent_query.py`
- `tests/unit/test_email_agent_storage.py`
- `tests/unit/test_email_agent_service.py`
- `tests/unit/test_email_agent_config.py`
- `tests/unit/test_main_backend.py`
- `tests/unit/test_authorized_skill_executor.py`
- `tests/unit/test_main_tool_loop.py`
- `tests/unit/test_main_model_acceptance.py`
- `benchmarks/models/main_acceptance_cases.json`

Forbidden in this phase: Gmail fallback/sync trigger; provider mutations; mark/spam/review-state writes;
send/reply/forward/delete; approval/ticket changes; another domain.

Tasks:

- [x] `P4-01` Add immutable `EmailQuery` with inclusive `start`, exclusive `end`, IANA timezone, bounded
  sender/recipient/source/category/visibility filters, text terms, attachment predicate, ordering, and
  limit. Closed schema only; require concrete normalized ISO instants at dispatch.
- [x] `P4-02` Extend storage with the two-sided interval and typed filters using parameterized queries.
  Explicit invalid source/category/visibility values fail closed; they may never become an omitted filter.
- [x] `P4-03` Move query compilation out of the oversized service into
  `app/skills/domains/email_agent/query.py`. Main interprets natural
  language into schema values; domain code validates dates and policy but contains no phrase-specific
  rules for `last 3 days`, weekdays, or named dates.
- [x] `P4-04` Define exact temporal semantics: an exact local date is
  `[local midnight, next local midnight)`; rolling N days uses the request timezone and current injected clock; DST transitions,
  month/year boundaries, future ranges, reversed ranges, and nonexistent/ambiguous local times have
  explicit validation tests.
- [x] `P4-05` Publish and implement `email.query_messages`, `email.get_message`, `email.get_thread`,
  `email.summarize`, and `email.status`. `email.discuss` becomes outer Main reasoning over bounded
  observations, not a separate fixed formatter.
- [x] `P4-06` Return bounded stable E-references, result count/truncation, normalized interval, projection
  source, and freshness timestamp. Raw bodies are returned only within existing focus limits and remain
  excluded from generic memory/history.
- [x] `P4-07` Preserve exact user/channel/agent authorization on every call and resume. Outside the email
  channel, discovery may describe support but selected tools are unavailable and no mailbox metadata is
  returned.
- [x] `P4-08` Add held-out cases for `last 3 days`, a single exact date, a named weekday/date, before/
  after/between, sender plus attachment, multiple filters, no matches, stale projection disclosure,
  invalid source, unauthorized channel, excessive limit, and observation injection text.

Verification:

```bash
python -m pytest -q tests/unit/test_email_agent_query.py tests/unit/test_email_agent_storage.py tests/unit/test_email_agent_service.py tests/unit/test_email_agent_config.py tests/unit/test_authorized_skill_executor.py tests/unit/test_main_tool_loop.py tests/unit/test_main_model_acceptance.py
python -m compileall -q app scripts tests
python -m pytest -q
phase_export="$(mktemp -d)"
python scripts/export_clean_repo.py "$phase_export"
git diff --check
```

Activation disposition: do not run P4's former five-descriptor activation independently. P5F E1 absorbs
the gate, retains the verified interval/query foundation, advances query/status contracts, adds live
mailbox/label discovery and pagination, and activates the exact six P5F read/status IDs. The stored
`email.summarize` compatibility descriptor remains inactive because Main now summarizes bounded query
observations in its normal response step.

P5F must still prove that arbitrary query combinations require schema values rather than a new branch,
disabled Email writes have no legacy fallthrough, scheduler-owned sync remains bounded, and private
authorization is unchanged. Its exact operation kill switches and rollback replace the obsolete P4
five-ID instructions. No P4-only activation window or data migration is authorized.

Implementation evidence (2026-08-31):

- Reuse map: the proving slice reuses the SQL-backed `SkillRegistryService`, generic Main selection/loop,
  `AuthorizedSkillExecutor`, `SkillExecutionDispatcher`, existing Email private-channel policy, existing
  `EmailAgentSQLiteStorage`, and existing Email reference-set/freshness machinery. The only new component is
  the cohesive typed query/execution boundary in `app/skills/domains/email_agent/query.py`; no second
  registry, authorization service, queue, provider client, or Email store was introduced.
- Data ownership: the existing Email SQLite projection remains authoritative. `EmailQuery`, tool
  descriptors, normalized request arguments, bounded observations, and temporal context are ephemeral.
  Existing scoped Email reference sets remain the sole owner of E-reference resolution. No schema or data
  migration occurred, and no provider or review-state mutation is reachable from the five read tools.
- Composition correction: the generic dispatcher already accepted injected typed handlers, but production
  composition did not populate that slot. `app/core/router.py` now accepts the injection and
  `app/runtime.py` binds only the existing Email service by skill ID; neither contains a phrase or Email
  behavior branch.
- Verification: local full suite `783 passed, 2 skipped`; compileall, owned-file Ruff, diff check, generated
  skill compilation, and the 545-file clean export passed. The exact sanitized P4 export passed the
  public-tree check, 97 focused tests, and the authoritative
  Ubuntu full suite (`785 passed`). A preliminary authoritative Main run passed all 20/20 enabled cases,
  including 10/10 P4 and all safety-critical cases, with zero failed token loops; `legacy_latency_v1` p95
  was `5.4943s` against the recorded `5.5186s` baseline.
- Activation remains deliberately pending. The live P3 image is still `shadow` with empty domain and
  operation allowlists, legacy Micro routing enabled, and Main repair `num_predict=1024`. P3 observation
  began `2026-08-31T00:46:54Z` and cannot close before `2026-09-01T00:46:54Z`; therefore no P4 allowlist,
  canary, provider call, or active-mode change has occurred. The staged tree can be discarded without a
  runtime rollback; the retained P3 image remains the live rollback point.
- The read-only interim audit at `2026-08-31T01:50:11Z` found zero `main.action.*` events since the P3
  promotion. The two new durable jobs were both content-free model compute-budget notices; there were zero
  work tickets, ticket entries/expectations/reviews, operation receipts, scheduled jobs, skill runs, or
  committed effects. Representative Discord traffic has therefore not started the qualifying sample set;
  elapsed wall time alone cannot close P3.

## P5 - Remaining read surfaces and the Lists end-to-end proving slice

Each P5 subphase is independently gated. P5A is intentionally broader than a read-only slice because a
read-only Lists canary cannot prove the product requirement that Main infer a goal and compose safe tools.
The user approved this scope revision on 2026-08-31 after rejecting item-count, punctuation, and phrase
branches as substitutes for model reasoning. P5B-E remain read-only. P5F is the separately approved
accelerated Email exception: it completes the Email read surface and adds only reversible, allowlisted,
additive managed-label operations against the central Jarvis Gmail mailbox. P9 still requires every tool
used by cross-domain composition to have passed its own gate.

### P5A - Lists end-to-end reasoning: read, create, and batch add

Status: `complete`
Depends on: implemented P3 bounded-loop safety and the verified P4 typed-tool framework. The remaining P3
elapsed-time observation and P4 Email activation gates do not block this narrowly scoped Lists slice.
Runtime default after subphase: Lists reads plus reversible local `create_collection` and `add_items` may
be independently active; destructive and item-removal/update operations remain unavailable.

Allowed production files: `app/runtime.py`, `app/config.py`, `app/api/accelerator_admission_app.py`,
`app/core/main_backend.py`, `app/core/main_tool_loop.py`, `app/core/request_flow.py`,
`app/skills/tool_contracts.py`, `app/skills/domains/lists/context.py`,
`app/skills/domains/lists/service.py`, `app/skills/domains/lists/storage.py`,
ADD `app/skills/domains/lists/tools.py`, `app/prompts/skills/lists_skill.md`,
`app/db/core_schema.py`, `app/db/migrations.py`, `app/db/sqlite_store.py`, `.env.example`, and
`deploy/docker/compose.yaml`.

Allowed tests and acceptance data: `tests/unit/test_accelerator_admission.py`, `tests/unit/test_config.py`,
`tests/unit/test_core_schema_migrations.py`,
`tests/unit/test_lists_service_resolution.py`, `tests/unit/test_lists_service_sqlite_persistence.py`,
`tests/unit/test_main_tool_loop.py`, ADD `tests/unit/test_lists_typed_tools.py`,
`tests/unit/test_skill_registry_service.py`, `tests/unit/test_main_model_acceptance.py`, and
`benchmarks/models/main_acceptance_cases.json`; acceptance evaluator changes remain in
`scripts/benchmark_main_models.py`.

Reuse and ownership decisions:

- `reuse`: the generic discovery, selection, bounded call/observe loop, dispatcher, operation-envelope,
  schema validation, and exact operation/domain kill switches. No Lists workflow intent or planner is
  added.
- `adapt`: expose a focused Lists typed-tool handler beside the oversized legacy service. It uses the
  existing Lists storage authority and does not route through legacy phrase splitting, hidden auto-create,
  or shared-owner fallback.
- `adapt`: extend the existing Core schema/migration authority with Lists-owned operation identities;
  do not create a second database, queue, task system, or receipt authority.
- `owner`: `lists` and `list_items` remain the canonical user data. `list_operations` owns only mutation
  identity, argument hash, bounded redacted result, and completion state. Runtime observations are
  ephemeral projections of those authorities.
- `policy`: the authenticated requesting user owns newly created lists. Existing shared household lists
  are returned as explicit candidates and are usable only after the model supplies the selected stable
  collection reference; domain code never silently changes owner scope.

Tasks:

- [x] `P5A-01` Publish and implement `lists.list_collections` and `lists.get_collection` with closed
  schemas, bounded items, stable opaque references, explicit owner scope, and no mutation.
- [x] `P5A-02` Publish and implement safe `lists.create_collection(name)` and atomic
  `lists.add_items(collection_ref, items[])`. One item and many items use the same array schema. The
  model interprets wording and punctuation; no handler, router, regex, conjunction split, or numbered-list
  parser may derive item boundaries on the typed path.
- [x] `P5A-03` Prove ordinary adaptive multi-step behavior: Main interprets a request such as creating a
  named list with requested entries and may either create first or attempt the bounded add first. If the
  add reports that the target is missing, the observation returns to Main; Main may create the explicitly
  requested list and retry the add within the same bounded turn. A no-op or failed write attempt must not
  be cached as a successful effect. After creation, Main may pass the canonical collection reference to
  one `add_items` call, then truthfully report both committed operations. The domain has no combined
  create-and-add operation and no special case for an item count. Pull forward the minimum
  same-skill provenance primitive: an `observation_derived` claim is accepted only when it names a current
  root-local observation, its source pointer is allowlisted `scope=same_domain` by that source descriptor,
  the destination descriptor has the same skill owner, and the claimed source value exactly equals the
  destination argument. Cross-domain transfer remains unavailable until P9.
- [x] `P5A-04` Make missing, ambiguous, stale, or unauthorized collection selectors return a bounded
  `needs_input`/denial result. Name resolution may produce explicit stable candidates but may not execute a
  fuzzy or shared-owner match. A `needs_input` observation is replanning input when another authorized tool
  can satisfy the prerequisite entirely from explicit request data; it becomes a user clarification only
  when the LLM cannot resolve it without guessing. Reauthorize the envelope's user/channel/agent scope on
  every call.
- [x] `P5A-05` Add ordered core migration 009 and baseline schema support for Lists-owned
  `list_operations(operation_id PRIMARY KEY, owner_user_id, action, target_ref, arguments_hash, status,
  result_json, created_at, completed_at)`, its lookup index, and additive reader-compatibility metadata.
  `create_collection` and the complete `add_items` batch commit with their completed operation record in
  one SQLite transaction. Same ID/hash replays the bounded result; same ID/different hash conflicts.
- [x] `P5A-06` Characterize existing behavior and prove arbitrary list names, empty lists, one to 50 item
  arrays, duplicate item text, preserved order, ambiguity, unauthorized refs, validation bounds, restart
  replay, and crash rollback. Add held-out model cases that vary punctuation, conjunctions, formatting,
  item count, and list name without adding code branches for those variants. The gate accepts multiple
  valid next steps and separately proves recovery after a missing-target observation; it must not encode a
  single conservative ordering as the only correct plan.

Activation gate: pass targeted and full suites, clean export, three consecutive authoritative held-out
model runs, and an isolated copied-database end-to-end canary. For these P5A runs, every active P5A case
remains mandatory, every safety-critical case across previously
certified phases must pass, failed token loops remain zero, and total pass rate remains at least 95%.
Non-safety P3/P4 cases remain regression samples under that statistical threshold rather than requiring
deterministic perfection on every stochastic run. Then enable only domain `lists` and exact operations
`lists.list_collections,lists.get_collection,lists.create_collection,lists.add_items`. Because `active` is
currently a global Main-path mode, prove conversation behavior and fail-closed handling for
all non-enabled domains before promotion. A short live canary may create only an unmistakably disposable
canary list; no real user list may be changed. Observe duplicate/unauthorized effects at zero and verify
the canary receipt/state directly. The prior 24-hour read-only wait is replaced for this slice by these
three model passes plus exact end-to-end state verification; safety failures still block immediately.

Gate result (2026-08-31): complete. Three consecutive authoritative model runs passed the gate at
`26/27`, `27/27`, and `27/27` (96.3%, 100%, and 100%; p95 `5.9022s`, `6.3448s`, and `4.9596s`) with all
mandatory and safety cases passing and zero failed token loops. Three isolated copied-production
database canaries, an operator-identity canary, and a complete copied-database `/ask` route passed. The
live disposable canary then completed the exact `lists.create_collection -> lists.add_items -> respond`
sequence in six steps, committed two effects, and verified both requested items in Core schema v9. Local
full verification passed `809` tests with `2` skipped; authoritative Ubuntu verification passed `811`.
The clean public-tree export and checker passed. Live containers are healthy on image
`sha256:a57688bd42e09ed88da55a058bfc4fcd44d2d0e13b331186c35917bc7503821e`, with only the four P5A Lists
operations enabled. The explicit development-headroom profile is active so ordinary recoverable misses
can re-enter reasoning without changing any authority, approval, effect, idempotency, or operation
allowlist boundary.

Rollback: first remove `lists.create_collection,lists.add_items`, preserving reads and committed data;
then remove the two read IDs if needed. Remove domain `lists` only when no Lists operation remains active.
Set global mode `off` only for a full legacy rollback. Never auto-delete a committed user list during
rollback.

### P5F - Accelerated Email reasoning and central-inbox management

Status: `ready_for_execution`
Depends on: completed `P5A` and the verified P4 typed-tool framework. P3/P4's broader observation debt is
retained but does not block this independently gated slice.
Runtime default after subphase: authorized central Jarvis Gmail reads and reversible additive managed-label
operations may be independently active; original routed source accounts remain read-only views.

Canonical execution detail: [`email-reasoning-inbox-management-plan.md`](email-reasoning-inbox-management-plan.md).
That focused plan is authoritative for P5F's file allowlists, schemas, tests, worker lifecycle, canaries,
stop conditions, reuse map, and data-ownership map. Where the earlier P4/P8D Email prose describes an
exclusive category-to-Gmail-label operation, P5F supersedes it: classifier categories remain local and
never enqueue provider work; user-managed labels are additive and independently selected by Main.

Locked scope:

- The central Jarvis Gmail account is the only provider mutation target. Routed inbox names are authorized
  query projections; Jarvis does not authenticate to or mutate original source accounts.
- E1 activates exactly six reads/status operations: `email.list_mailboxes`, `email.list_labels`,
  `email.query_messages`, `email.get_message`, `email.get_thread`, and `email.status`.
- E2 may then add `email.get_operation`, `email.apply_labels`, and `email.remove_labels`. The two writes
  accept the same one-to-many schemas and touch only enabled protected-catalog labels. Initial policy
  includes `Done`, `To-do`, `Bills`, and `AYSO`; policy additions require configuration, not code.
- P5F reserves Core migration 010 and a Compose-owned `email-operations-worker`. The worker can claim only
  the new additive operation table; the legacy exclusive-category backlog is never eligible.
- Sending, drafting, replying, forwarding, deleting, trashing, archiving, spam, read/unread mutation,
  source-account mutation, and cross-domain promotion remain unavailable.

Execution tasks:

- [ ] `P5F-E0` Characterize and contain the legacy automatic category-label queue without provider calls.
- [ ] `P5F-E1` Add authorized mailbox/label discovery, query contract v2, keyset pagination, truthful
  coverage, and bounded operator-run historical backfill; certify reads before writes.
- [ ] `P5F-E2` Add migration 010, additive label parent/child operations, read-back verification, and the
  tracked worker with leases, fencing, bounded retry, dead letters, recovery, and health.
- [ ] `P5F-E3` Pass focused/full/model/copied-database gates, promote reads first, run a reversible live
  canary on one disposable central-mailbox message and dedicated canary label, then observe for 24 hours.

Rollback removes exact Email operation IDs, disables the new worker, quiesces or expires claims, and
reconciles new rows to truthful terminal state. It never starts the legacy worker, deletes projection
history, down-migrates Core, auto-reverses verified Gmail state, or touches a source account.

### P5B - Calendar reads

Status: `not_started`
Depends on: `P4` framework gate; P5A need not be complete
Runtime default after subphase: Calendar reads may be independently active

Allowed files: `app/skills/domains/calendar/context.py`, `app/skills/domains/calendar/handler.py`,
`app/skills/domains/calendar/service.py`, `app/skills/domains/calendar/storage.py`,
`app/services/google/calendar_live.py` - query adapter only, `app/prompts/skills/calendar_skill.md`,
`tests/unit/test_calendar_service.py`, `tests/unit/test_google_calendar_live_paths.py`,
`tests/unit/test_main_tool_loop.py`, and `benchmarks/models/main_acceptance_cases.json`.

Tasks:

- [ ] `P5B-01` Publish `calendar.query_events` with required inclusive start/exclusive end, timezone,
  calendar/person scope, optional text, ordering, and bounded limit.
- [ ] `P5B-02` Replace daily/weekly collapse in the new path with typed range validation and provider
  query delegation. An unknown explicit person/calendar fails closed rather than using the default.
- [ ] `P5B-03` Return source, synchronization truth, truncation, and bounded event fields. The in-memory
  fallback must identify itself and never claim Google synchronization.
- [ ] `P5B-04` Test exact date, rolling and arbitrary ranges, DST, unknown/ambiguous person, multiple
  calendars, unauthorized scope, provider failure, and bounds. Do not change create/update/delete.

Gate and rollback: targeted Calendar read tests, common exit commands, and a 24-hour read canary with
domain `calendar` and only `calendar.query_events` newly present in the operation allowlist. Remove that
operation ID to roll back; remove the domain only if no Calendar operation remains active.

### P5C - Home/Lights reads and naming repair

Status: `not_started`
Depends on: `P4` framework gate; P5A/P5B need not be complete
Runtime default after subphase: Home reads may be independently active

Allowed files: `app/skills/domains/lights/context.py`, `app/skills/domains/lights/handler.py`,
`app/skills/domains/lights/service.py`, `app/skills/domains/lights/storage.py`,
`app/skills/domains/lights/receipts.py`, `app/prompts/skills/lights_skill.md`,
`tests/unit/test_home_service_persistence.py`,
`tests/unit/test_skill_context_contracts.py`, and `tests/unit/test_main_tool_loop.py`.

Tasks:

- [ ] `P5C-01` Publish and implement `home.list_devices` and `home.get_device_state` over the existing
  simulated SQLite authority; use canonical opaque references and bounded alias hints.
- [ ] `P5C-02` Replace stale `home.get_switch_state`/`home.list_switches` declarations and receipt names
  additively while retaining legacy compatibility aliases outside the model projection.
- [ ] `P5C-03` Return ambiguity instead of silently selecting a fuzzy candidate. Always state simulated
  state; do not imply physical-device truth.
- [ ] `P5C-04` Test exact/alias/ambiguous/missing devices, user scope, bounded enumeration, stale aliases,
  and zero action-log writes.

Gate and rollback: targeted Home tests, common exit commands, and a 24-hour read canary with domain
`home` and only `home.list_devices,home.get_device_state` newly present in the operation allowlist. Remove
those IDs to roll back; remove the domain only if no Home operation remains active.

### P5D - Documents restricted reads

Status: `not_started`
Depends on: `P4` framework gate; other P5 subphases need not be complete
Runtime default after subphase: scoped Documents reads may be independently active

Allowed files: `app/skills/domains/documents/handler.py`,
`app/skills/domains/documents/context.py`, `app/skills/domains/documents/query_service.py`,
`app/skills/domains/documents/schemas.py`, `app/skills/domains/documents/types.py`,
`app/skills/domains/documents/permissions.py`, `app/prompts/skills/documents_skill.md`,
`tests/unit/test_document_query_service.py`, `tests/unit/test_document_phase10_restricted_gate.py`,
`tests/unit/test_document_request_guard.py`, and `tests/unit/test_main_tool_loop.py`.

Tasks:

- [ ] `P5D-01` Publish truthful contracts for `documents.upload_capability`, `documents.search`,
  `documents.status`, `documents.inspect`, `documents.source_link`, and `documents.list_reviews`.
- [ ] `P5D-02` Preserve the existing operator versus scoped-Discord permission matrix. Discovery must not
  reveal document existence outside scope; every Discord call must recheck current attachment binding.
- [ ] `P5D-03` Preserve restricted-read/no-generic-memory behavior and all content/item/field/snippet
  bounds. Return provider artifacts only as existing safe links or opaque IDs, never model-visible
  provider objects or source bytes.
- [ ] `P5D-04` Test cross-user/channel denial, stale attachment, unsupported operation, injection content,
  truncation, and exact equivalence with legacy authorized reads.

Gate and rollback: all named restricted-boundary tests plus common exit commands and a 24-hour scoped
read canary with domain `documents` and only `documents.upload_capability`, `documents.search`,
`documents.status`, `documents.inspect`, `documents.source_link`, and `documents.list_reviews` in the
operation allowlist. Any content
leak is a global rollback trigger. Remove those six IDs to roll back; remove the domain only if no
Documents operation remains active.

### P5E - Bounded web research tool

Status: `not_started`
Depends on: `P4` framework gate; other P5 subphases need not be complete
Runtime default after subphase: Research may be independently active under its existing policy

Allowed production files:

- ADD `app/skills/domains/research/__init__.py`
- ADD `app/skills/domains/research/handler.py`
- `app/research/types.py`
- `app/research/protocols.py`
- `app/research/service.py`
- `app/research/decision_backend.py`
- ADD `app/prompts/skills/research_skill.md`
- `app/config.py`

Allowed tests: `tests/unit/test_web_research.py`, `tests/unit/test_main_tool_loop.py`, and
`tests/unit/test_architecture_boundaries.py`.

Tasks:

- [ ] `P5E-01` Publish `research.search_web(query, limit)` as a read tool only when existing research
  configuration and request policy authorize it. Reuse `WebResearchService` and SearXNG; add no provider.
- [ ] `P5E-02` Enforce the existing query/result limits, safe search, child restriction, timeouts, URL
  sanitation, and cache policy per call. Permit repeated refinement only within global loop/repeat caps.
- [ ] `P5E-03` Mark every snippet/title/URL untrusted, restrict answer links to returned safe URLs, and
  ensure research observations cannot grant another tool or authority.
- [ ] `P5E-04` Test disabled/unhealthy service, injection snippets, unsafe URL, child policy, repeated
  refinement, total observation cap, and zero side effects.

Gate and rollback: tests plus common exit commands, then a 24-hour read canary with domain `research`,
only `research.search_web` in the operation allowlist, and existing research policy still default-off.
Disable research or remove that exact operation ID to roll back; remove the domain only if no Research
operation remains active.

P5 completion evidence must explicitly reaffirm that Conversation is the outer response, interaction
Memory is context-only, Private Notes is adapter/scheduler-owned, and Calendar Inbox is scheduler-only.
None may appear in the interactive effective-tool catalog.

## P6 - Durable pre-action approval and protected Discord delivery

Status: `not_started`
Depends on: `P5F`; P5F reserves migration 010 and P6 follows with migration 011
Runtime default after phase: approval infrastructure available; no new write tool active

Objective: pause an exact validated call, obtain a durable decision from the configured private Discord
destination, then reauthorize and execute it at most once after restart.

Allowed production/configuration files:

- `app/reviews/types.py`
- `app/reviews/service.py`
- `app/reviews/repository.py`
- `app/db/review_schema.py`
- `app/db/migrations.py`
- `app/jobs/types.py`
- `app/jobs/repository.py`
- `app/core/main_tool_loop.py`
- `app/core/pending_interaction.py`
- ADD `app/core/approved_action_execution.py`
- `app/services/identity_service.py`
- `app/services/discord/bot.py`
- ADD `app/services/discord/approval_delivery.py`
- ADD `app/workers/action_approval_worker.py`
- ADD `scripts/canary_action_approval.py`
- ADD `scripts/check_worker_readiness.py`
- `app/api/routes/reviews.py`
- `app/container.py`
- `app/runtime.py` - composition only
- `app/config.py`
- `.env.example`
- `deploy/docker/compose.yaml`
- `deploy/ubuntu/discord_permissions.example.yaml` - symbolic keys/fake placeholders only

Allowed tests:

- `tests/unit/test_human_review_service.py`
- `tests/unit/test_pending_interaction_manager.py`
- `tests/unit/test_discord_adapter.py`
- `tests/unit/test_discord_identity_profiles.py`
- `tests/unit/test_durable_job_repository.py`
- `tests/unit/test_main_tool_loop.py`
- ADD `tests/unit/test_action_approval_delivery.py`
- ADD `tests/unit/test_action_approval_worker.py`
- ADD `tests/unit/test_action_approval_canary.py`
- ADD `tests/unit/test_worker_readiness.py`
- ADD `tests/integration/test_action_approval_restart.py`
- `tests/unit/test_core_schema_migrations.py`
- `tests/unit/test_architecture_boundaries.py`

Forbidden in this phase: domain write migration; literal production IDs; email content in proposals;
provider execution before approval; treating ActionTicket as approval; rerouting the reported message
before its sender is known.

Tasks:

- [ ] `P6-01` Add `action_proposals` to `app/db/review_schema.py` and ordered core migration 011 to
  `app/db/migrations.py`; record version 11 as additive with minimum reader 7 in the compatibility table.
  Fresh creation, upgrade from version 10, populated-row preservation, idempotent reopen, and P1-reader
  startup must pass in `tests/unit/test_core_schema_migrations.py`. Store every required
  `ActionApprovalProposal` field, including nullable closed `transfer_manifest_json`,
  `transfer_binding_hash`, closed `batch_manifest_json`, `batch_manifest_hash`, and the purpose-bound
  destination-argument payload lifecycle reserved for P9,
  but no secrets, source value/observation payload, raw email body, raw document text, or hidden reasoning.
  Terminal transitions atomically clear the optional destination-argument payload while retaining the
  content-free manifest and hashes.
- [ ] `P6-02` Enforce `authorization_binding`, exact canonical argument hash, descriptor/resource version,
  requester/channel, destination purpose, immutable approver, expiry, and allowed lifecycle transitions.
  When the optional transfer manifest is present, validate its exact closed shape, bounds, canonical
  hash, request/identity binding, source and destination versions, and absence of source values. Reusing
  an idempotency key with different content, batch manifest, or transfer manifest must fail as a
  conflict. An independent batch requires an exact child manifest before notification; single/atomic
  calls reject one.
- [ ] `P6-03` Convert a policy result requiring approval into `waiting_for_approval`, persist only an
  opaque pending pointer, and atomically create the proposal, linked pending review, and exactly one
  `review.notification.discord.v1` job using the locked dedupe key before acknowledging the pause. The
  Human Review and durable-job repositories must share the same Core transaction for this method; a
  post-commit best-effort enqueue is forbidden. No domain dispatch, ticket, or effect occurs yet.
- [ ] `P6-04` Add durable job types `review.notification.discord.v1` and
  `review.action_execution.v1` to the existing job ledger. Add default-off settings
  `ACTION_APPROVAL_WORKER_ENABLED=false`, batch size `10`, lease `60` seconds, and poll `2` seconds, plus
  a Compose `approvals` profile running `python -m app.workers.action_approval_worker`. The worker owns
  claims, heartbeats, bounded retry/backoff, dead letters, lease recovery, cancellation, and process
  lifecycle only and records `worker_type=action_approval` at startup and every bounded poll. It delegates
  notification jobs to `ApprovalDelivery` and execution jobs to
  `ApprovedActionExecutionService`; the Discord adapter never executes a domain action. Enforce the
  locked notification and execution-job dedupe keys and same-key/same-payload reuse versus mismatched-
  payload conflict rules at the repository boundary.
- [ ] `P6-05` Add symbolic protected destination purposes `human_reviews` and `operator_notices`.
  Resolve IDs only from protected runtime configuration at send/decision time. Tracked examples use fake
  values that pass the public-tree checker. Existing and new Human Review notification kinds use
  `human_reviews`; non-review proactive status notices use `operator_notices`.
- [ ] `P6-06` Deliver a bounded approval card containing review ID, safe action summary, requester, risk,
  expiry, and exact deterministic commands `approve <review-id>` and
  `reject <review-id> [reason]`. Do not include raw email/document bodies or credentials.
- [ ] `P6-07` Accept decisions only in the resolved `human_reviews` destination from the configured
  immutable approver identity. Bind Discord guild/channel/message/actor IDs, make duplicate identical
  decisions idempotent, and reject conflicting/stale/wrong-actor/wrong-channel decisions.
- [ ] `P6-08` Approved execution reloads the proposal, descriptor, resource version, identity, current
  authorization, and both domain and exact-operation allowlists immediately before dispatch. For a
  transfer-bound proposal it also reloads every named source descriptor/version, verifies the source
  tool is still currently authorized for the same identity/channel, the recorded pointer still matches
  the recorded transfer scope, sensitivity/persistence policy is not weakened, and the destination
  argument/manifest hashes still match. For an independent batch, revalidate every child target/hash and
  preserve its original index/ID; changed or newly added children are denied. Any
  unavailable/revoked/changed source denies execution. Use the
  original stable operation ID; execute at most once; atomically clear the purpose-bound argument payload
  on every terminal transition; publish a bounded completion/failure reply and receipt reference.
- [ ] `P6-09` Add a restart matrix covering restart before notification, after notification, before
  decision persistence, after approval/before execution, during an expired lease, after effect commit/
  before job completion, and duplicate Discord delivery. Each path must end in one durable state and at
  most one effect. Include a synthetic transfer manifest: restart must preserve content-free source/
  destination bindings, reauthorize both sides, execute once only on an exact match, and clear the
  destination payload on every terminal outcome. Include an independently batched synthetic action and
  prove the approved parent resumes only unresolved children with one receipt per committed child. Crash
  immediately before and after the proposal/review/notification transaction and prove there is never a
  stranded pending proposal without its one deduplicated notification job. Repeat the same crash and
  conflict checks around approval/review/execution-job commit and prove exactly one
  `review-action-execution:v1:<proposal_id>:<operation_id>` job exists.
- [ ] `P6-10` On the authoritative Ubuntu runtime, attribute the currently misplaced proactive message using content-free
  event/job/message IDs and sender implementation. Check Private Notes delivery, model-compute notices,
  document completion, and other notifiers. Only after positive attribution, update that sender's
  protected destination to the appropriate named purpose. If attribution is uncertain, stop without
  changing either destination.
- [ ] `P6-11` Add two operator-only, content-free verification CLIs. `canary_action_approval.py` may create
  and inspect only a `canary.no_effect` proposal; that descriptor exists only inside the canary/integration
  harness, is never compiled into the registry or Main catalog, and its executor can only record an
  in-memory/test receipt. The production approval worker must set it `denied` at execution-time
  reauthorization, proving no hidden canary authority. `check_worker_readiness.py` opens Core SQLite
  read-only from required `--database PATH`, accepts repeatable
  `--require-worker TYPE=MAX_AGE_SECONDS`, bounded `--wait-seconds`,
  `--write-dead-letter-baseline PATH`, `--dead-letter-baseline PATH`,
  `--write-email-dead-letter-baseline PATH`, or `--email-dead-letter-baseline PATH`. The Email baseline is
  the separate count of terminal/dead-letter rows in `email_label_operations` and
  `email_mailbox_operations`; an increase in either fails. It emits only types/status/counts/ages and exits
  nonzero for missing/stale/degraded heartbeats, malformed requirements, or a baseline increase. Tests use
  fake clocks and fixture databases.

  The same CLI also accepts repeatable `--prospective-operation TOOL_ID` and
  `--write-runtime-requirements PATH`. It validates the prospective IDs against the compiled catalog and
  writes this exact sorted, content-free JSON object:

  ```json
  {
    "schema_version": 1,
    "required_services": ["fixed-compose-service-name"],
    "required_workers": [{"max_age_seconds": 120, "type": "fixed-worker-type"}],
    "email_consumer_required": false,
    "reasons": [{"code": "fixed_reason_code", "subject": "tool-or-job-type"}]
  }
  ```

  Derivation uses protected enabled flags, currently active exact operation IDs, prospective exact
  operation IDs, descriptor `runtime_dependencies`, nonterminal proposals, unfinished durable jobs, and
  unfinished owning-domain operation rows. It never uses a container's previous running state. The closed
  mappings are: `action_approval -> action-approval-worker/action_approval/120`;
  `ticket_review -> ticket-review/ticket_review/60` only while Action Tickets are enabled or unfinished
  ticket work exists; `document_processing -> document-worker/documents/120`;
  `email_operations -> email-operations-worker/email_operations/120` plus
  `email_consumer_required=true`; enabled/unfinished Plane work adds
  `plane-sync/plane_sync/60`; enabled Documents access adds `document-gateway`; and enabled Discord
  attachment ingress adds `discord-attachment-ingress`. An unknown operation, dependency, service, worker,
  table state, or reason code fails closed. Reason `code` is exactly one of
  `protected_flag_enabled`, `active_operation_dependency`, `prospective_operation_dependency`,
  `nonterminal_action_proposal`, `unfinished_durable_job`, or `unfinished_domain_operation`; `subject` is a
  catalog-validated symbolic config, tool, job, or operation type matching
  `[a-z][a-z0-9_.-]{0,127}`. Reasons are unique and sorted by code/subject. The output may contain only
  these fixed service names, worker
  types/ages, symbolic tool/job types, booleans, and counts--never config values, IDs, paths, content, or
  credentials.

  Passing `--runtime-requirements PATH` validates that exact schema/version, rejects any unknown field or
  value, and automatically enforces every listed heartbeat with its listed maximum age. It does not start
  services. The release shell must separately prove each listed Compose service is running/healthy and,
  when `email_consumer_required=true`, prove the tracked Compose-owned Email worker, its fresh heartbeat,
  and its readiness-only invocation before a canary.
- [ ] `P6-12` On the authoritative Ubuntu runtime, set protected
  `ACTION_APPROVAL_WORKER_ENABLED=true`, retain mode `active` with only already-certified read IDs, and
  start the worker with
  `docker compose --env-file .env -f deploy/docker/compose.yaml --profile approvals up -d --no-build action-approval-worker`.
  Require a fresh `action_approval` heartbeat within 120 seconds and write the content-free P6
  dead-letter baseline before running the live no-effect canary. Keep this worker enabled/running as a
  P8 entry condition; a missing/stale heartbeat or dead-letter increase blocks every write activation.

Verification:

```bash
python -m pytest -q tests/unit/test_human_review_service.py tests/unit/test_pending_interaction_manager.py tests/unit/test_discord_adapter.py tests/unit/test_discord_identity_profiles.py tests/unit/test_durable_job_repository.py tests/unit/test_main_tool_loop.py tests/unit/test_action_approval_delivery.py tests/unit/test_action_approval_worker.py tests/unit/test_action_approval_canary.py tests/unit/test_worker_readiness.py tests/integration/test_action_approval_restart.py tests/unit/test_core_schema_migrations.py tests/unit/test_architecture_boundaries.py
python -m compileall -q app scripts tests
python -m pytest -q
phase_export="$(mktemp -d)"
python scripts/export_clean_repo.py "$phase_export"
git diff --check
```

After the phase image/configuration is promoted on the authoritative Ubuntu runtime, use this exact
worker gate before the live no-effect canary:

```bash
p6_dead_letter_baseline="/opt/jarvis/data/action-approval-p6-dead-letters.json"
docker compose --env-file .env -f deploy/docker/compose.yaml --profile approvals up -d --no-deps --no-build action-approval-worker
docker compose --env-file .env -f deploy/docker/compose.yaml run --rm --no-deps --no-build -T \
  jarvis python scripts/check_worker_readiness.py \
  --database /opt/jarvis/data/jarvis_v2.db \
  --require-worker action_approval=120 \
  --wait-seconds 180 \
  --write-dead-letter-baseline "$p6_dead_letter_baseline"
```

The authoritative Ubuntu P6 canary is approval-lifecycle-only. Use `canary.no_effect` to prove correct
private delivery, correct actor binding, denial in every other channel, approve/reject/expiry persistence,
duplicate decision idempotency, restart recovery, and production execution-time refusal because that
operation is absent from both effective allowlists. The injected synthetic executor is exercised only by
the integration test and must record one no-effect test receipt after approval and none after rejection/
expiry. P6 performs no live domain effect. P8A owns the first real reversible approval canary.

Exit gate: the restart matrix passes; production rejects the inert canary at reauthorization with zero
effect; formal approval is distinct from tickets; protected destination and approver values are verified
but absent from tracked files; the misplaced message source and final route are recorded without private
IDs; `action_approval` readiness and the dead-letter baseline pass after the canary. Rollback: remove all
write operation IDs, set mode `off`, set protected `ACTION_APPROVAL_WORKER_ENABLED=false`, and run
`docker compose --env-file .env -f deploy/docker/compose.yaml --profile approvals stop action-approval-worker`.
Leave proposals visible. Expire pending proposals with an operator-rollback reason; after proving no
claim/effect, atomically cancel each approved proposal and its unclaimed execution job. Reconcile every
`executing` proposal to truthful effect/no-effect state before a terminal transition. Never execute work
merely to drain it during rollback.

## P7 - Action-ticket, receipt, replay, and recovery hardening

Status: `not_started`
Depends on: `P6`
Runtime default after phase: no additional tools active

Objective: make every effect in a multi-call turn independently idempotent, truthfully receipted, and
recoverable before broad write migration.

Allowed production files:

- `app/tickets/types.py`
- `app/tickets/repository.py`
- `app/tickets/service.py`
- `app/tickets/review_service.py`
- `app/tickets/remediation_service.py`
- `app/tickets/verifier_registry.py`
- `app/tickets/verifiers/lists.py`
- `app/tickets/verifiers/home.py`
- `app/tickets/verifiers/calendar.py`
- ADD `app/tickets/async_receipts.py`
- `app/core/action_execution.py`
- `app/core/turn_finalizer.py`
- `app/jobs/types.py`
- `app/jobs/repository.py`
- `app/services/event_log.py`
- `app/workers/ticket_review_worker.py`

Allowed tests:

- `tests/unit/test_action_ticket_repository.py`
- `tests/unit/test_action_ticket_service.py`
- `tests/unit/test_calendar_ticket_verifier.py`
- `tests/unit/test_durable_job_repository.py`
- `tests/integration/test_action_ticket_review_flow.py`
- `tests/integration/test_action_ticket_operator_api.py`
- ADD `tests/integration/test_multi_effect_recovery.py`
- ADD `tests/unit/test_async_effect_receipts.py`

Core schema and migration changes are forbidden in P7. Use existing ticket entries, receipts,
expectations, and durable-job payloads.

Tasks:

- [ ] `P7-01` Characterize ticket creation, receipt capture, verifier scheduling, watchdog, replay, and
  remediation before changing them.
- [ ] `P7-02` Give every accepted complete tool call its locked stable operation ID and every committed
  effect a distinct receipt. For ticket-eligible tools only, before dispatch persist a content-minimized
  execution manifest (parent operation ID, every independent-batch child ID/index/target hash/argument
  hash, expected count, tool/version hashes, and an opaque owning-domain recovery-manifest hash when one
  exists) in existing
  `ticket_entries.structured_payload_json` under dedupe key
  `tool-execution-manifest:v1:<parent_operation_id>`. The ticket entry is the immutable parent manifest
  authority: same key requires byte-equivalent canonical manifest/binding/expected count and returns the
  existing entry; changed content is a terminal conflict rather than `append_entry()`'s legacy silent
  reuse. Owning-domain ledgers remain authoritative for child state. Persist receipt references atomically
  with the existing ticket/job transaction boundary or report queued/partial, never hard success.
  Restricted Documents tools remain ticket-exempt and cannot activate until P8C proves their named
  domain-owned reservation.
- [ ] `P7-02A` Add a transaction-aware `EffectManifestReservation` application protocol for an owning
  domain whose operation ledger shares Core SQLite. It accepts a redacted immutable ticket projection and
  a domain callback that writes its private recovery record using the caller's existing connection/
  transaction; it opens one `BEGIN IMMEDIATE`, exact-compares both dedupe records, and commits both or
  neither. It does not expose private domain values to Ticket code or let a domain import Ticket storage.
  P7 proves the seam with a synthetic domain; P8D is its first production consumer. A database without a
  shared Core transaction must use its separately named durable reconciliation boundary instead.
- [ ] `P7-03` Make watchdog expected-count aware. Missing receipts, expired claims, partially finalized
  turns, partially committed independent batches, and effect-committed/job-incomplete states must
  reconcile after restart without duplicating a committed child. Atomic batches must have either their
  one parent receipt or no committed effect. Keep the original parent manifest immutable; child progress
  is append-only ticket transitions reduced from owning-domain rows and receipts, never an in-place rewrite
  that disguises a changed manifest.
- [ ] `P7-04` Make replay reconstruct only from durable receipts/events and clearly label unverifiable
  local Calendar and simulated Home truth. It must not infer success from model prose.
- [ ] `P7-05` Require remediation to create a child ticket/operation before execution, use the same
  authorization/idempotency/approval path, and reconcile a crash between effect and completion.
- [ ] `P7-06` Apply the already-enforced P3 sensitivity/persistence policy to ticket-eligible execution
  context and safe events. Raw Email, Documents, Private Notes, credentials, and hidden reasoning are
  forbidden; this task does not weaken or replace P3 generic-history enforcement.
- [ ] `P7-07` Add a narrow `AsyncChildOutcomeSink`/reconciliation protocol in
  `app/tickets/async_receipts.py`. A domain worker reports a terminal child by immutable parent
  operation ID, parent-manifest hash, child operation ID, effect state, and bounded domain receipt fields;
  the sink resolves and exact-validates the one ticket manifest itself, so the domain row does not need a
  ticket ID. The closed outcomes are `verified | dead_letter | cancelled | denied` under key
  `ticket-child-outcome:v1:<child_operation_id>`. `verified` requires exactly one canonical receipt with
  receipt dedupe key `ticket-effect-receipt:v1:<child_operation_id>`; the other outcomes append one
  content-free terminal child transition and forbid a receipt. Same child/same canonical outcome/receipt
  is idempotent; any conflicting terminal outcome or receipt is a terminal conflict. A queued result is a
  job reference, not a receipt, and the grouping parent never receives an effect receipt. The
  expected-count reducer uses both receipts and no-receipt terminal transitions. Its mapping is closed:
  any missing/nonterminal child is `queued` plus `reconciliation_required`; all verified is `completed`;
  verified mixed with any no-receipt terminal outcome is `partial`; zero verified plus any
  `dead_letter|denied` is `failed`; and all `cancelled` is `failed` with content-free reason
  `all_children_cancelled` because the existing ticket aggregate has no canceled terminal state. A
  cancelled/denied/dead-letter mixture with zero verified is `failed` with reason `no_effect_terminal`.
  The reducer can detect a committed parent manifest with missing domain children, but only marks
  `reconciliation_required`; it never calls a domain or fabricates child state. Update the
  existing watchdog in `app/workers/ticket_review_worker.py` and its focused repository/worker tests for
  expected-count and append-only transitions. Unit and integration tests cover worker restart after
  effect/before receipt, conflicting receipt replay, missing child detection, all-cancelled, all-denied,
  verified/denied, mixed verified/dead-letter, and immutable-manifest enforcement.

Gate: all targeted tests, full restart/replay integration matrix, common exit commands, and no behavior
change for inactive domains. Stop if a side effect has no authoritative verifier/truth statement or if
atomic capture requires a new ledger or schema. Rollback: disable tool execution and deploy the prior
code; P7 creates no schema to reverse, and legacy ticket workers continue against existing rows.

## P8 - Existing writes by domain and risk

Each subphase requires its read phase, P6, and P7. Activate exactly one new write-operation set at a time
by adding only that subphase's exact IDs to `MAIN_TOOL_ENABLED_OPERATIONS`; retain already-certified read
IDs and require the owning domain in `MAIN_TOOL_ENABLED_DOMAINS`. Before
editing, record legacy behavior and provider idempotency/reconciliation. Every subphase runs targeted
tests, the approval restart matrix where applicable, common exit commands, a 24-hour shadow period, and
a 48-hour active canary. Any duplicate, unauthorized, unapproved, or falsely reported effect rolls back
all active writes immediately. Execute P8A through P8E in the listed order; skipping or reordering a
subphase requires a reviewed plan revision. Before every P8 activation, rerun
`check_worker_readiness.py` against the P6 dead-letter baseline, generate requirements for the exact
prospective operation set, start/prove every required consumer, and require every derived heartbeat to be
fresh. A previously stopped consumer is not optional when the requirements manifest names it. Removing a
write ID disables that operation on the active
path and cannot invoke its legacy implementation; mode `off` is the only full legacy rollback.

The prospective operation sets are closed: P8A uses the six IDs in P8A-01; P8B uses
`home.set_device_state`; P8C uses the four IDs in P8C-01; P8D uses the four IDs in P8D-01; and P8E uses
the four IDs in P8E-01. The fixed service/profile map is
`action-approval-worker/approvals`, `ticket-review/tickets`, `plane-sync/plane`,
`document-worker/documents`, `document-gateway/documents`, and
`discord-attachment-ingress/discord-attachments`, plus P5F's
`email-operations-worker/email-operations`. Any other manifest service is a stop condition.

Exact P8 pre-activation worker gate; set only `p8_subphase` to the subphase being activated:

```bash
(
  set -euo pipefail
  p8_subphase="<set P8A, P8B, P8C, P8D, or P8E>"
  case "$p8_subphase" in
    P8A) prospective_operations=(lists.update_item lists.remove_items lists.clear_collection lists.delete_collection) ;;
    P8B) prospective_operations=(home.set_device_state) ;;
    P8C) prospective_operations=(documents.queue_processing documents.propose_metadata documents.review_field documents.confirm_fields) ;;
    P8D) prospective_operations=(email.set_review_state email.correct_local_category email.mark_read_complete email.move_to_spam) ;;
    P8E) prospective_operations=(calendar.create_event calendar.create_event_with_invites calendar.update_event calendar.delete_event) ;;
    *) echo "Unknown P8 subphase" >&2; exit 1 ;;
  esac

  p6_dead_letter_baseline="/opt/jarvis/data/action-approval-p6-dead-letters.json"
  runtime_requirements_container="/opt/jarvis/data/reasoning-led-${p8_subphase}-runtime-requirements.json"
  runtime_requirements_host="./data/reasoning-led-${p8_subphase}-runtime-requirements.json"
  required_services_file="./data/reasoning-led-${p8_subphase}-required-services.txt"
  prospective_args=()
  for operation_id in "${prospective_operations[@]}"; do
    prospective_args+=(--prospective-operation "$operation_id")
  done
  docker compose --env-file .env -f deploy/docker/compose.yaml run --rm --no-deps --no-build -T \
    jarvis python scripts/check_worker_readiness.py \
    --database /opt/jarvis/data/jarvis_v2.db \
    --write-runtime-requirements "$runtime_requirements_container" \
    "${prospective_args[@]}"
  test -f "$runtime_requirements_host"

  python - "$runtime_requirements_host" > "$required_services_file" <<'PY'
import json
import sys

allowed = {
    "action-approval-worker",
    "ticket-review",
    "plane-sync",
    "document-worker",
    "document-gateway",
    "discord-attachment-ingress",
}
with open(sys.argv[1], encoding="utf-8") as stream:
    payload = json.load(stream)
if payload.get("schema_version") != 1:
    raise SystemExit("unexpected runtime requirements schema")
services = payload.get("required_services")
if not isinstance(services, list) or services != sorted(set(services)):
    raise SystemExit("invalid runtime service list")
if not set(services) <= allowed:
    raise SystemExit("unknown runtime service")
for service in services:
    print(service)
PY
  test -f "$required_services_file"

  while IFS= read -r required_service; do
    case "$required_service" in
      action-approval-worker) required_profile=approvals ;;
      ticket-review) required_profile=tickets ;;
      plane-sync) required_profile=plane ;;
      document-worker|document-gateway) required_profile=documents ;;
      discord-attachment-ingress) required_profile=discord-attachments ;;
      *) echo "Unknown required service" >&2; exit 1 ;;
    esac
    docker compose --env-file .env -f deploy/docker/compose.yaml --profile "$required_profile" \
      up -d --no-deps --no-build --wait --wait-timeout 180 "$required_service"
    test -n "$(docker compose --env-file .env -f deploy/docker/compose.yaml --profile "$required_profile" ps -q --status running "$required_service")"
  done < "$required_services_file"

  docker compose --env-file .env -f deploy/docker/compose.yaml run --rm --no-deps --no-build -T \
    jarvis python scripts/check_worker_readiness.py \
    --database /opt/jarvis/data/jarvis_v2.db \
    --runtime-requirements "$runtime_requirements_container" \
    --wait-seconds 180 \
    --dead-letter-baseline "$p6_dead_letter_baseline"
)
```

If the generated manifest says `email_consumer_required=true`, the phase cannot activate until the P8D
readiness/timer/dead-letter supplement below has also passed. P8C additionally requires its Documents
worker supplement. These supplements are requirements, not optional diagnostics.

### P8A - Remaining Lists writes and destructive operations

Status: `not_started`
Depends on: `P5A`, `P6`, and `P7`
Runtime default after subphase: the remaining Lists writes may be independently active

Allowed files: `app/skills/domains/lists/context.py`, `app/skills/domains/lists/handler.py`,
`app/skills/domains/lists/planning.py`, `app/skills/domains/lists/service.py`,
`app/skills/domains/lists/storage.py`, `app/skills/domains/lists/receipts.py`,
`app/tickets/verifiers/lists.py`, `app/prompts/skills/lists_skill.md`,
`app/db/core_schema.py`, `app/db/migrations.py`, `app/db/sqlite_store.py`,
`tests/unit/test_core_schema_migrations.py`,
`tests/unit/test_lists_service_resolution.py`, `tests/unit/test_lists_service_sqlite_persistence.py`,
`tests/unit/test_action_ticket_service.py`, and `tests/unit/test_main_tool_loop.py`.

- [ ] `P8A-01` Retain the P5A-certified `lists.create_collection` and `lists.add_items` operations and
  publish `lists.update_item`, `lists.remove_items`, `lists.clear_collection`, and
  `lists.delete_collection` with closed batch/reference schemas and bounded item counts.
- [ ] `P8A-02` Keep all new-path semantics typed: Main supplies explicit item references and patches;
  missing or ambiguous collections/items clarify without phrase parsing or fuzzy execution.
- [ ] `P8A-03` Apply stable idempotency through the P5A-created `list_operations` authority. Single
  explicit item removal is `local_write`;
  clear/delete always pause for formal approval and bind current collection version.
- [ ] `P8A-04` Preserve per-user/shared ownership only through explicit policy, return one receipt per
  committed mutation, and verify restart/duplicate/partial-batch behavior.
- [ ] `P8A-05` Extend the existing P5A Lists transaction helpers for update/remove/clear/delete without a
  new store or schema path. Each mutation and its completed operation record commit in one SQLite
  transaction; same ID/hash replays the bounded stored result and a different hash conflicts. Test crash
  before/after commit and compatibility with populated P5A rows.

First live approved-effect canary: create a dedicated disposable canary list, add a sentinel item, submit
`lists.clear_collection` or `lists.delete_collection`, and prove reject/expiry cause no mutation while one
correct approval causes exactly one reversible local mutation and one receipt/ticket across duplicate
delivery and restart. Never use a real user list.

Rollback: remove exactly `lists.update_item`, `lists.remove_items`, `lists.clear_collection`, and
`lists.delete_collection` from `MAIN_TOOL_ENABLED_OPERATIONS` while retaining the P5A-certified reads,
create, and add operations; do not reverse already approved committed changes automatically.

### P8B - Home/Lights writes

Status: `not_started`
Depends on: `P8A` and `P5C`
Runtime default after subphase: Home writes may be independently active

Allowed files: `app/skills/domains/lights/context.py`, `app/skills/domains/lights/handler.py`,
`app/skills/domains/lights/service.py`, `app/skills/domains/lights/storage.py`,
`app/skills/domains/lights/receipts.py`, `app/tickets/verifiers/home.py`, `app/prompts/skills/lights_skill.md`,
`app/db/core_schema.py`, `app/db/migrations.py`, `app/db/sqlite_store.py`,
`tests/unit/test_core_schema_migrations.py`,
`tests/unit/test_home_service_persistence.py`, and `tests/unit/test_main_tool_loop.py`.

- [ ] `P8B-01` Publish `home.set_device_state(device_ref, state)` only; group/all-device mutation remains
  absent.
- [ ] `P8B-02` Require an exact canonical device after bounded resolution. Ambiguity clarifies; fuzzy
  score alone never authorizes execution.
- [ ] `P8B-03` Make repeated desired state idempotent without duplicate action-log effects and preserve a
  truthful simulated-state receipt/ticket classification.
- [ ] `P8B-04` Test unauthorized/missing/ambiguous devices, duplicate calls, restart, partial loop, and
  absence of hidden group execution.
- [ ] `P8B-05` Update the baseline core schema and ordered migration 012 to add nullable `operation_id`
  and `arguments_hash` to
  `switch_actions_log`, a unique partial operation-ID index, and an additive compatibility row with
  minimum reader 7. State update and action-log insert commit atomically; same ID/hash replays the stored
  state without a new log row and a different hash conflicts. Test fresh/upgrade/P1-reader compatibility
  and crash before/after commit.

Rollback: remove exactly `home.set_device_state` from `MAIN_TOOL_ENABLED_OPERATIONS` while retaining both
Home read IDs. No claim of physical rollback is permitted.

### P8C - Documents controlled writes and durable work

Status: `not_started`
Depends on: `P8B` and `P5D`
Runtime default after subphase: scoped Documents writes may be independently active

Allowed files: `app/skills/domains/documents/handler.py`,
`app/skills/domains/documents/query_service.py`, `app/skills/domains/documents/schemas.py`,
`app/skills/domains/documents/types.py`, `app/skills/domains/documents/reprocessing.py`,
`app/skills/domains/documents/review_corrections.py`, `app/skills/domains/documents/receipts.py`,
`app/skills/domains/documents/permissions.py`, `app/skills/domains/documents/storage.py`,
`app/skills/domains/documents/ports.py`, `app/db/document_schema.py`, `app/jobs/document_enqueue.py`,
`app/jobs/repository.py`, `scripts/manage_document_backup.py`,
`app/prompts/skills/documents_skill.md`, `tests/unit/test_document_query_service.py`,
`tests/unit/test_document_reprocessing.py`, `tests/unit/test_document_phase7_proposals.py`,
`tests/unit/test_document_phase10_restricted_gate.py`, `tests/unit/test_document_request_guard.py`,
`tests/unit/test_document_storage.py`, `tests/unit/test_document_schema_migrations.py`,
`tests/unit/test_document_backup.py`, `tests/unit/test_durable_job_repository.py`, and
`tests/unit/test_main_tool_loop.py`.

- [ ] `P8C-01` Publish `documents.queue_processing` with allowlisted tiers, `documents.propose_metadata`,
  `documents.review_field`, and `documents.confirm_fields`; keep Discord attachment and operator scopes.
- [ ] `P8C-02` Reuse existing durable processing jobs and request IDs. Queued work returns `queued`, not
  hard success; duplicate/restart/dead-letter behavior stays bounded and visible.
- [ ] `P8C-03` Preserve existing HumanReview and field/resource-version binding. Tool approval may not
  bypass domain review or broaden business-card-only Discord mutation.
- [ ] `P8C-04` Keep restricted content out of generic pending state, tickets, memory, events, and approval
  cards. Test stale evidence, cross-user references, conflicting correction, and duplicate confirmation.
- [ ] `P8C-05` Before changing the encrypted Documents database from version 14, add and deploy a
  compatibility-aware Documents reader that, when encountering a newer version, requires a
  `document_schema_reader_compatibility` row for every intervening version with
  `change_class=additive` and `minimum_reader_version` no greater than its reader version. Missing,
  destructive, or too-new rows fail closed. Prove the bridge changes no version-14 schema or behavior,
  retain that exact image as the Documents rollback reader, and do not run migration 15 until this gate
  passes on the authoritative Ubuntu runtime. Extend P1's existing
  `scripts/manage_document_backup.py reader-check --source PATH` through this bridge without changing its
  read-only/no-migration contract. The retained bridge image must pass this command against a version-15
  fixture and later against the live verified Documents database.
- [ ] `P8C-06` Use `operation_id` as the existing durable-job idempotency key for
  `documents.queue_processing`; its Core payload contains only opaque document ref, tier, and argument
  hash. Set `DOCUMENT_SCHEMA_VERSION=15`; ordered Documents migration 15 adds
  `document_schema_reader_compatibility`, records version 15 as additive with minimum reader 14, and adds
  `document_tool_operations(operation_id PRIMARY KEY, tool_id, arguments_hash, status, target_ref, result_ref, created_at, completed_at)`
  to the encrypted Documents schema for proposal/
  review/confirmation writes. Reserve and complete it in the same Documents SQLite transaction as the
  mutation whenever they share that database; same ID/hash replays the opaque result, different hash
  conflicts, and reserved/incomplete rows reconcile without a duplicate mutation after restart. This is
  the Documents domain's pre-dispatch authority, not a generic ticket or second queue. Test fresh version
  15 creation, populated 14-to-15 upgrade, idempotent reopen, foreign-key/integrity checks, the retained
  compatibility-aware version-14 reader opening version 15, and the pre-bridge version-14 reader failing
  closed. Rollback disables the tools and uses the retained bridge image; never down-migrate or use the
  pre-bridge reader against version 15.
- [ ] `P8C-07` Run the common P8 gate with `p8_subphase=P8C` before shadow and again before active. Assert
  the generated requirements contain `document-worker` and worker `documents` with maximum age 120; start
  the existing `documents` profile worker with the fixed command in that gate and require its fresh,
  non-degraded heartbeat plus no durable dead-letter increase. `documents.queue_processing` remains absent
  from the operation allowlist until this passes. The already-required `document-gateway` health gate for
  interactive Documents access remains independent and must also pass; neither a healthy gateway nor an
  existing queued job substitutes for worker readiness.

Rollback: remove exactly `documents.queue_processing`, `documents.propose_metadata`,
`documents.review_field`, and `documents.confirm_fields` from `MAIN_TOOL_ENABLED_OPERATIONS` while
retaining Documents read IDs; existing queued jobs follow their current operator cancellation/recovery
policy.

### P8D - Existing Email projection/provider writes

Status: `not_started`
Depends on: `P8C`, `P7`, and `P5F`
Runtime default after subphase: local review/category and approved later mailbox-state writes may be
independently active while P5F additive label tools remain intact

Allowed files: `app/skills/domains/email_agent/context.py`,
`app/skills/domains/email_agent/handler.py`, `app/skills/domains/email_agent/service.py`,
`app/skills/domains/email_agent/storage.py`, `app/skills/domains/email_agent/receipts.py`,
`app/skills/domains/email_agent/spam_worker.py`, `app/db/domain_schema.py`,
`app/db/migrations.py`, `app/prompts/skills/email_agent_skill.md`,
`app/workers/email_operations_worker.py`, `scripts/run_email_spam_worker.py` - compatibility only,
`app/container.py`, `app/runtime.py` - composition only,
`tests/unit/test_email_agent_service.py`, `tests/unit/test_email_agent_storage.py`,
ADD `tests/unit/test_email_agent_schema.py`, `tests/unit/test_core_schema_migrations.py`,
`tests/unit/test_email_spam_worker.py`, `tests/unit/test_main_tool_loop.py`, and
`tests/unit/test_action_ticket_service.py`; ADD
`tests/integration/test_email_batch_recovery.py`.

- [ ] `P8D-01` Publish `email.set_review_state`, `email.correct_local_category`,
  `email.mark_read_complete`, and `email.move_to_spam` with the locked separate effect classes. Retain
  P5F's `email.apply_labels` and `email.remove_labels` unchanged; do not publish the superseded exclusive
  `email.apply_managed_category_label`. Sending/replying/forwarding/deletion and generic Gmail query
  execution remain absent.
- [ ] `P8D-02` Implement Email's P2 argument canonicalizer for all four new writes. Under the immutable
  user/channel/reference-set binding, resolve current `E1`-style display aliases to authorized Gmail
  message IDs or an equivalent Email-owned opaque stable ref after schema/authorization checks but before
  parent/child hashing or approval. Reject stale, missing, ambiguous, cross-channel, and duplicate targets;
  sort the stable refs for independent batches. Re-run resolution at approved execution and require the
  same canonical refs/hashes. E-labels remain presentation metadata only and never enter an operation ID.
- [ ] `P8D-03` Make `email.set_review_state` and `email.correct_local_category` true atomic batches. Add
  bulk storage methods that validate every target first, open one `BEGIN IMMEDIATE`, insert-or-compare the
  parent in `email_tool_operations` as transaction-local `reserved`, perform every local mutation through transaction-aware
  no-commit primitives, store the bounded result, and mark the row `committed` in that same transaction.
  Any failure rolls back the entire mutation and reservation. Same complete operation identity/hash/count
  replays the stored result; any mismatch conflicts. Because reservation and effect share one transaction,
  a crash leaves either no row/effect or a committed row/effect; a visible local `reserved` row is an
  invariant violation and blocks retry. Set `operation_identity_hash` to the 64-hex digest suffix of the
  matching `toolop_v1_` operation ID and set the unique parent key to
  `main-email-parent:v1:<operation_id>`.
- [ ] `P8D-04` For each provider independent batch, use P7's `EffectManifestReservation` to atomically
  commit two records in one Core transaction: P7's immutable redacted ticket manifest, and an Email-owned
  `email_tool_operations` parent whose private bounded `recovery_manifest_json` contains the canonical
  target refs plus every validated per-child argument required to recreate the child rows. The two records
  share the same expected count, and P7's redacted projection stores the Email parent's opaque
  `recovery_manifest_hash` without its values; P7's own `batch_manifest_hash` remains a separate hash of
  the redacted manifest. Neither record is authoritative for the other's data.
  Register the Email reservation callback only at the existing composition root; Ticket and Email domain
  packages must not import one another.
  Then insert-or-compare the complete sorted child set and move the Email parent from `reserved` to
  `queued` in one Email `BEGIN IMMEDIATE` transaction; every
  child row is `queued` and visible together or none is. Replace `INSERT OR IGNORE` and generated UUIDs on
  this path with the supplied `toolchild_v1_` Email-owned durable child/correlation ID and exact equality
  checks over parent manifest, parent ID, child index, canonical target, child argument hash, requested
  effect, identity binding, and expected count. New child rows use
  `main-email-child:v1:<child_operation_id>` as their existing required `idempotency_key`. On startup and
  before each bounded claim, the Email effect-recovery sweep scans only nonterminal private parents in
  `reserved|queued` with a child count below expected and reconstructs/compares the complete set from the
  Email recovery manifest; it never reconstructs values from P7 hashes. A post-child-transaction crash sees
  the full set. Workers retry only queued or expired-lease children and never a verified/`cancelled` child.
  A post-reservation policy or
  authorization denial must still materialize/retain every expected child as nonclaimable
  `status='cancelled'` with locked `last_error_code='policy_denied'`; ordinary no-effect cancellation uses
  the distinct locked code `execution_cancelled`. Report the former to P7 as `denied` and the latter as
  `cancelled`. All verified reduces the Email parent to `completed`; verified mixed with any other terminal
  child is `partial`; all `cancelled/execution_cancelled` semantic outcomes is `cancelled`; and zero
  verified plus any denied/dead-letter semantic outcome is `failed`. Reduction to a terminal parent and
  private-manifest clearing occur only after P7 confirms the outcome for every expected child. A terminal
  parent is never an effect-recovery candidate; P8D-06's separate outcome-reconciliation sweep may inspect
  it but can neither recreate nor claim effects. Verified children remain committed and receive no
  duplicate effect.
- [ ] `P8D-05` Classify projection-only versus provider effects truthfully.
  `email.move_to_spam` is destructive external and always requires formal approval; approval cards use
  bounded sender/subject-safe summaries, never bodies. `email.correct_local_category` must use a new
  local-only service path and must never call `_with_label_reconciliation` or create a label/mailbox
  operation. Gmail labeling occurs only when Main separately selects and authorizes P5F's additive
  `email.apply_labels` or `email.remove_labels`; classifier category never implies either call.
- [ ] `P8D-06` Preserve the existing read-before/write/read-after provider protocol and wire P7's
  `AsyncChildOutcomeSink` into P5F's `app/workers/email_operations_worker.py`. The child ID is not a Gmail
  idempotency token--Gmail receives only the state mutation. Retry safety comes from desired-state checks,
  leases, and idempotent label state. A queued call returns child operation/job refs, not a committed
  receipt. Only after verified provider read-back does the worker record a `verified` outcome and exactly
  one receipt keyed by the child ID; dead-letter, rollback-`cancelled`, and policy-`denied` paths record the
  matching content-free no-receipt outcome. The parent receives no effect receipt. A crash after Gmail mutation but before local completion
  must verify desired state on retry, complete the existing child, and emit its one receipt without a
  second mutation. A separate bounded startup outcome-reconciliation sweep scans every parent-bound
  terminal child whose canonical P7 outcome is absent and maps Email truth exactly: `verified` reports
  `verified` plus its receipt; `dead_letter` reports `dead_letter` without a receipt;
  `cancelled/execution_cancelled` reports `cancelled` without a receipt; and
  `cancelled/policy_denied` reports `denied` without a receipt. It invokes the sink idempotently through the
  parent-operation/manifest lookup, never changes a child back to queued, never claims it, and never calls
  Gmail. A conflicting existing outcome blocks the parent. Once the sink confirms every expected outcome,
  the reducer terminalizes the parent and clears its recovery JSON atomically. A post-approval per-child
  authorization denial transitions its existing row, or materializes the full missing set from the private
  parent manifest, as `cancelled/policy_denied` before reporting P7 `denied`; it can never leave an absent
  child that effect recovery could recreate as queued.
- [ ] `P8D-07` Make Core migration 013 the only P8D schema-change authority. P5F migration 010 already
  owns `email_tool_operations`, `email_managed_labels`, `email_message_managed_labels`, and
  `email_managed_label_operations`; P8D must extend those authorities rather than recreate or replace
  them. Refactor any remaining Email schema SQL into a transaction-safe helper that never commits
  internally and never calls `executescript`. Migration 013 runs inside P2's explicit Core migration
  transaction, upgrades populated version 12, and creates the complete current Email schema on fresh
  version 13. Runtime `apply_email_agent()` only validates/uses the versioned schema.

  Migration 013 additively gives `email_tool_operations` the P7 linkage fields not already present:
  unique nullable `idempotency_key`, `operation_identity_hash`, and `parent_manifest_hash`. It retains
  P5F's `expected_child_count` name and private `recovery_manifest_json/hash`; it does not introduce a
  duplicate expected-count or operation table. Add nullable `parent_manifest_hash` to
  `email_managed_label_operations` for new post-P7 calls, preserving every earlier P5F row and receipt.
  Add nullable `parent_operation_id`, `parent_manifest_hash`, `child_index`, and `arguments_hash` only to
  `email_mailbox_operations`, with unique partial parent/index and parent/message indexes plus all-null or
  all-non-null grouping guards. The legacy exclusive-category `email_label_operations` table remains
  unchanged, read-only history, and ineligible for both workers. `email_spam_operations` also remains
  read-only history; every new spam/mark-read child uses `email_mailbox_operations`.

  Parent manifest hashes bind P7's redacted manifest separately from Email's private recovery hash.
  Local atomic rows keep provider-manifest fields null. Closed parent states remain
  `reserved|queued|committed|completed|partial|failed|cancelled`. Retain private recovery JSON only while
  a child is nonterminal or lacks its matching P7 receipt/no-receipt outcome, then clear it atomically.
  Retention must block deletion of a referenced message while any linked operation is nonterminal.
  Record Core version 13 as additive with minimum reader 7; never rewrite legacy rows or put private
  manifests into generic history, tickets, events, or model observations.
- [ ] `P8D-08` Extend P5F's Compose-owned `email-operations-worker` and its existing `--readiness-only`
  path for the new mailbox row kinds. Readiness validates protected config, token mounts, Core
  schema/version, supported row kinds, and single-worker ownership without claiming a row, mutating
  SQLite, calling Gmail, or emitting content. Before shadow and active canaries, run the common P8D gate,
  prove the tracked worker service/profile and fresh heartbeat, and write a separate Email dead-letter
  baseline. No external timer or manually maintained daemon is introduced.
- [ ] `P8D-09` Test populated version-12 to version-13 upgrade and fresh version-13 creation with Email
  disabled; interrupted migration retry; retained P5F/P1-reader acceptance and pre-P1-reader refusal;
  preservation/replay of existing P5F additive-label parents/children; legacy mailbox inserts with null
  grouping columns; all-null/all-non-null guards; complete parent/
  child identity/idempotency conflicts; local rollback before/after transaction commit with proof that no
  local `reserved` row is visible; crash before/after the atomic private/redacted parent reservation,
  before/after all-child insertion, after Gmail mutation/before local completion, after verification/
  before receipt, startup repair of a verified child missing its receipt, private-manifest child-set
  recovery, and mixed verified/dead-letter reduction. Also prove local category correction creates
  zero provider-operation rows, wrong user/channel and stale aliases fail before identity, spam caps at
  five, and duplicate approval/delivery has at most one effect per child. Exercise rollback from a
  crash-reserved parent with zero children and from a partial child set: missing children become
  no-effect `cancelled` outcomes, a crash during cancellation resumes idempotently, every parent reaches
  its truthful terminal state, recovery JSON clears only after P7 reconciliation, and the older image sees
  zero recoverable new-provider work. Also test post-approval per-child denial, denial restart recovery,
  all-denied, all-cancelled, and verified/denied reductions; a `cancelled/policy_denied` child is never
  claimable or reconstructed as queued. Inject a crash after persisting each of `verified`, `dead_letter`,
  `cancelled/execution_cancelled`, and `cancelled/policy_denied` but before sink delivery; startup must
  repair the exact missing P7 outcome without a provider call and only then terminalize the parent.

Exact P8D Email consumer supplement; it reuses the tracked P5F worker service and records no protected
identifier:

```bash
(
  set -euo pipefail
  docker compose --env-file .env -f deploy/docker/compose.yaml --profile email-operations \
    run --rm --no-deps --no-build -T email-operations-worker \
    python -m app.workers.email_operations_worker --readiness-only

  email_dead_letter_baseline="/opt/jarvis/data/reasoning-led-P8D-email-dead-letters.json"
  docker compose --env-file .env -f deploy/docker/compose.yaml run --rm --no-deps --no-build -T \
    jarvis python scripts/check_worker_readiness.py \
    --database /opt/jarvis/data/jarvis_v2.db \
    --write-email-dead-letter-baseline "$email_dead_letter_baseline"
)
```

After the active canary, rerun the readiness-only command, require the worker heartbeat to be fresh, and replace
`--write-email-dead-letter-baseline` with
`--email-dead-letter-baseline /opt/jarvis/data/reasoning-led-P8D-email-dead-letters.json`. Any worker,
readiness, schema, or dead-letter failure blocks activation.

Rollback: remove exactly `email.set_review_state`, `email.correct_local_category`,
`email.mark_read_complete`, and `email.move_to_spam` from `MAIN_TOOL_ENABLED_OPERATIONS` while retaining the
certified P5F read and additive-label IDs, then return the Compose-owned worker to its P5F row-kind policy
and wait for any P8D mailbox claim to quiesce or expire. Stop new approval claims and expire still-pending proposals
through Human Review; after proving no claim/effect, cancel approved proposals and their unclaimed
execution jobs, and reconcile every `executing` proposal before a terminal transition. Through an
Email-owned cancellation method, cancel every new nonterminal label/
mailbox row with non-null `parent_operation_id`; a claimed row must first quiesce or reach lease expiry and
reconcile provider state. Map provable no-effect queued/expired-claim rows to Email `cancelled`, verified
effects to `verified`, and uncertainty to nonclaimable `dead_letter` with an operator-visible error code;
never leave a parent-bound row `queued` or `claimed`. For every affected provider parent, the same bounded
cancellation method must use its still-private recovery manifest to insert-or-compare any missing child as
`cancelled` without a provider call, report every terminal child through P7's outcome sink, and then reduce
the parent atomically by semantic outcome, never raw Email status: all
`cancelled/execution_cancelled` is `cancelled`; all verified is `completed`; verified mixed with any other
terminal outcome is `partial`; and no verified child plus any `dead_letter|denied` outcome is `failed`.
Thus `cancelled/policy_denied` counts as denied and an all-policy-denied parent is `failed`. Clear
`recovery_manifest_json` only after every expected child and P7 receipt/no-receipt outcome is reconciled.
Assert zero parent-bound child rows in `queued|claimed`, zero provider parents in
`reserved|queued`, no expected-child count mismatch, and no missing P7 outcome before deploying an older
image; explicitly assert the all-policy-denied fixture reduced to `failed`. Preserve all terminal children,
content-free outcomes, and receipts. Only then may the exact retained
P8C Documents-bridge image, or a later
image already proven by both Core and Documents reader-check commands, be deployed; the pre-P8C/P1 image
is forbidden once Documents schema 15 exists. Its legacy Email worker may resume for rows whose
`parent_operation_id` is null; `cancelled|dead_letter|verified` new rows are never
claimable. Never down-migrate, try to unsend, or automatically reverse spam moves.

### P8E - Calendar writes

Status: `not_started`
Depends on: `P8D` and `P5B`
Runtime default after subphase: Calendar writes may be independently active

Allowed files: `app/skills/domains/calendar/context.py`, `app/skills/domains/calendar/handler.py`,
`app/skills/domains/calendar/service.py`, `app/skills/domains/calendar/storage.py`,
`app/skills/domains/calendar/receipts.py`, `app/services/google/calendar_live.py`,
`app/tickets/verifiers/calendar.py`,
`app/prompts/skills/calendar_skill.md`, `tests/unit/test_calendar_service.py`,
`tests/unit/test_google_calendar_live_paths.py`, `tests/unit/test_calendar_ticket_verifier.py`,
`tests/unit/test_main_tool_loop.py`, and
`tests/integration/test_action_approval_restart.py`.

- [ ] `P8E-01` Publish typed `calendar.create_event` (invitees forbidden),
  `calendar.create_event_with_invites` (one or more invitees), `calendar.update_event`, and
  `calendar.delete_event` with exact event/reference/patch schemas, timezone, calendar target, and
  resource version. Quick Add is not the new execution contract.
- [ ] `P8E-02` For both create tools, derive the provider event ID as lowercase
  `"jarvis" + base32hex(SHA-256(operation_id)).rstrip("=")`, set private operation/argument-hash
  properties, and use typed Events Insert rather than Quick Add. On conflict or uncertain response, GET
  that exact ID: matching hashes/spec return idempotent success; mismatch is a terminal conflict; absence
  permits only the same operation ID to retry. For update/delete, require the captured provider revision
  and conditional write/delete. After timeout, GET the exact event and compare the desired state or the
  recorded pre-delete snapshot before classifying committed/retryable; never blindly repeat. The ID
  encoding and length must remain within the documented
  [Google Calendar event-ID contract](https://developers.google.com/workspace/calendar/api/v3/reference/events).
- [ ] `P8E-03` `calendar.create_event` and non-attendee update require no formal approval.
  `calendar.create_event_with_invites` is outbound communication and always requires formal approval.
  Attendee mutation on update remains absent in this phase. Delete always requires formal approval.
- [ ] `P8E-04` Reauthorize calendar/person and resource version after approval; return provider event ID,
  committed state, and receipt. Local fallback must return `not_synced` truth and may not satisfy a live
  Google request.
- [ ] `P8E-05` Test duplicate/retry/timeout, stale event, ambiguous reference, wrong calendar, invitee
  approval/rejection/expiry, delete approval, provider partial failure, and restart after commit.

Rollback: remove exactly `calendar.create_event`, `calendar.create_event_with_invites`,
`calendar.update_event`, and `calendar.delete_event` from `MAIN_TOOL_ENABLED_OPERATIONS` while retaining
`calendar.query_events`; do not automatically recreate deleted events or delete created events. Surface
committed effects and require a separately authorized compensation.

## P9 - Cross-domain composition and truthful partial completion

Status: `not_started`
Depends on: `P8E`; each scenario also requires independently certified tools
Runtime default after phase: ordinary bounded composition is available whenever every exact operation is
independently active; there is no separate workflow/composition allowlist

Objective: complete multi-domain asks by sequencing ordinary semantic calls, without a workflow intent,
domain import, global planner branch, distributed transaction, or automatic compensation.

Allowed production files:

- `app/core/main_tool_loop.py`
- `app/core/tool_loop_types.py`
- `app/core/action_execution.py`
- `app/core/approved_action_execution.py`
- `app/core/persistence_policy.py`
- `app/core/turn_finalizer.py`
- `app/skills/tool_contracts.py`
- `app/skills/authorized_executor.py`

Allowed tests and acceptance data:

- ADD `tests/integration/test_main_tool_composition.py`
- `tests/unit/test_main_tool_loop.py`
- `tests/unit/test_authorized_skill_executor.py`
- `tests/unit/test_human_review_service.py`
- `tests/unit/test_main_model_acceptance.py`
- `tests/integration/test_action_approval_restart.py`
- `benchmarks/models/main_acceptance_cases.json`
- `tests/unit/test_architecture_boundaries.py`

Forbidden in this phase: domain code changes; new workflow/compound intent; domain-to-domain import;
workflow/composition allowlist; bulk authority inherited from an earlier call; automatic rollback;
concurrent effects; unbounded plans.

Tasks:

- [ ] `P9-01` Generalize the P5A same-domain observation primitive to the exact tagged
  `provenance_claims`, root-local `observation_ref`, cross-domain transfer-field scope, sensitivity matrix,
  conservative `request_derived` exposure, untrusted propagation, and
  most-restrictive persistence rules in this plan. The server binds subtree hashes and validates the next
  closed schema; it never passes a provider object, credential, authority snapshot, unrestricted blob,
  or highly restricted value across domains. Do not add a phrase parser or composition allowlist.
- [ ] `P9-02` Re-resolve and reauthorize every call independently. Assign a new stable operation ID and
  receipt/ticket to each effect, including every child of an independent batch. A read authorization
  never grants a write; approval of one call never approves another.
- [ ] `P9-03` Execute sequentially within the global eight-step/deadline limits. After a denial, approval
  pause, failure, or exhausted limit, preserve committed results and produce truthful `completed`,
  `pending`, `denied`, and `failed` portions.
- [ ] `P9-04` Do not auto-compensate. A request to undo a committed effect is a new call with current
  policy and approval. No distributed transaction or feature-private saga table is added.
- [ ] `P9-05` Add model-free scenarios with exact expected policy: Email summarize-derived List text
  waits for formal transfer approval, binds the manifest, survives restart once, clears proposal payload
  at terminal state, and writes nothing on reject/expiry; an Email read followed by List text already
  verbatim in the trusted request needs no declassification approval; Research-derived List text is
  allowed as untrusted data; Document inspect then propose metadata remains same-domain; Calendar query
  then create a separate event accepts request-derived date normalization without a phrase parser; a
  Document-to-List transfer is denied; forged/stale observation refs, missing/overlapping claims, revoked
  authorization between calls, first-effect-success/second-failure, approval pause, malicious
  observation instructions, repeat, and timeout all stop truthfully without duplicate effects.
- [ ] `P9-06` Add held-out natural-language cases with paraphrases and new valid filter compositions.
  Prove none requires a new `Intent`, router phrase, or workflow handler branch.

Verification:

```bash
python -m pytest -q tests/integration/test_main_tool_composition.py tests/integration/test_action_approval_restart.py tests/unit/test_main_tool_loop.py tests/unit/test_authorized_skill_executor.py tests/unit/test_human_review_service.py tests/unit/test_main_model_acceptance.py tests/unit/test_architecture_boundaries.py
python -m compileall -q app scripts tests
python -m pytest -q
phase_export="$(mktemp -d)"
python scripts/export_clean_repo.py "$phase_export"
git diff --check
```

Exit gate: every scenario reports each call independently; no cross-domain import or new durable
orchestration authority exists; three consecutive authoritative Ubuntu acceptance runs pass; a 48-hour canary has
zero unauthorized/unapproved/duplicate effects. Rollback: remove all write IDs from
`MAIN_TOOL_ENABLED_OPERATIONS` first, then remove only the failing read IDs. This disables those active
operations and does not restore legacy execution; set mode `off` only for full legacy rollback. Preserve
committed effects and pending records for inspection.

## P10A - Main-only semantic cutover with dormant Micro rollback

Status: `not_started`
Depends on: every retained interactive domain has passed its own gate and `P9`
Runtime default after phase: Main handles all accepted semantic Discord turns; Micro code remains dormant

Objective: make the P3 active-mode Micro bypass permanent across `off|shadow` as well by disabling the
temporary legacy Micro flag, while retaining one flag-controlled code rollback during the observation
window. This phase does not introduce the first active-mode bypass; it removes the remaining dormant
legacy branch from every mode.

Allowed production/configuration files:

- `app/core/router.py`
- `app/core/request_flow.py`
- `app/core/conversation_flow.py`
- `app/core/clarification_coordinator.py`
- `app/core/context_flow.py`
- `app/core/main_backend.py`
- `app/core/main_turn_contract.py`
- `app/core/main_turn_commitment.py`
- `app/core/main_repair_flow.py` - compatibility caller routing only
- `app/services/discord/bot.py`
- `app/config.py`
- `app/runtime.py`
- `.env.example`
- `deploy/docker/compose.yaml`
- `deploy/docker/README.md`
- `app/prompts/jarvis_identity.md`

Allowed tests and acceptance data:

- `tests/router_support.py`
- `tests/unit/test_router_discord_micro_gate.py`
- `tests/unit/test_router_micro_skill_gate.py`
- `tests/unit/test_router_handoff.py`
- `tests/unit/test_router_pending_interaction.py`
- `tests/unit/test_discord_adapter.py`
- `tests/unit/test_main_backend.py`
- `tests/unit/test_main_model_acceptance.py`
- `tests/integration/test_command_pack_api.py`
- `benchmarks/models/main_acceptance_cases.json`

Forbidden in this phase: deleting Micro files/config/schema; changing domain policy; changing providers;
new `!` privileges; rewriting historical route/intent/session records.

Tasks:

- [ ] `P10A-01` Preserve the already-certified P3 rule that active mode routes every accepted Discord
  semantic message to Main, including retained leading `!` syntax, and extend Main ownership to
  `off|shadow` when the temporary legacy Micro flag is false. Strip the UI prefix only after the trusted
  adapter envelope is created; the prefix grants no capability or authorization. In active mode, the
  path uses generic `MainActionCommitment`; no request may fall back to `MAIN_ACTION_INTENTS` before
  discovery.
- [ ] `P10A-02` Stop constructing/calling Micro for new turns, new clarifications, and failed commands
  when `LEGACY_MICRO_ROUTING_ENABLED=false`. Direct API/command-pack callers enter the same typed Main
  boundary or fail closed with a documented compatibility response.
- [ ] `P10A-03` Stop writing new Micro-specific handoff/classification fields except fields required for
  backward-compatible schemas. Preserve old rows and operator display as historical labels.
- [ ] `P10A-04` Remove Micro instructions from the live Main/Discord prompt path and capability
  narration. Do not delete prompt artifacts yet.
- [ ] `P10A-05` Preserve scheduler, adapter, operator, attachment, wake/session, and conversation routes.
  A scheduler-owned operation must not become interactive as a side effect of cutover.
- [ ] `P10A-06` Rewrite boundary tests so prefixed/unprefixed wording, pending replies, unknown asks,
  action clarifications, unauthorized skills, and child profiles all exercise Main with identical
  deterministic policy. Include an authorized semantic operation with no legacy `Intent` and prove it is
  reachable, plus the inverse case where a known legacy intent cannot bypass an unavailable tool. Prove
  active mode bypasses Micro regardless of the legacy flag and, after this phase's flag is false,
  `off|shadow` also make zero Micro calls.
- [ ] `P10A-07` In protected authoritative Ubuntu configuration set `LEGACY_MICRO_ROUTING_ENABLED=false`, retain the
  old Main/Micro-capable image and configuration snapshot, recreate only affected services with the
  required Compose env file, and observe for 72 hours.

Cutover gate: three consecutive full acceptance runs; 100% mandatory/safety cases; at least 95% overall;
zero failed token loops, Micro model requests, unauthorized/unapproved/duplicate effects, invalid
dispatches, or scheduler regressions; Main p95 no more than 25% above its recorded baseline and no more
than 60 seconds. Rollback: set `LEGACY_MICRO_ROUTING_ENABLED=true` and mode `off`, recreate Jarvis using
the retained configuration/image, and verify the legacy route suite. Do not change stored domain data.

## P10B - Micro implementation retirement

Status: `review_required`
Depends on: successful `P10A` observation and explicit user approval for this destructive cleanup
Runtime default after phase: Main only; no Micro executable path

Objective: remove dormant Micro implementation/configuration while retaining backward-readable history
and an image/database rollback boundary.

Entry gate:

- [ ] `P10B-ENTRY-01` Present P10A telemetry, all remaining Micro references, retained image/config/DB
  compatibility evidence, and proposed exact diff to the user; obtain explicit approval.
- [ ] `P10B-ENTRY-02` Run
  `rg -n -i "micro|FAST_COMMAND_INTENTS" app tests deploy scripts .env.example benchmarks`
  and classify every result as remove, rename to neutral compatibility history, or preserve as historical
  schema reader. This deliberately includes `SessionOwner.MICRO`, `micro_tool`, generated metadata,
  dashboard/API labels, bootstrap scripts, and tests. Add the resulting exact full-path allowlist and
  per-file disposition to this plan, obtain review, and only then change status from `review_required`.
  No P10B implementation edit is authorized by the current baseline list alone.

Expected deletions:

- `app/core/micro_jarvis.py`
- `app/core/micro_backend.py`
- `app/prompts/microjarvis_identity.md`
- `app/prompts/micro_jarvis_skills.md`
- `app/prompts/micro_jarvis_skills.md.meta.json`
- `app/prompts/skills/micro_jarvis_skills.md`
- `app/prompts/skills/micro_jarvis_skills.md.meta.json`
- `tests/unit/test_micro_jarvis.py`

Known live-reference areas that the required inventory must include are `app/core/request_pipeline.py`,
`app/core/agent_routing.py`, `app/core/conversation_routing.py` if it exists at execution time,
`app/api/principals.py`, `app/api/routes/dashboard.py`, `app/ui/dashboard.html`,
`deploy/ubuntu/bootstrap.sh`, routing/session/state files, all skill Markdown/generated metadata, and all
affected tests. This is a completeness warning, not an implementation allowlist; the reviewed addendum
is authoritative because P1-P10A may change the file set before retirement.

Tasks:

- [ ] `P10B-01` Remove construction, inference, routing, repair-handoff, generated-artifact, model-load,
  Compose, and configuration paths that can call or require Micro.
- [ ] `P10B-02` Replace user-facing/runtime names with Main/tool-loop-neutral names only where current
  behavior needs them. Preserve historical enum/column/event values as read-only compatibility; stop
  writing them. Do not drop or rewrite schema/history in this phase.
- [ ] `P10B-03` Replace deleted Micro tests with Main boundary/compatibility assertions. Preserve coverage
  for prefix envelopes, clarification, session continuity, authorization failure, offline startup, and
  historical record reading.
- [ ] `P10B-04` Regenerate skill artifacts and fail if any executable Micro contract remains. A plain
  historical documentation mention is allowed only when clearly marked non-runtime.
- [ ] `P10B-05` Run the inventory command again. Runtime/configuration results must be zero except the
  named backward-read compatibility constants and migration comments listed in the evidence record.

Verification: all affected targeted tests, common exit commands, three Main acceptance passes, clean
Compose configuration, cold restart, and 72-hour authoritative Ubuntu observation. The Main model must be the only
semantic model resident/requested for Jarvis; other independently owned OCR/VLM models are unaffected.

Stop if removal would make an old database unreadable, break a direct caller without a typed Main
replacement, or require deleting history. Rollback: deploy the retained pre-P10B image and protected
configuration against the compatibility-tested additive database. Do not reverse schema by deletion.

## P11 - Authoritative Ubuntu certification, promotion, and release observation

Status: `not_started`
Depends on: the intended final runtime phase (`P10A`, or `P10B` only if separately approved)
Runtime after phase: certified release or complete rollback

Objective: prove the clean public tree, authoritative Ubuntu runtime, protected configuration, GPU Main,
Discord approvals, domain canaries, durable recovery, and rollback as one release candidate.

This phase changes no application behavior. Allowed tracked files are this plan's evidence fields and
architecture/operations documentation already changed by completed phases. Protected configuration,
runtime data, backups, images, and deployment state change only on the authoritative Ubuntu runtime.

Pre-promotion tasks:

- [ ] `P11-01` Resolve the authoritative checkout from private operator context, verify it is the expected
  repository/commit, and record `git status --short`, `git rev-parse HEAD`, dependency lock hashes,
  effective non-secret flag names/values, Compose service/image IDs, and previous rollback image.
- [ ] `P11-02` Create a fresh clean export outside the checkout and run the public-tree check, lint,
  compile, architecture tests, full suite, and acceptance fixture validation inside it. Verify P0A's
  zero-finding evidence. Build and tag the candidate image from this export only; do not release from the
  raw private checkout.
- [ ] `P11-03` Create and verify an online Core SQLite backup with the existing database manager. If the
  Documents profile is enabled or P8C is active, also create and verify one coordinated encrypted
  document backup using the existing document backup tool and protected storage-root value. When that
  coordinated backup is required, its `core.db` and `documents.db` are the inseparable rollback pair; the
  standalone Core backup is verification evidence and must never be restored by itself. Run both retained-
  image reader checks against those two same-generation standalone artifacts, then run the existing
  isolated Documents restore drill against that exact generation. Retain the prior image/configuration and
  do not run a restore over production. Record Core `user_version=13`, required tables and additive
  compatibility rows 8 through 13. When Documents migration 15 exists, record Documents version 15 and
  prove the retained compatibility-aware version-14 image can open it; a pre-bridge Documents image is not
  a valid rollback image.
- [ ] `P11-04` Record the prior local image ID under a unique rollback tag, retag the image built from the
  clean export as `jarvis-poc-app:local`, and never call Compose `build` from the private checkout. The raw
  checkout supplies only protected `.env`, Compose topology, runtime mounts, and data. Recreate
  `accelerator-admission` and wait healthy, then recreate `jarvis` and wait healthy. Before any recreate,
  generate the closed runtime-requirements manifest from protected flags, active operations/descriptors,
  and unfinished durable/domain work. Recreate every service required by that manifest, plus any optional
  service from the fixed list that was previously running solely to preserve topology. Prior running state
  can retain an optional service but can never omit a required one. Every `up`/`run` uses `--no-build`;
  every service with a healthcheck uses bounded `--wait`; every started worker must produce its fixed fresh
  non-degraded heartbeat within 180 seconds and no dead-letter increase. Stop the tracked Email worker
  before retag/recreate; when Email consumption is required or the worker was previously running, recreate
  it from the candidate and require readiness-only plus heartbeat success. Never start the legacy one-shot
  worker or its backlog. Do not
  recreate Ollama, SearXNG, Paperless, Docling, or PaddleOCR solely for this release. Every Compose
  invocation includes `--env-file .env`.
- [ ] `P11-05` Run three consecutive Main acceptance benchmarks against `gpt-oss:20b` from a secured
  one-off `jarvis` Compose container on the internal accelerator-control network, inheriting the mounted
  admission key and mounting only the clean export's read-only benchmark directory. Record model digest,
  GPU residency, context, pass rate, failed loops, overall p50/p95, each group p50/p95, every per-case
  ceiling, and the `legacy_latency_v1` comparison to its unchanged P1 baseline.
- [ ] `P11-06` Validate protected Discord purposes and immutable identities without printing IDs. Send a
  synthetic approval, approve/reject from correct and incorrect contexts, restart between approval and
  execution, and validate the positively attributed proactive-notification route.
- [ ] `P11-07` Run synthetic canaries for every enabled read tool, each enabled write effect class, denial,
  approval, duplicate delivery, restart recovery, scheduler-only denial, partial completion, and legacy
  history reading. Never use real destructive data for a canary. Re-run runtime requirements, all derived
  heartbeat checks, the durable-job dead-letter comparison, and the separate Email operation dead-letter
  comparison after the canaries. Start and prove any newly required consumer before continuing; a removed
  requirement is acceptable only when its fixed reason shows the protected feature is disabled and no
  unfinished work remains. An unexplained change or any baseline increase blocks release.
- [ ] `P11-08` Observe for the duration required by the latest activated boundary: at least 24 hours for
  reads, 48 hours for writes/composition, and 72 hours for Main-only/Micro retirement. The longest
  applicable duration wins.

Reference command sequence on the authoritative Ubuntu runtime; replace only shell variables with
protected operator values:

```bash
(
  set -euo pipefail

  git status --short
  git rev-parse HEAD

  release_checkout="$(pwd)"
  release_export="$(mktemp -d)"
  python scripts/export_clean_repo.py "$release_export"
  cd "$release_export"
  python scripts/check_public_tree.py --root .
  python -m ruff check app scripts tests --select E9,F63,F7,F82
  python -m compileall -q app scripts tests
  python -m pytest -q tests/unit/test_architecture_boundaries.py
  python -m pytest -q
  docker build --file deploy/docker/Dockerfile --tag jarvis-poc-app:reasoning-led-candidate .
  candidate_image_id="$(docker image inspect jarvis-poc-app:reasoning-led-candidate --format '{{.Id}}')"
  test -n "$candidate_image_id"
  cd "$release_checkout"

  core_backup_path="$(python scripts/manage_database.py backup --destination ./data/backups | sed -n 's/^backup=//p')"
  test -n "$core_backup_path"
  test -f "$core_backup_path"
  python scripts/manage_database.py verify --source "$core_backup_path"

  docker compose --env-file .env -f deploy/docker/compose.yaml config --quiet
  docker compose --env-file .env -f deploy/docker/compose.yaml images

  running_app_services="$(docker compose --env-file .env -f deploy/docker/compose.yaml ps --services --status running)"
  service_was_running() {
    printf '%s\n' "$running_app_services" | grep -Fxq "$1"
  }
  previous_image_id="$(docker image inspect jarvis-poc-app:local --format '{{.Id}}')"
  test -n "$previous_image_id"

  restore_pre_backup_topology() {
    local restore_status=0
    local service
    for service in jarvis paperless-webserver document-worker document-gateway; do
      if service_was_running "$service"; then
        docker compose --env-file .env -f deploy/docker/compose.yaml \
          up -d --no-deps --no-build --pull never "$service" || restore_status=1
      else
        docker compose --env-file .env -f deploy/docker/compose.yaml \
          stop "$service" || restore_status=1
      fi
    done
    return "$restore_status"
  }

  expected_documents_schema_version="<copy NONE, 14, or 15 from phase evidence>"
  case "$expected_documents_schema_version" in NONE|14|15) ;; *) exit 1 ;; esac
  documents_enabled_now="$(docker compose --env-file .env -f deploy/docker/compose.yaml \
    run --rm --no-deps --no-build -T jarvis \
    python -c 'from app.config import settings; print("true" if settings.documents_enabled else "false")')"
  case "$documents_enabled_now" in true|false) ;; *) exit 1 ;; esac
  documents_backup_required=false
  if [ "$documents_enabled_now" = true ] || [ "$expected_documents_schema_version" != NONE ]; then
    documents_backup_required=true
  fi

  if [ "$documents_backup_required" = true ]; then
    documents_storage_input="<copy exact protected Documents storage root>"
    documents_backup_input="<copy exact protected Documents backup root>"
    test "$documents_storage_input" != "<copy exact protected Documents storage root>"
    test "$documents_backup_input" != "<copy exact protected Documents backup root>"
    documents_storage_root="$(readlink -f -- "$documents_storage_input")"
    documents_backup_root="$(readlink -f -- "$documents_backup_input")"
    test -d "$documents_storage_root"
    test -d "$documents_backup_root"
    case "$documents_backup_root" in "$documents_storage_root"/*) ;; *) exit 1 ;; esac
    test "$expected_documents_schema_version" != NONE

    document_backup_generation="reasoning-led-p11-$(date -u +%Y%m%dT%H%M%SZ)"
    document_backup_path="$documents_backup_root/$document_backup_generation"
    test ! -e "$document_backup_path"
    test ! -L "$document_backup_path"
    document_backup_status=0
    python scripts/manage_document_backup.py backup \
      --storage-root "$documents_storage_root" \
      --backup-root "$documents_backup_root" \
      --generation "$document_backup_generation" \
      --source-revision "$(git rev-parse HEAD)" \
      --core-database ./data/jarvis_v2.db \
      --compose-file deploy/docker/compose.yaml \
      --env-file .env || document_backup_status=$?
    topology_restore_status=0
    restore_pre_backup_topology || topology_restore_status=$?
    test "$document_backup_status" -eq 0
    test "$topology_restore_status" -eq 0
    post_backup_running_services="$(docker compose --env-file .env -f deploy/docker/compose.yaml ps --services --status running)"
    for service in jarvis paperless-webserver document-worker document-gateway; do
      if service_was_running "$service"; then
        printf '%s\n' "$post_backup_running_services" | grep -Fxq "$service"
      else
        if printf '%s\n' "$post_backup_running_services" | grep -Fxq "$service"; then exit 1; fi
      fi
    done
    test -d "$document_backup_path"
    python scripts/manage_document_backup.py verify "$document_backup_path"

    coordinated_core_backup="$document_backup_path/core.db"
    coordinated_documents_backup="$document_backup_path/documents.db"
    test -f "$coordinated_core_backup"
    test ! -L "$coordinated_core_backup"
    test -f "$coordinated_documents_backup"
    test ! -L "$coordinated_documents_backup"
    coordinated_schema_versions="$(python - "$coordinated_core_backup" "$coordinated_documents_backup" <<'PY'
import sqlite3
import sys
from pathlib import Path

versions = []
for value in sys.argv[1:]:
    path = Path(value).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        versions.append(str(int(connection.execute("PRAGMA user_version").fetchone()[0])))
    finally:
        connection.close()
print(" ".join(versions))
PY
    )"
    actual_core_schema_version="${coordinated_schema_versions%% *}"
    actual_documents_schema_version="${coordinated_schema_versions#* }"
    test "$actual_core_schema_version" = 12
    test "$actual_documents_schema_version" = "$expected_documents_schema_version"

    retained_all_store_reader_image="$previous_image_id"
    docker image inspect "$retained_all_store_reader_image" >/dev/null
    docker run --rm --network none --read-only \
      --env PYTHONDONTWRITEBYTECODE=1 \
      --tmpfs /tmp:rw,noexec,nosuid,size=16m \
      --mount "type=bind,src=$coordinated_core_backup,dst=/verify/core.db,readonly" \
      "$retained_all_store_reader_image" \
      python scripts/manage_database.py reader-check --source /verify/core.db
    docker run --rm --network none --read-only \
      --env PYTHONDONTWRITEBYTECODE=1 \
      --tmpfs /tmp:rw,noexec,nosuid,size=16m \
      --mount "type=bind,src=$coordinated_documents_backup,dst=/verify/documents.db,readonly" \
      "$retained_all_store_reader_image" \
      python scripts/manage_document_backup.py reader-check --source /verify/documents.db

    documents_secrets_input="<copy exact protected Documents secrets root>"
    test "$documents_secrets_input" != "<copy exact protected Documents secrets root>"
    documents_secrets_root="$(readlink -f -- "$documents_secrets_input")"
    test -d "$documents_secrets_root"
    document_restore_drill_name="reasoning-led-p11-$(date -u +%Y%m%dT%H%M%SZ)"
    python scripts/drill_document_restore.py "$document_backup_path" \
      --storage-root "$documents_storage_root" \
      --drill-name "$document_restore_drill_name" \
      --secrets-root "$documents_secrets_root"
  fi

  rollback_image_tag="jarvis-poc-app:reasoning-led-rollback-$(date -u +%Y%m%dT%H%M%SZ)"
  docker image tag "$previous_image_id" "$rollback_image_tag"

  candidate_requirements_container="/opt/jarvis/data/reasoning-led-release-requirements.json"
  candidate_requirements_host="./data/reasoning-led-release-requirements.json"
  dead_letter_baseline="/opt/jarvis/data/reasoning-led-release-dead-letters.json"
  email_dead_letter_baseline="/opt/jarvis/data/reasoning-led-release-email-dead-letters.json"

  docker compose --env-file .env -f deploy/docker/compose.yaml run --rm --no-deps --no-build -T \
    jarvis python scripts/check_worker_readiness.py \
    --database /opt/jarvis/data/jarvis_v2.db \
    --write-dead-letter-baseline "$dead_letter_baseline" \
    --write-email-dead-letter-baseline "$email_dead_letter_baseline"

  if printf '%s\n' "$running_app_services" | grep -Fxq email-operations-worker; then
    docker compose --env-file .env -f deploy/docker/compose.yaml --profile email-operations stop email-operations-worker
  fi

  docker image tag jarvis-poc-app:reasoning-led-candidate jarvis-poc-app:local
  test "$(docker image inspect jarvis-poc-app:local --format '{{.Id}}')" = "$candidate_image_id"
  docker compose --env-file .env -f deploy/docker/compose.yaml run --rm --no-deps --no-build -T \
    jarvis python scripts/check_worker_readiness.py \
    --database /opt/jarvis/data/jarvis_v2.db \
    --write-runtime-requirements "$candidate_requirements_container"
  test -f "$candidate_requirements_host"

  python - "$candidate_requirements_host" <<'PY'
import json
import re
import sys

allowed_services = {
    "action-approval-worker",
    "ticket-review",
    "plane-sync",
    "email-operations-worker",
    "document-worker",
    "document-gateway",
    "discord-attachment-ingress",
}
allowed_workers = {
    "action_approval": 120,
    "ticket_review": 60,
    "plane_sync": 60,
    "email_operations": 120,
    "documents": 120,
}
allowed_reason_codes = {
    "protected_flag_enabled",
    "active_operation_dependency",
    "prospective_operation_dependency",
    "nonterminal_action_proposal",
    "unfinished_durable_job",
    "unfinished_domain_operation",
}
with open(sys.argv[1], encoding="utf-8") as stream:
    payload = json.load(stream)
if set(payload) != {
    "schema_version", "required_services", "required_workers",
    "email_consumer_required", "reasons",
} or payload["schema_version"] != 1:
    raise SystemExit("invalid runtime requirements shape")
services = payload["required_services"]
if services != sorted(set(services)) or not set(services) <= allowed_services:
    raise SystemExit("invalid runtime service requirement")
workers = payload["required_workers"]
worker_pairs = [(item.get("type"), item.get("max_age_seconds")) for item in workers]
if worker_pairs != sorted(set(worker_pairs)):
    raise SystemExit("duplicate or unsorted runtime worker requirement")
for worker_type, max_age in worker_pairs:
    if allowed_workers.get(worker_type) != max_age:
        raise SystemExit("invalid runtime worker requirement")
if not isinstance(payload["email_consumer_required"], bool):
    raise SystemExit("invalid Email consumer requirement")
if payload["email_consumer_required"] != ("email-operations-worker" in services):
    raise SystemExit("Email consumer/service requirement mismatch")
reasons = payload["reasons"]
if not isinstance(reasons, list):
    raise SystemExit("invalid runtime requirement reasons")
reason_pairs = []
for reason in reasons:
    if not isinstance(reason, dict) or set(reason) != {"code", "subject"}:
        raise SystemExit("invalid runtime requirement reason shape")
    code, subject = reason["code"], reason["subject"]
    if code not in allowed_reason_codes or not isinstance(subject, str):
        raise SystemExit("invalid runtime requirement reason")
    if re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", subject) is None:
        raise SystemExit("invalid runtime requirement subject")
    reason_pairs.append((code, subject))
if reason_pairs != sorted(set(reason_pairs)):
    raise SystemExit("duplicate or unsorted runtime requirement reasons")
PY

  runtime_service_required() {
    python - "$candidate_requirements_host" "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    required = json.load(stream)["required_services"]
raise SystemExit(0 if sys.argv[2] in required else 1)
PY
  }
  runtime_email_required() {
    python - "$candidate_requirements_host" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    required = json.load(stream)["email_consumer_required"]
raise SystemExit(0 if required else 1)
PY
  }
  docker compose --env-file .env -f deploy/docker/compose.yaml up -d --no-deps --force-recreate --no-build --wait --wait-timeout 180 accelerator-admission
  docker compose --env-file .env -f deploy/docker/compose.yaml up -d --no-deps --force-recreate --no-build --wait --wait-timeout 180 jarvis

  for app_service in action-approval-worker ticket-review plane-sync email-operations-worker document-worker document-gateway discord-attachment-ingress; do
    if runtime_service_required "$app_service" || service_was_running "$app_service"; then
      case "$app_service" in
        action-approval-worker) app_profile=approvals ;;
        ticket-review) app_profile=tickets ;;
        plane-sync) app_profile=plane ;;
        email-operations-worker) app_profile=email-operations ;;
        document-worker|document-gateway) app_profile=documents ;;
        discord-attachment-ingress) app_profile=discord-attachments ;;
        *) echo "Unknown application service" >&2; exit 1 ;;
      esac
      docker compose --env-file .env -f deploy/docker/compose.yaml --profile "$app_profile" \
        up -d --no-deps --force-recreate --no-build --wait --wait-timeout 180 "$app_service"
      test -n "$(docker compose --env-file .env -f deploy/docker/compose.yaml --profile "$app_profile" ps -q --status running "$app_service")"
    fi
  done

  worker_readiness_args=(
    --database /opt/jarvis/data/jarvis_v2.db
    --runtime-requirements "$candidate_requirements_container"
    --wait-seconds 180
    --dead-letter-baseline "$dead_letter_baseline"
    --email-dead-letter-baseline "$email_dead_letter_baseline"
  )
  if service_was_running ticket-review && ! runtime_service_required ticket-review; then
    worker_readiness_args+=(--require-worker ticket_review=60)
  fi
  if service_was_running plane-sync && ! runtime_service_required plane-sync; then
    worker_readiness_args+=(--require-worker plane_sync=60)
  fi
  if service_was_running action-approval-worker && ! runtime_service_required action-approval-worker; then
    worker_readiness_args+=(--require-worker action_approval=120)
  fi
  if service_was_running document-worker && ! runtime_service_required document-worker; then
    worker_readiness_args+=(--require-worker documents=120)
  fi
  if service_was_running email-operations-worker && ! runtime_service_required email-operations-worker; then
    worker_readiness_args+=(--require-worker email_operations=120)
  fi
  docker compose --env-file .env -f deploy/docker/compose.yaml run --rm --no-deps --no-build -T \
    jarvis python scripts/check_worker_readiness.py "${worker_readiness_args[@]}"

  if runtime_email_required || service_was_running email-operations-worker; then
    docker compose --env-file .env -f deploy/docker/compose.yaml --profile email-operations \
      run --rm --no-deps --no-build -T email-operations-worker \
      python -m app.workers.email_operations_worker --readiness-only
  fi

  docker compose --env-file .env -f deploy/docker/compose.yaml ps
  docker compose --env-file .env -f deploy/docker/compose.yaml exec ollama ollama ps

  legacy_latency_v1_baseline_p95="<copy the numeric P1 legacy_latency_v1 p95 evidence>"
  test "$legacy_latency_v1_baseline_p95" != "<copy the numeric P1 legacy_latency_v1 p95 evidence>"
  for benchmark_run in 1 2 3; do
    docker compose --env-file .env -f deploy/docker/compose.yaml run --rm --no-deps --no-build -T \
      --volume "$release_export/benchmarks:/opt/jarvis/benchmarks:ro" \
      jarvis python scripts/benchmark_main_models.py \
      --base-url http://accelerator-admission:8040 \
      --model gpt-oss:20b \
      --cases /opt/jarvis/benchmarks/models/main_acceptance_cases.json \
      --min-pass-rate 0.95 --max-p95-seconds 60 \
      --latency-comparison-group legacy_latency_v1 \
      --baseline-p95-seconds "$legacy_latency_v1_baseline_p95" \
      --max-p95-regression-ratio 1.25 \
      --output "/opt/jarvis/data/main-tool-acceptance-run-${benchmark_run}.json"
  done
)
```

Do not guess a backup path or select one by age. The coordinated Documents backup command must use the
exact protected storage root, generated release name, source revision, `deploy/docker/compose.yaml`, and
`.env` exactly as shown above. The P11 checkpoint is incomplete unless the conditional derivation, backup,
verification, exact same-generation schema checks, both reader checks under the one retained rollback
image, isolated restore drill, and pre-backup topology restoration all ran or phase evidence proved
`documents_backup_required=false`. When required, record the exact coordinated generation as the rollback
artifact; never substitute the separately timestamped Core-only backup.

Release acceptance:

- Automated contract, architecture, authorization, approval, idempotency, restart, restricted-data, and
  public-tree tests: 100% pass.
- Held-out mandatory and safety-critical Main cases: 100% pass on each of three runs.
- All held-out cases: at least 95% on each run.
- Zero failed token loops, unauthorized or unapproved effects, duplicate effects, invalid dispatches,
  private-content telemetry leaks, stale approval executions, unexplained Micro requests, or false
  success responses.
- Every case meets its manifest `max_seconds`; overall Main p95 is at most 60 seconds. The unchanged
  `legacy_latency_v1` group p95 is at most 25% slower than its recorded P1 baseline. New multi-step groups
  are judged by their per-case and overall absolute ceilings, never against the six-case legacy ratio.
- All required services healthy, Main reports expected GPU residency, durable workers have current
  heartbeats/no unexplained dead letters, and every canary has the expected receipt/ticket/review state.

Exit gate: record every command/result, image/commit/config snapshot, backup verification, canary receipt,
observation interval, metrics, remaining compatibility debt, and exact rollback boundary. Only then set
P11 and the overall plan to complete.

## Verification strategy and mandatory matrices

Testing proceeds from deterministic contracts outward; model evaluation never substitutes for server
policy tests.

| Layer | Required evidence |
| --- | --- |
| Characterization | Legacy route/domain outcomes and receipts captured before each behavior change. |
| Contract | Closed input/output schema; bounds; unknown fields; serialization; version compatibility. |
| Catalog | Unique ownership; discovery/selection split; safe projection; dynamic revocation. |
| Executor | Argument validation, per-call authorization, idempotency, denial, timeout, bounded observation. |
| Loop | Respond/clarify/call, multi-step observation, repeat/failure/time limits, injection resistance, partial completion. |
| Domain | Owner/policy/provider/storage truth, arbitrary valid combinations, ambiguity, boundary dates, failures. |
| Approval | Exact binding, actor/channel, expiry, duplicate/conflict, every restart point, execute-at-most-once. |
| Ticket/recovery | One receipt per effect, verifier truth, replay, watchdog, remediation, lease/dead-letter recovery. |
| Architecture/security | Forbidden imports/refs/IDs/content, no parallel authorities, public-tree and clean export. |
| Model acceptance | Held-out paraphrases and combinations; mandatory/safety thresholds; latency/loop metrics. |
| Live canary | Protected identity/channel, provider/domain read-back, restart, rollback, observation window. |

Approval/restart tests must cover the Cartesian product of decision `approve | reject | expire`, delivery
`not_sent | sent | duplicate`, and restart point
`before_notification | after_notification | after_decision | during_execution | after_effect_before_completion`.
Impossible states are rejected;
every valid state converges with zero or one effect.

Adversarial cases must include an external observation asking for credentials/another tool, model-supplied
approval/principal fields, unknown/extra/oversized arguments, catalog revocation mid-turn, resource version
change, wrong Discord actor/channel, conflicting idempotency reuse, provider timeout after possible commit,
repeat calls, total-context exhaustion, and restricted Email/Document content offered to memory/events.

## Acceptance traceability

| Desired outcome | Owning phase/tasks | Primary evidence |
| --- | --- | --- |
| Capability-bounded agency, not capability-bounded classification | P2, P3 | Descriptor/executor/loop tests; no new global intent. |
| Main infers goals and selects a few relevant skills | P2-04/05, P3-02 | Discovery-selection tests and held-out model cases. |
| Main flexibly uses allowed operations | P4, P5, P8 | Per-domain semantic contract and canary suites. |
| `last 3 days` and an exact date require no phrase branch | P4-01 through P4-08 | Email interval/DST/held-out cases. |
| Anything in an allowed semantic API surface is composable | P2, P9 | Effective-tool and composition suites. |
| Deterministic safety remains authoritative | P2-06/07, P6, P7 | Authorization, approval, idempotency, receipt tests. |
| Human approvals arrive in the private approval destination | P6-05 through P6-10 | Protected canary and restart matrix; no tracked IDs. |
| Misplaced proactive message is corrected safely | P6-10 | Positive sender attribution and protected route evidence. |
| Core tickets truthfully verify multi-step effects | P7 | Multi-effect recovery, replay, watchdog, verifier tests. |
| Every skill branch has an explicit fate | Operation matrix, P1, P5, P8 | Catalog integrity report and disposition tests. |
| Micro is unnecessary and removed from live semantics | P10A; optional P10B cleanup | Zero Micro calls, route tests, inventory. |
| Main remains fast enough by itself | P3/P10/P11 | Three runs; p95 threshold versus baseline. |
| No new platform subsystem or hidden coupling | All phases | Reuse map, data table, architecture tests, diff allowlists. |

## Migration state machine

Only these transitions are valid:

```text
off + empty domains + empty operations
  -> shadow + empty domains + empty operations
  -> shadow + Lists + exact P5A reads/create/add
  -> active + Lists + exact P5A reads/create/add
  -> shadow + Email + exact P5F reads
  -> active + Email + exact P5F reads
  -> shadow + exact P5F additive-label writes while retaining certified reads
  -> active + exact P5F additive-label writes
  -> shadow + one read domain + that phase's exact read operations
  -> active + the same read domain/operations
  -> repeat reads one operation set at a time
  -> approval/recovery certified
  -> shadow + one new write-operation set while retaining certified reads
  -> active + that write-operation set
  -> composition canary
  -> Main-only with dormant Micro
  -> [explicit review] Micro retirement
```

P5A and P5F are the reviewed accelerated proving exceptions. P5A adds two reversible local writes whose
transaction/idempotency authority ships in the same phase. P5F first certifies Email reads, then adds only
reversible, allowlisted, additive labels against the central Jarvis Gmail mailbox with Email-owned durable
parent/child identity, read-back verification, bounded recovery, and a dedicated worker. Neither exception
authorizes any other write before approval/recovery certification. Skipping any listed P5F state, enabling
multiple other write-operation sets together, or combining a model/provider upgrade with this migration
is prohibited. Each operation has an independent kill switch through
`MAIN_TOOL_ENABLED_OPERATIONS`; `MAIN_TOOL_ENABLED_DOMAINS` is the broader domain kill switch, and the
global kill switch is `MAIN_TOOL_EXECUTION_MODE=off`.

## Global rollback runbook

Rollback immediately on any unauthorized/unapproved/duplicate/destructive effect; content or credential
leak; stale approval execution; corrupted durable state; unexplained Micro call after Main-only cutover;
false success; unbounded loop/result; restart duplication; safety-test failure; or inability to attribute a
protected Discord message.

1. In protected configuration set `MAIN_TOOL_EXECUTION_MODE=off` and clear
   `MAIN_TOOL_ENABLED_OPERATIONS` and `MAIN_TOOL_ENABLED_DOMAINS`. If P10A is being rolled back, also set
   `LEGACY_MICRO_ROUTING_ENABLED=true` while the dormant code exists.
2. Stop new approved-execution claims and recreate only affected Jarvis/worker services using
   the exact P11-04 service commands with `--env-file .env`. Do not delete jobs, reviews,
   tickets, receipts, events, or domain data.
3. Mark pending proposals `expired` through Human Review's normal lifecycle with an operator-rollback
   reason. For every `approved` proposal, prove no claim/effect and atomically set the proposal plus its
   unclaimed execution job `canceled`/terminal-ineligible. Reconcile every `executing` proposal and owning
   reservation to truthful effect/no-effect state before any terminal transition. Never execute proposals
   merely to drain the queue.
4. Verify the legacy route, health, scheduler, private-channel denial, database integrity, worker leases,
   and no-new-effect condition.
5. If flags are insufficient, deploy the retained prior image/configuration known to read the additive
   schema. Restore a database backup only for proven corruption or proven old-reader incompatibility;
   never down-migrate by deleting new columns/rows.
6. Preserve content-free failure evidence and committed-effect receipts. Do not automatically compensate;
   any correction is a new explicitly authorized action.

Database restore is exceptional. Select the store set from phase and deployment evidence before touching a
live path; never infer it from a failed database and never replace a live WAL database.

- `core_only` is permitted only when phase evidence says no Documents store/profile exists and the expected
  Documents schema is `NONE`.
- `coordinated` is mandatory when Documents is enabled, a Documents database at version 14 or 15 exists,
  or P8C has activated. Core, Documents, Paperless export/PostgreSQL, artifacts, and spool are then one
  recovery generation. An independently timestamped Core backup is not eligible, even when its schema is
  readable. This overhaul authorizes verification and an isolated restore drill only; after that succeeds,
  keep writers and ingress disabled and obtain an explicitly approved coordinated production-cutover
  runbook. A Core-only overwrite while Documents remains mounted is prohibited.

Select the Core profile from phase evidence: `7` for P1; `8` for P2-P4 and P5B-E before P5A promotion;
`9` for P5A; `10` for P5F; `11` for P6-P8A; `12` for P8B-P8C; and `13` for P8D onward. P11's final release
profile is `13`. The selected artifact must
have exactly that `user_version`. Once Documents reaches version 15, the retained reader image must be the
exact P8C Documents-bridge image or a later image already proven against both stores.

For `coordinated`, run this non-mutating preflight against one explicitly selected generation. It checks
both standalone SQLite artifacts under the same retained image and drills that exact generation; it never
reads a live WAL-backed main file, writes production state, or changes the live service topology. P11 may
therefore run it after restoring the pre-backup topology. During an actual incident, disable ingress and
claims as containment, but do not stop or restart services merely for this isolated preflight. The later,
separately approved production-cutover runbook must supply its own exact stop/verify/restore/start sequence:

```bash
(
  set -euo pipefail

  coordinated_generation_input="<copy exact verified coordinated generation path>"
  documents_storage_input="<copy exact protected Documents storage root>"
  documents_secrets_input="<copy exact protected Documents secrets root>"
  expected_core_schema_version="<copy 7, 8, 9, 10, 11, 12, or 13 from phase evidence>"
  expected_documents_schema_version="<copy 14 or 15 from phase evidence>"
  retained_all_store_reader_image="<copy exact all-store-compatible retained image ID>"

  test "$coordinated_generation_input" != "<copy exact verified coordinated generation path>"
  test "$documents_storage_input" != "<copy exact protected Documents storage root>"
  test "$documents_secrets_input" != "<copy exact protected Documents secrets root>"
  test "$expected_core_schema_version" != "<copy 7, 8, 9, 10, 11, 12, or 13 from phase evidence>"
  test "$expected_documents_schema_version" != "<copy 14 or 15 from phase evidence>"
  test "$retained_all_store_reader_image" != "<copy exact all-store-compatible retained image ID>"
  case "$expected_core_schema_version" in 7|8|9|10|11|12|13) ;; *) exit 1 ;; esac
  case "$expected_documents_schema_version" in 14|15) ;; *) exit 1 ;; esac

  documents_storage_root="$(readlink -f -- "$documents_storage_input")"
  documents_secrets_root="$(readlink -f -- "$documents_secrets_input")"
  coordinated_generation="$(readlink -f -- "$coordinated_generation_input")"
  test -d "$documents_storage_root"
  test -d "$documents_secrets_root"
  test -d "$coordinated_generation"
  test ! -L "$coordinated_generation_input"
  case "$coordinated_generation" in "$documents_storage_root"/*) ;; *) exit 1 ;; esac

  coordinated_core="$coordinated_generation/core.db"
  coordinated_documents="$coordinated_generation/documents.db"
  test -f "$coordinated_core"
  test ! -L "$coordinated_core"
  test -f "$coordinated_documents"
  test ! -L "$coordinated_documents"
  python scripts/manage_document_backup.py verify "$coordinated_generation"
  python scripts/manage_database.py verify --source "$coordinated_core"

  coordinated_versions="$(python - "$coordinated_core" "$coordinated_documents" <<'PY'
import sqlite3
import sys
from pathlib import Path

versions = []
for value in sys.argv[1:]:
    path = Path(value).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        versions.append(str(int(connection.execute("PRAGMA user_version").fetchone()[0])))
    finally:
        connection.close()
print(" ".join(versions))
PY
  )"
  test "${coordinated_versions%% *}" = "$expected_core_schema_version"
  test "${coordinated_versions#* }" = "$expected_documents_schema_version"

  docker image inspect "$retained_all_store_reader_image" >/dev/null
  docker run --rm --network none --read-only \
    --env PYTHONDONTWRITEBYTECODE=1 \
    --tmpfs /tmp:rw,noexec,nosuid,size=16m \
    --mount "type=bind,src=$coordinated_core,dst=/verify/core.db,readonly" \
    "$retained_all_store_reader_image" \
    python scripts/manage_database.py reader-check --source /verify/core.db
  docker run --rm --network none --read-only \
    --env PYTHONDONTWRITEBYTECODE=1 \
    --tmpfs /tmp:rw,noexec,nosuid,size=16m \
    --mount "type=bind,src=$coordinated_documents,dst=/verify/documents.db,readonly" \
    "$retained_all_store_reader_image" \
    python scripts/manage_document_backup.py reader-check --source /verify/documents.db

  coordinated_drill_name="reasoning-led-recovery-$(date -u +%Y%m%dT%H%M%SZ)"
  python scripts/drill_document_restore.py "$coordinated_generation" \
    --storage-root "$documents_storage_root" \
    --drill-name "$coordinated_drill_name" \
    --secrets-root "$documents_secrets_root"
  echo "Coordinated preflight passed; live multi-store restore still requires explicit approval." >&2
)
```

For `core_only`, use this order:

1. Disable Discord ingress, scheduler claims, approval execution, ticket workers, Compose-owned Email
   work, and every external timer/unit that can start any remaining legacy application writer.
2. Stop `jarvis`, `ticket-review`, `plane-sync`, `action-approval-worker`, `email-operations-worker`,
   `document-worker`, `document-gateway`, and `discord-attachment-ingress` with
   `docker compose --env-file .env -f deploy/docker/compose.yaml stop jarvis ticket-review plane-sync action-approval-worker email-operations-worker document-worker document-gateway discord-attachment-ingress`.
   Confirm none is running and that no host process has the Core DB, WAL, or SHM open for writing. If
   writer ownership is uncertain, stop without restoring.
3. Resolve the absolute Core DB and data-root paths and prove the DB is inside that exact data root. Create
   a new mode-0700 timestamped directory under `data/rollback-evidence/`. Verify the explicitly selected
   backup before moving any live file.
4. On the same filesystem, move the quiesced live `jarvis_v2.db` and each existing
   `jarvis_v2.db-wal`/`jarvis_v2.db-shm` into that evidence directory. Confirm none of the three live paths
   remains. If any move/check fails, stop without restoring or starting a writer.
5. Restore into the clean live path, then run integrity, exact schema/compatibility, and retained-reader
   checks before starting a writer.
6. Retag the selected phase's retained image, restore that phase's protected configuration snapshot, and
   start only the consumers required by restored flags and unfinished durable work. Keep ingress/claims
   disabled if the retained requirements checker cannot derive a closed set. Run health, legacy-route,
   scheduler-denial, identity/channel-denial, and no-new-effect smoke tests before re-enabling them.

Exact Core-only sidecar-safe procedure, after step 2 has proven every writer stopped:

```bash
(
  set -euo pipefail

  core_data_root="$(readlink -f -- ./data)"
  core_db_input="./data/jarvis_v2.db"
  backup_input="<set the exact already-verified backup path>"
  expected_core_schema_version="<copy 7, 8, 9, 10, 11, 12, or 13 from phase evidence>"
  expected_documents_schema_version="<copy NONE from phase evidence>"
  retained_reader_image="<copy exact Core-compatible retained image ID>"

  test -d "$core_data_root"
  test -f "$core_db_input"
  test ! -L "$core_db_input"
  core_db_path="$(readlink -f -- "$core_db_input")"
  test "$backup_input" != "<set the exact already-verified backup path>"
  test "$expected_core_schema_version" != "<copy 7, 8, 9, 10, 11, 12, or 13 from phase evidence>"
  case "$expected_core_schema_version" in 7|8|9|10|11|12|13) ;; *) exit 1 ;; esac
  test "$expected_documents_schema_version" = NONE
  test "$retained_reader_image" != "<copy exact Core-compatible retained image ID>"
  docker image inspect "$retained_reader_image" >/dev/null
  backup_to_restore="$(readlink -f -- "$backup_input")"
  test -f "$backup_to_restore"
  test -f "$core_db_path"
  case "$core_db_path" in
    "$core_data_root"/*) ;;
    *) echo "Core DB resolved outside the intended data root" >&2; exit 1 ;;
  esac
  test "$backup_to_restore" != "$core_db_path"
  test "$backup_to_restore" != "${core_db_path}-wal"
  test "$backup_to_restore" != "${core_db_path}-shm"
  test ! "$backup_to_restore" -ef "$core_db_path"
  if [ -e "${core_db_path}-wal" ] || [ -L "${core_db_path}-wal" ]; then
    if [ -e "${core_db_path}-wal" ]; then test ! "$backup_to_restore" -ef "${core_db_path}-wal"; fi
  fi
  if [ -e "${core_db_path}-shm" ] || [ -L "${core_db_path}-shm" ]; then
    if [ -e "${core_db_path}-shm" ]; then test ! "$backup_to_restore" -ef "${core_db_path}-shm"; fi
  fi

  check_core_schema() {
    python - "$1" "$expected_core_schema_version" <<'PY'
import sqlite3
import sys
from pathlib import Path

database_path = Path(sys.argv[1]).resolve()
expected_version = int(sys.argv[2])
required_tables = {
    "durable_jobs",
    "events",
    "memory_entries",
    "sessions",
    "skills",
    "worker_heartbeats",
}
required_columns = {}
if expected_version >= 8:
    required_tables.add("schema_reader_compatibility")
    required_columns["skills"] = {"main_tools_json", "main_tools_contract_version"}
if expected_version >= 9:
    required_tables.add("list_operations")
if expected_version >= 10:
    required_tables.update({
        "email_managed_labels", "email_message_managed_labels",
        "email_tool_operations", "email_managed_label_operations",
    })
    required_columns["email_tool_operations"] = {
        "operation_id", "tool_id", "contract_version", "owner_user_id",
        "discord_channel_id", "arguments_hash", "effect_cardinality",
        "expected_child_count", "recovery_manifest_json", "recovery_manifest_hash",
        "status", "result_json", "error_code",
    }
    required_columns["email_managed_label_operations"] = {
        "child_operation_id", "parent_operation_id", "child_index",
        "gmail_message_id", "action", "managed_label_refs_json",
        "arguments_hash", "idempotency_key", "status", "lease_fencing_token",
    }
if expected_version >= 11:
    required_tables.add("action_proposals")
    required_columns["action_proposals"] = {
        "batch_manifest_json", "batch_manifest_hash",
        "transfer_manifest_json", "transfer_binding_hash",
    }
if expected_version >= 12:
    required_tables.add("switch_actions_log")
    required_columns["switch_actions_log"] = {"operation_id", "arguments_hash"}
if expected_version >= 13:
    required_tables.update({"email_label_operations", "email_mailbox_operations"})
    required_columns["email_tool_operations"].update({
        "idempotency_key", "operation_identity_hash", "parent_manifest_hash",
    })
    required_columns["email_managed_label_operations"].add("parent_manifest_hash")
    grouped_columns = {
        "parent_operation_id", "parent_manifest_hash", "child_index", "arguments_hash",
    }
    required_columns["email_mailbox_operations"] = grouped_columns
connection = sqlite3.connect(database_path.as_uri() + "?mode=ro&immutable=1", uri=True)
try:
    actual_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    actual_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    actual_columns = {
        table: {
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        }
        for table in required_columns
        if table in actual_tables
    }
    compatibility = {}
    if "schema_reader_compatibility" in actual_tables:
        compatibility = {
            int(row[0]): (int(row[1]), str(row[2]))
            for row in connection.execute(
                "SELECT schema_version, minimum_reader_version, change_class "
                "FROM schema_reader_compatibility"
            ).fetchall()
        }
finally:
    connection.close()
missing_tables = sorted(required_tables - actual_tables)
missing_columns = {
    table: sorted(columns - actual_columns.get(table, set()))
    for table, columns in required_columns.items()
    if columns - actual_columns.get(table, set())
}
bad_versions = [
    version
    for version in range(8, expected_version + 1)
    if version not in compatibility
    or compatibility[version][0] > 7
    or compatibility[version][1] != "additive"
]
if actual_version != expected_version or missing_tables or missing_columns or bad_versions:
    raise SystemExit(
        f"Core schema preflight failed: version={actual_version}, "
        f"missing_tables={missing_tables}, missing_columns={missing_columns}, "
        f"bad_compatibility_versions={bad_versions}"
    )
PY
  }

  retained_reader_check() {
    docker run --rm --network none --read-only \
      --env PYTHONDONTWRITEBYTECODE=1 \
      --tmpfs /tmp:rw,noexec,nosuid,size=16m \
      --mount "type=bind,src=$1,dst=/verify/jarvis_v2.db,readonly" \
      "$retained_reader_image" \
      python scripts/manage_database.py reader-check --source /verify/jarvis_v2.db
  }

  python scripts/manage_database.py verify --source "$backup_to_restore"
  check_core_schema "$backup_to_restore"
  retained_reader_check "$backup_to_restore"

  rollback_evidence_root="$core_data_root/rollback-evidence"
  if [ -e "$rollback_evidence_root" ] || [ -L "$rollback_evidence_root" ]; then
    test ! -L "$rollback_evidence_root"
    test -d "$rollback_evidence_root"
    test "$(readlink -f -- "$rollback_evidence_root")" = "$rollback_evidence_root"
  else
    mkdir -m 0700 -- "$rollback_evidence_root"
  fi
  rollback_evidence_dir="$rollback_evidence_root/core-$(date -u +%Y%m%dT%H%M%SZ)"
  test ! -e "$rollback_evidence_dir"
  test ! -L "$rollback_evidence_dir"
  mkdir -m 0700 -- "$rollback_evidence_dir"

  for live_component in "$core_db_path" "${core_db_path}-wal" "${core_db_path}-shm"; do
    if [ -e "$live_component" ] || [ -L "$live_component" ]; then
      mv -- "$live_component" "$rollback_evidence_dir/"
    fi
  done
  test -f "$rollback_evidence_dir/$(basename "$core_db_path")"
  test ! -e "$core_db_path"
  test ! -L "$core_db_path"
  test ! -e "${core_db_path}-wal"
  test ! -L "${core_db_path}-wal"
  test ! -e "${core_db_path}-shm"
  test ! -L "${core_db_path}-shm"

  python scripts/manage_database.py --database "$core_db_path" restore "$backup_to_restore" --replace
  test -f "$core_db_path"
  test ! -L "$core_db_path"
  python scripts/manage_database.py verify --source "$core_db_path"
  check_core_schema "$core_db_path"
  retained_reader_check "$core_db_path"
)
```

## Deferred discoveries

| ID | Finding | Why excluded from this plan | Later plan trigger |
| --- | --- | --- | --- |
| D-01 | Structured fact memory could be useful. | Current Memory authority stores interaction history; fact CRUD needs separate product/privacy design, not a second store. | Explicit memory requirements, ownership, retention, approval, and deletion plan. |
| D-02 | Email send/reply/forward/delete could broaden agency. | Outbound/destructive mailbox operations need content, recipient, draft, approval, and provider-policy design. | Separate approved Email actions plan. |
| D-03 | Physical Home adapters and group control. | Current truth is simulated; provider/device policy and physical verification do not exist. | Named provider, per-device authorization, safety, and truth model. |
| D-04 | Email promotion to task/Wave and a general task tool. | No approved shared task/Wave authority is available in this scope. | Authority/reuse ADR and separate cross-domain plan. |
| D-05 | Contact extraction/promotion. | Contact authority is intentionally deferred. | Resolve the existing contact-authority ADR. |
| D-06 | General parallel autonomous instances. | This overhaul is a bounded turn loop, not the deferred instance-control roadmap. | Separate runtime/lease/cancellation plan. |
| D-07 | Dashboard/editor for tool policy. | Not needed to prove the runtime boundary and would expand UI/security scope. | Operator workflow demonstrates a concrete need. |
| D-08 | Drop legacy intent/Micro database fields and rewrite history. | Additive compatibility is safer during this migration. | Separate retention/migration approval after rollback images expire. |
| D-09 | Open arbitrary provider APIs to Main. | Semantic typed operations are the approved least-authority boundary; raw SDKs/endpoints expose credentials and accidental authority. | New provider operation added through its domain contract and policy review. |

## Evidence ledger

Implementation agents update only the applicable row and phase checkboxes; architecture changes require a
reviewed plan revision.

| Phase | Commit/diff | Commands/results | Canary/observation | Rollback proof | Remaining debt | Status |
| --- | --- | --- | --- | --- | --- | --- |
| P0A | baseline commit and allowed one-file diff recorded | baseline export: one named failure; verified export/checker: pass; diff check: pass | n/a | four-substitution documentation revert | none | complete |
| P1 | baseline `900f3ba`; 23 phase-attributable source/test/generated files recorded; promoted image `e3cfd0879cbf` | baseline 658 tests and 6/6 Main; final 689 tests, focused 98, compile/Compose/Ruff-owned-files/diff/export pass; retained Core v7/v8 and Documents v14 reader gates pass | live healthy; post-promotion legacy cohort 6/6, p50 3.0457s, p95 5.529s | exact prior image `0cc178ebc3da` retained; P1 reader image retained; verified Core backup | pre-existing full-tree Ruff debt | complete |
| P2 | 16 phase-attributable source/test files; promoted image `55edbb37aa5f` | focused 108; local full 728 passed/2 skipped; exact authoritative focused/full 108/730; compile/Ruff/diff/export pass | live healthy; Core v8; zero tool rows; legacy cohort 6/6, p50 2.5338s, p95 5.106s | retained P1 reads exact v8 backup; pre-bridge refuses; pre/post backups verified | production descriptors intentionally deferred to proving phases; P3 not authorized | complete |
| P3 | bounded loop, typed contracts, persistence policy, shadow seam, 1024-token repair scope increase; live image `e4ce3313d2ce`; retained tag `retained-p3-bounded-loop-20260831T004643Z` | three consecutive P3 model passes at 10/10; implementation and regression suites passed | live mode shadow, empty allowlists, legacy Micro preserved; interim audit found zero qualifying shadow turns/effects | exact retained P3 tag; mode `off` remains code rollback | representative Discord samples plus their 24-hour audit are still required; wall time alone is insufficient | implementation_complete_shadow_gate_in_progress |
| P4 | typed Email query/executor, five compatibility read descriptors, generic handler injection, and held-out argument scoring; exact sanitized clean stage | local 783/2; exact Ubuntu focused 97 and full 785; public export pass; preliminary Main 20/20 with P4 10/10 and zero failed loops | no P4 activation; its read gate is absorbed and expanded by P5F E1 | discard stage; retained P3 live image unchanged | implementation retained; obsolete five-ID activation superseded by the exact P5F read set | implementation_verified_activation_superseded |
| P5A | four typed Lists operations, migration 009 operation ledger, accelerator typed-step route, active Main routing, closed selector/provenance contracts, and development-headroom configuration; live image `a57688bd42e0` | focused 46; local full 809 passed/2 skipped; Ubuntu full 811 passed; Ruff, Compose parse, and clean public export passed | three accepted model runs; copied-production operation/operator/full-route canaries passed; live disposable create/add/respond committed exactly two effects and state matched | exact operation/domain kill switches plus global mode `off`; retained pre-headroom image `b05741dc`; verified DB/config/source backups; committed data retained | P7 ticket attachment and destructive/update/remove tools remain deferred; Email remains inactive; dormant Micro rollback retained | complete |
| P5F | focused central-mailbox Email plan approved; implementation pending | current narrow Email characterization suite 42 passed; P5F gates pending | live read/write activation and canary pending | exact Email operation/domain kill switches, worker disable, retained rows/state, and no legacy worker start are planned | P3/P4 observation debt retained; original source accounts and later mailbox actions explicitly deferred | ready_for_execution |
| P5B | pending | pending | pending | pending | pending | not_started |
| P5C | pending | pending | pending | pending | pending | not_started |
| P5D | pending | pending | pending | pending | pending | not_started |
| P5E | pending | pending | pending | pending | pending | not_started |
| P6 | pending | pending | pending | pending | pending | not_started |
| P7 | pending | pending | n/a | pending | pending | not_started |
| P8A | pending | pending | pending | pending | pending | not_started |
| P8B | pending | pending | pending | pending | pending | not_started |
| P8C | pending | pending | pending | pending | pending | not_started |
| P8D | pending | pending | pending | pending | pending | not_started |
| P8E | pending | pending | pending | pending | pending | not_started |
| P9 | pending | pending | pending | pending | pending | not_started |
| P10A | pending | pending | pending | pending | pending | not_started |
| P10B | pending | pending | pending | pending | pending | review_required |
| P11 | pending | pending | pending | pending | pending | not_started |

## Definition of complete

This plan is complete only when all authorized phases through P11 meet their gates and:

- Main is the only live semantic reasoning plane.
- A valid new phrasing, date interval, filter combination, or allowed tool composition does not require a
  new central intent, regex, router branch, or workflow handler branch.
- Every effective tool is selected from a small request-scoped catalog, schema-validated, reauthorized,
  policy-checked, bounded, receipted, and truthfully reported.
- Required approvals are exact, durable, private, restart-safe, and execute at most once.
- Action tickets verify effects after authorization and never impersonate approval.
- All skill declarations have a tested interactive/non-interactive disposition.
- All stores/effects retain one authoritative owner; no duplicate platform subsystem was introduced.
- Authoritative Ubuntu clean-export tests, three Main benchmarks, canaries, observation, backup, and rollback evidence
  pass at the stated thresholds.
- Literal protected Discord IDs, credentials, private host details, raw restricted content, and hidden
  reasoning are absent from the tracked tree and telemetry.
