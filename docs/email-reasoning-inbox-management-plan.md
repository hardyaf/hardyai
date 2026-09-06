# Email Reasoning and Inbox Management Plan

Status: `mailbox_state_discord_canary_pending`; E0-E4 deployed, E5 deterministic/provider gates complete, corrected Discord end-to-end canary pending

Prepared: 2026-08-31

Master phase: `P5F` in `docs/reasoning-led-capability-execution-plan.md`

## Objective

Make the central Jarvis Gmail mailbox an ordinary reasoning-led Email capability rather than a fixed
display workflow. From an authorized private Discord channel, Main must be able to:

1. discover the routed inbox views and Jarvis-managed labels currently available;
2. query any indexed date or interval with any supported combination of mailbox, sender, label, review,
   text, attachment, ordering, and pagination filters;
3. use query observations to summarize or discuss the selected messages without a date-, item-count-,
   punctuation-, sender-, or mailbox-specific code branch;
4. add or remove one or more allowlisted Jarvis-managed Gmail labels from one or more selected messages;
5. set selected messages read or unread, archive them, or restore them to the Inbox through separately
   authorized reversible tools that Main can compose with managed-label operations;
6. observe missing or ambiguous mailboxes, labels, senders, references, and pages, then choose another
   authorized Email tool, clarify, or stop truthfully within the existing bounded Main loop; and
7. report queued, verified, failed, truncated, stale, and partial states truthfully.

The central Jarvis Gmail account is the only provider mutation target. Configured source accounts and
forwarding destinations are routed views for query and display; Jarvis does not authenticate to or mutate
the original source inboxes.

## Locked scope

### Included

- Central Jarvis Gmail projection and its configured routed inbox views.
- Historical projection backfill through the existing read-only Gmail adapter and durable-job ledger.
- Runtime discovery of authorized mailbox and managed-label catalogs.
- Arbitrary concrete intervals and an all-indexed-history query when no interval was requested.
- Exact sender addresses, sender domains, bounded sender-name/organization text, recipients, content
  text, attachments, routed mailboxes, labels, local review visibility, ordering, and pagination.
- Additive application and removal of Jarvis-managed labels. Initial protected label policy will include
  `Done`, `To-do`, `Bills`, and `AYSO`; later policy additions require configuration, not code.
- Reversible central-mailbox system-label transitions: `read|unread`, archive by removing `INBOX`, and
  restore by adding `INBOX`. Main composes these primitives with managed-label operations; there is no
  folder-, label-, item-count-, or phrase-specific move workflow.
- Provider read-before/write/read-after verification, idempotent replay, bounded leases/retries, dead
  letters, operation status, and worker health.
- Existing private Discord user/channel/agent authorization on every discovery, read, resume, and write.

### Deferred or prohibited

- Sending, drafting, replying, forwarding, deleting, trashing, link browsing, or Gmail settings
  administration.
- Modifying any original source inbox or adding another Gmail account/token.
- Arbitrary Gmail labels. Main may touch only the durable Jarvis-managed label catalog, plus the fixed
  `INBOX` and `UNREAD` system labels through their dedicated tools; it never receives a raw system-label
  parameter and cannot touch another system or unrelated user label.
- Creating or deleting managed-label definitions from Discord. The first slice can create the configured
  provider label lazily when it is first applied, but it cannot expand the managed catalog.
- Spam moves, local review-state mutation, and snooze remain in the later P8D phase; spam still requires
  formal approval. Restore-to-Inbox refuses Spam or Trash rather than changing those states implicitly.
- Cross-domain promotion to Lists, Calendar, Tasks, Wave, or another skill. P9 remains the cross-domain
  composition phase.
- Proactive completion messages. The requested Discord turn receives the queued operation reference;
  `email.get_operation` provides later status until the shared notification infrastructure is available.

## Audited starting point

- Email synchronization and the legacy Gmail modify flags are enabled, but active Main currently exposes
  only the four P5A Lists operations. No Email tool is live on the reasoning path.
- Five typed P4 reads exist. The date/interval and storage boundaries are sound, but the live mailbox and
  label catalogs are absent from production model context.
- Exact source/category validation occurs during canonicalization. An invalid model guess is returned as
  a denial, so Main cannot observe candidates and replan.
- Sender arrays accept exact addresses only. Broad text can incidentally match a sender name, but the
  contract does not express name or domain intent precisely.
- Queries default to ten active messages, have no cursor, and may omit handled messages or truncate a date
  without a way to request the next page.
- The local projection begins at activation because historical backfill is disabled.
- Legacy classification is exclusive: one logical category is stored per message. The existing Gmail
  category writer removes other Jarvis-managed category labels, so it is not an additive tagging system.
- Automatic category-to-Gmail reconciliation has created a large legacy queued-label backlog. The
  authoritative runtime has no active Email operation worker. Exact counts and protected identifiers stay
  in release evidence, not this tracked plan.
- Focused Email query, service, configuration, worker, and Gmail writer tests pass. They characterize the
  narrower existing design; they do not prove the target inbox-management flow.

## Architectural invariants

1. Main reasons over semantic Email tools; routers, handlers, adapters, and storage contain no phrase,
   punctuation, sender-name, label-name, item-count, or date-expression branches.
2. Tool discovery and exact operation/domain allowlists determine availability. Legacy Email intents do
   not execute when active Main cannot resolve a typed Email operation.
3. The Email domain resolves model-visible mailbox, label, message, cursor, and operation references to
   provider/local identities. Raw Gmail IDs, destination addresses, tokens, and provider clients never
   enter model contracts.
4. Missing or ambiguous user-level selectors are normal observations. They return bounded candidates and
   `needs_input`; they are not policy denials unless current authorization actually failed.
5. Email content remains untrusted private data under `no_store`. It cannot add instructions, tools,
   labels, recipients, authority, or cross-domain actions.
6. Classification categories and user-managed labels are separate. A classifier may propose exactly one
   local category; a message may have zero or many managed labels. Classification never queues a Gmail
   label write.
7. Applying label A never removes label B. Removing a label removes only the exact requested enabled
   Jarvis-managed label and cannot touch `INBOX`, `UNREAD`, `SPAM`, or any unmanaged label.
   Dedicated mailbox-state tools may change only their fixed target: read state changes `UNREAD`, while
   archive/restore changes `INBOX`; neither accepts a model-supplied Gmail label ID.
8. Every accepted effectful root call has one stable parent operation ID. Each message is one independently
   recoverable child effect; all labels requested for that message are one provider `modify` call.
9. Queued is not committed. Only provider read-back creates a verified child receipt. A parent with mixed
   outcomes is partial; retries never repeat a verified child.
10. Legacy label rows are permanently ineligible for the new worker. Deployment cannot turn the existing
    backlog into provider effects merely by starting the worker.
11. The worker is a tracked Compose service with bounded polling, claims, leases, fencing, retry/backoff,
    dead letters, heartbeat, graceful stop, and startup lease recovery. There is no untracked systemd
    timer or manually maintained daemon.
12. No tracked file contains real Gmail addresses, Discord IDs, OAuth paths, tokens, provider label IDs,
    or the protected production catalog.

## Runtime tool catalog

| Tool | Effect | Input summary | Result summary | Approval |
| --- | --- | --- | --- | --- |
| `email.list_mailboxes` | read | empty | authorized opaque mailbox refs, display names, bounded coverage/counts | none |
| `email.list_labels` | read | optional bounded text filter | enabled opaque label refs and display names | none |
| `email.query_messages` v2 | read | optional interval plus closed mailbox/sender/label/review/text/attachment/order/page filters | bounded message refs, normalized query, result-set ref, next cursor, coverage and truncation | none |
| `email.get_message` | read | current message ref | bounded projected message/summary and managed labels | none |
| `email.get_thread` | read | current message ref, optional limit/cursor | bounded thread messages and next cursor | none |
| `email.status` v2 | read | empty | content-free sync, backfill, worker, queue, dead-letter, and coverage state | none |
| `email.get_operation` | read | opaque operation ref | content-free parent/child counts and terminal/partial state | none |
| `email.apply_labels` | external write, independent batch | 1..50 current message refs and 1..10 enabled label refs | queued operation/child refs, later verified receipts | none |
| `email.remove_labels` | external write, independent batch | 1..50 current message refs and 1..10 enabled label refs | queued operation/child refs, later verified receipts | none |
| `email.set_read_state` | external write, independent batch | 1..50 current message refs and `state=read|unread` | queued operation/child refs, later verified receipts | none |
| `email.archive_messages` | external write, independent batch | 1..50 current message refs | queued operation/child refs, later verified receipts | none |
| `email.restore_to_inbox` | external write, independent batch | 1..50 current message refs | queued operation/child refs, later verified receipts | none |

`email.summarize` is not required for a collection request. Main uses `email.query_messages` observations
and its normal response step to produce a summary at the requested focus. The compatibility descriptor may
remain stored, but it is not activated until its current unused `focus` behavior is either removed or made
truthful. `email.discuss` remains outer Main reasoning rather than a formatter tool.

### Query schema decisions

- `start` and exclusive `end` are optional as a pair. One without the other is invalid. When both are
  absent, the query means all currently indexed history, bounded by page size and cursor.
- Natural dates are normalized by Main using the server-supplied clock/timezone context. Domain code
  validates aware concrete instants only.
- `mailbox_refs` is an optional unique array of 1..10 refs from `email.list_mailboxes`; omission means all
  authorized routed views. Multiple mailboxes do not require multiple central intents.
- Sender fields are distinct: `sender_addresses` is 1..10 exact addresses, `sender_domains` is 1..10 exact
  domains, and `sender_text` is one bounded case-insensitive sender-name/organization query. General
  `text` remains a subject/sender/snippet search rather than pretending to be exact sender identity.
- `recipient_addresses` is an optional unique array of 1..10 exact addresses. It filters the central
  projection only and never grants access to or mutation authority over a source account.
- `label_refs` is an optional unique array of 1..10 managed-label refs with `label_match=any|all`.
  Classification filtering stays a separately named compatibility field and cannot masquerade as tags.
- `visibility=all|active|unseen|needs_reply|completed|spam`; omission means `all`. Asking for a date or
  sender must not silently hide handled messages.
- `order=oldest|newest`, `limit=1..50`, default 20. A result larger than one page returns a request-bound,
  user/channel-bound, expiring opaque cursor. A cursor call cannot add or change filters.
- Query observations disclose indexed coverage and freshness. A requested interval outside indexed
  coverage returns `coverage_incomplete`, not a false empty-inbox claim.

### Selector and replan contract

- `list_mailboxes` and `list_labels` are ordinary tools, not static prompt text. Their values are current,
  authorized, content-free domain observations.
- Human-friendly mailbox or label text may be supplied only to a read resolver. Exact unique matches
  canonicalize to opaque refs; zero or multiple matches return `needs_input` with bounded safe candidates.
- Effect tools accept only canonical refs. Main may transfer `/messages/*/message_ref`,
  `/mailboxes/*/mailbox_ref`, and `/labels/*/label_ref` within `skill.email.agent` through the existing
  same-domain observation provenance boundary.
- Invalid schema, stale reference, wrong channel/user/agent, disabled label, or changed authorization
  fails before operation identity. A stale-but-recognizable selector returns a safe typed observation;
  actual authorization failure remains a denial.
- Main may therefore execute `list_labels -> query_messages -> apply_labels -> set_read_state ->
  archive_messages -> respond` without a compound intent or workflow-specific planner. A request to
  "move these to Bills and mark them read" is ordinary composition, not a bespoke move-to-Bills intent.

## Reuse map

| Concern | Decision | Existing authority and adaptation |
| --- | --- | --- |
| Skill discovery and execution | reuse | `SkillRegistryService`, Markdown descriptors, generic selection, loop, dispatcher, and exact allowlists |
| Authorization | reuse | `EmailAgentPermissions` and current immutable Discord user/channel/agent grant; recheck every call and resume |
| Message projection | reuse/adapt | `EmailAgentSQLiteStorage`, `email_messages`, summaries, classifications, user state, and reference sets |
| Date reasoning | reuse | Main temporal context plus immutable `EmailQuery`; expand schema/default/pagination without phrase parsing |
| Source routes | adapt | Existing protected `source_routes`; add safe opaque runtime mailbox projection, never expose addresses |
| Managed labels | new bounded authority | `email_managed_labels` is necessary because exclusive classifier categories cannot represent additive tags |
| Message-label projection | new bounded projection | `email_message_managed_labels` mirrors verified Gmail state for only the managed catalog |
| Parent operation identity | adapt | Add Email-owned `email_tool_operations` in Core SQLite, matching the P5A domain-ledger pattern |
| Child provider operations | adapt | Reuse `email_managed_label_operations` as the one parent-bound provider-label child ledger; migration 011 broadens only the parent tool check. Its legacy name is compatibility debt, not a second queue. |
| Provider adapter | adapt | Extend the existing isolated Gmail writer with fixed `INBOX`/`UNREAD` transitions and read-back; raw provider labels remain unavailable to tools |
| Background worker | adapt | Reuse the Compose-owned `email-operations-worker`, its claims, fencing, rate caps, retry/dead-letter behavior, heartbeat, and recovery; no second worker or queue |
| Historical work | reuse | Existing durable job ledger with `email.projection_backfill.v1`; no Email-private backfill queue |
| Events | reuse | `EventLogService`, content-free IDs/counts/status only |
| Tickets/approvals | deferred | Email domain receipts protect this reversible slice; P7/P8D later attach shared tickets and formal spam approval |

The two `new bounded authority` decisions do not create a second Email system. Existing categories are
exclusive classifier decisions, and existing label-operation rows encode exactly one category label with
no durable parent or additive label set. They cannot safely own many-label state or recover an independent
batch without semantic overloading and a destructive table rewrite.

## Data ownership and projection map

| Datum/effect | Authoritative owner | Projection/cache | Consistency and deletion behavior |
| --- | --- | --- | --- |
| Central message existence/content and Gmail label state | central Gmail | bounded `email_messages` metadata/summary projection | history sync/backfill flows Gmail -> SQLite; Gmail content is never generic memory |
| Routed mailbox definition | protected Email permissions | ephemeral `mailbox_ref` catalog | config -> runtime only; removal disables query selection without deleting messages |
| Classifier category | `email_classifications` | query/display field | exactly one local proposal/correction; never creates provider label work |
| Jarvis-managed label authorization | `email_managed_labels` | protected config seeds and model-safe catalog | explicit operator sync; removed config disables new writes, never deletes Gmail labels automatically |
| Per-message managed-label presence | Gmail verified state | `email_message_managed_labels` | worker/sync read-back flows Gmail -> SQLite; stale state is disclosed, not assumed |
| Query reference/cursor | existing Email reference-set authority | opaque model ref | user/channel/query-bound expiry; expiry deletes only the pointer, never mail |
| Parent call identity/recovery | `email_tool_operations` | content-free operation status | retained through audit policy; private recovery manifest clears after all children reconcile |
| Per-message provider effect | `email_managed_label_operations` plus Gmail read-back | receipt/event projection | one child per message; verified rows never rerun; rows are retained, not silently deleted |
| Read/unread and Inbox membership | central Gmail system-label state | `email_messages.gmail_label_ids_json` | the same isolated worker performs one fixed transition and commits the local projection only after exact read-back; sync may later refresh Gmail -> SQLite |
| Historical backfill work | existing durable job ledger | Email sync-run counts/status | bounded claims/retry/dead letter; deleting a job never deletes projected mail |

## Core migration 010

P5F reserves ordered Core migration `010`; later planned Core migrations advance by one. Migration 010 is
additive with minimum compatible reader 7 and must be present in both fresh schema creation and upgrades
from populated version 9.

Add:

1. `email_managed_labels(label_ref PRIMARY KEY, policy_key UNIQUE, display_name, gmail_label_name UNIQUE,
   provider_label_id, enabled, origin, created_at, updated_at)`; `origin` is initially `protected_config`.
2. `email_message_managed_labels(gmail_message_id, label_ref, present, provider_label_id,
   last_verified_at, PRIMARY KEY(gmail_message_id,label_ref))` with foreign keys to projected messages and
   managed labels.
3. `email_tool_operations(operation_id PRIMARY KEY, tool_id, contract_version, owner_user_id,
   discord_channel_id, arguments_hash, effect_cardinality, expected_child_count,
   recovery_manifest_json, recovery_manifest_hash, status, result_json, error_code, created_at,
   completed_at)`. Closed states are `reserved|queued|completed|partial|failed|cancelled`.
4. `email_managed_label_operations(child_operation_id PRIMARY KEY, parent_operation_id, child_index,
   gmail_message_id, action, managed_label_refs_json, arguments_hash, idempotency_key UNIQUE, status,
   attempt_count, max_attempts, lease_owner, lease_expires_at, lease_fencing_token, next_attempt_at,
   provider_labels_before_json, provider_labels_after_json, last_error_code, created_at, updated_at,
   completed_at)` with `action=apply|remove`, closed child states
   `queued|claimed|verified|dead_letter|cancelled`, and unique parent/index and parent/message indexes.

The parent private recovery manifest contains only canonical message IDs/refs, label refs, child indexes,
and hashes required to reconstruct the complete child set. It never enters generic history, tickets,
events, approval records, or model observations. The migration does not rewrite, claim, cancel, or delete
legacy `email_label_operations`; those rows are history and are ineligible for the new worker by table
identity.

## Core migration 011 - reversible mailbox-state expansion

The user authorized this pull-forward on 2026-09-01. Migration `011` preserves every migration-010 row
and broadens only `email_tool_operations.tool_id` to admit `email.set_read_state`,
`email.archive_messages`, and `email.restore_to_inbox`. The existing child table already stores one
`apply|remove` label-state transition per message, so no new queue, worker, parent ledger, durable datum,
or credential surface is introduced. Internal fixed sentinels distinguish `INBOX` and `UNREAD` from
managed-label refs; they are never accepted from model input or resolved through the managed catalog.

The schema/table names retain `managed_label` for rollback compatibility even though the child ledger now
owns the bounded provider-label subset. Record the naming mismatch as compatibility debt and do not rename
or duplicate the tables while the version-10 rollback image remains in service. Migration 011 is logically
additive, transactionally rebuilds the parent and child tables only to widen the SQLite CHECK constraint,
and records minimum compatible reader 10. A version-10 worker fails closed on any unsettled new row;
rollback therefore removes the three operation IDs and drains/reconciles their children before retagging.

## Provider operation lifecycle

1. Resolve every current E-reference and label ref under the immutable user/channel/agent binding.
2. Sort messages by canonical provider message ID. Preserve the requested label set in canonical label-ref
   order. Reject duplicates and disabled/stale refs.
3. Compute the existing root parent operation ID and deterministic `toolchild_v1_` child IDs before any
   provider work.
4. In one `BEGIN IMMEDIATE`, insert-or-compare the parent plus the complete child set. A changed hash,
   target count, target, action, or label set is a terminal conflict. Commit all rows or none.
5. Return `queued` with the opaque parent operation ref. No Gmail call occurs in the API/Discord process.
6. The isolated worker claims only `email_managed_label_operations`, with an atomic lease and fencing
   token. Legacy queue tables are never in its claim query.
7. For each child, read current Gmail label IDs. Managed-label calls resolve/create only configured
   Jarvis-managed labels. Mailbox-state calls map their domain-owned fixed sentinel to exactly `INBOX` or
   `UNREAD`; no other label is accepted. Issue one add/remove `modify` call if necessary, read state again,
   and verify the requested state while every non-target label stays unchanged. Restore refuses a message
   currently carrying `SPAM` or `TRASH`.
8. Update the child, managed-label catalog IDs, and per-message label projection atomically. Verified
   no-op desired state is a successful idempotent receipt; it is not a second provider effect.
9. On failure, clear the lease and apply bounded exponential backoff. Exhaustion produces a visible dead
   letter. Expired claims recover on startup. A crash after Gmail mutation re-reads desired state and
   completes the same child without a second mutation.
10. Reduce the parent from durable children: all verified -> completed; verified plus any terminal failure
    -> partial; zero verified with terminal failure -> failed; nonterminal children -> queued. Clear private
    recovery JSON only after every expected child is terminal and the final result is stored.

## Historical backfill contract

- Interactive turns never call Gmail search or trigger synchronization/backfill. Reads use the local
  projection only and disclose freshness/coverage.
- `scripts/manage_email_backfill.py enqueue` creates one bounded
  `email.projection_backfill.v1` durable root job for the pre-activation central mailbox history addressed
  to configured destination aliases. It records a maximum message/page budget and refuses an unbounded
  request.
- `run-once` claims at most one page, uses the existing read-only gateway and MIME/classification/summary
  pipeline, commits idempotent projections, and enqueues one deterministic continuation job when a
  provider cursor remains. A fixed root count/page ceiling stops the chain as `partial` rather than
  silently continuing.
- `status` reports content-free accepted/ignored/failed/dead-letter counts, earliest/latest indexed dates,
  and whether provider pagination completed. `cancel` stops only unclaimed continuation jobs; it never
  deletes projected messages.
- Enable `EMAIL_AGENT_ALLOW_HISTORICAL_BACKFILL` only for explicit operator enqueue/drain. Turn it off
  after completion. Normal history synchronization continues independently throughout.
- Reprocessing an already projected message is idempotent by Gmail message ID and source hash. A changed
  body creates the normal new summary/classification revision; it does not duplicate the message.

## Execution phases

### E0 - Characterize and contain legacy label work

Allowed changes: plan/evidence, configuration defaults, a read-only audit command, and the removal of
automatic classification-to-provider enqueue behavior. No new tool or provider effect.

- [x] `E0-01` Record a verified Core backup, current image/config rollback tag, schema version, integrity
  result, label/mailbox operation status counts, oldest queued row, and active worker/timer/container
  evidence without recording private IDs or message content.
- [x] `E0-02` Set the live legacy automatic label-write flag false and prove synchronization no longer
  creates `email_label_operations`. Do not start the legacy worker or mutate Gmail.
- [x] `E0-03` Remove `_with_label_reconciliation` from sync/read execution. Keep a named dormant legacy
  compatibility function only if rollback tests require it; active Main and normal sync cannot call it.
- [x] `E0-04` Add `scripts/manage_email_operations.py legacy-audit` and
  `legacy-quarantine --dry-run|--apply`. Apply requires the verified backup, proves zero active claims,
  marks only legacy queued rows `cancelled` with a fixed reason, and never deletes rows or calls Gmail.
- [x] `E0-05` Re-run sync twice and prove the legacy queue count does not grow, no provider call occurred,
  and all five existing verified rows remain intact.

Rollback: restore the prior config/code only while the legacy worker remains stopped. Never start a worker
against the legacy backlog during rollback.

### E1 - Catalog discovery, query v2, pagination, and backfill

Allowed production files: `app/skills/domains/email_agent/config.py`, `query.py`, `storage.py`,
`service.py` only for delegation, ADD `catalog.py`, `app/prompts/skills/email_agent_skill.md`,
`app/jobs/types.py`, `app/jobs/repository.py` only if a generic contract is missing,
ADD `scripts/manage_email_backfill.py`, `app/config.py`, `.env.example`, and composition-only
`app/runtime.py`/`app/container.py` changes.

- [x] `E1-01` Add protected permissions version 2 with an explicit `managed_labels` list. Continue reading
  version 1 for existing read/sync compatibility, but typed label writes require version 2 and an enabled
  catalog. Category `gmail_label_name` is legacy metadata and cannot authorize a new write.
- [x] `E1-02` Implement safe opaque mailbox/label catalogs and publish `email.list_mailboxes` plus
  `email.list_labels`. Do not expose route addresses, Gmail label IDs, or unauthorized catalogs.
- [x] `E1-03` Upgrade `email.query_messages` to the locked v2 schema/defaults and keyset pagination.
  Existing P4 exact-date/DST validation remains authoritative.
- [x] `E1-04` Return managed labels, result-set ref, next cursor, normalized filters, freshness, indexed
  coverage, and truthful truncation in bounded observations. Update get-message/thread projections to
  include only safe managed-label names/refs.
- [x] `E1-05` Convert zero/multiple mailbox or label resolution into `needs_input` observations with safe
  candidates. Keep actual authorization failure as `denied`. Add direct replan tests.
- [x] `E1-06` Implement the bounded durable historical backfill contract using the existing job ledger.
  No interactive or model-visible backfill tool exists.
- [x] `E1-07` Activate only the six E1 read/status operations after the E1 gate:
  `email.list_mailboxes`, `email.list_labels`, `email.query_messages`, `email.get_message`,
  `email.get_thread`, and `email.status`. Preserve the four active Lists operations and prove non-Email
  capabilities still fail closed or execute normally.

Rollback: remove the Email read operation IDs/domain entry. Backfill jobs may be cancelled while
unclaimed; already projected messages remain. Do not delete projection rows.

### E2 - Additive managed-label operations and worker

Allowed production files: ADD `app/skills/domains/email_agent/operations.py`,
`app/skills/domains/email_agent/storage.py`, `receipts.py`, `handler.py`,
`app/services/google/gmail_spam_writer.py` with a compatibility shim or a bounded mailbox-writer
extraction, ADD `app/workers/email_operations_worker.py`, `app/db/core_schema.py`,
`app/db/domain_schema.py`, `app/db/migrations.py`, `app/config.py`, `.env.example`,
`deploy/docker/compose.yaml`, `scripts/manage_email_operations.py`, and composition-only runtime files.

- [x] `E2-01` Implement Core migration 010 exactly as specified and retain the version-9 image as a
  compatibility-aware rollback reader. Prove fresh v10, populated v9->v10, idempotent reopen, rollback
  reader access, foreign keys, indexes, transaction rollback, and legacy row preservation.
- [x] `E2-02` Publish and implement `email.apply_labels`, `email.remove_labels`, and
  `email.get_operation`. Keep all behavior in the Email domain handler; no router or intent branch.
- [x] `E2-03` Implement atomic parent/complete-child reservation, stable identity conflicts, replay, and
  startup reconstruction from the private parent manifest.
- [x] `E2-04` Generalize the isolated Gmail writer to one additive label-set mutation per message with
  strict managed-catalog checks and read-back. Preserve the legacy class/import behind a named shim until
  rollback expiry; do not broaden its credential surface.
- [x] `E2-05` Add the Compose-owned `email-operations-worker` with default-off enable flag, bounded polling,
  rate caps, lease/fencing, retry/dead-letter, startup recovery, heartbeat, graceful stop, and a
  `--readiness-only` path that never claims work or calls Gmail.
- [x] `E2-06` Extend `email.status` and `email.get_operation` with content-free backlog/worker/parent-child
  truth. No message subject, sender, address, body, or label provider ID enters worker health or events.
- [x] `E2-07` Prove duplicate delivery, crash before/after reservation, partial child insertion, Gmail
  mutation before local completion, provider no-op, mixed child outcomes, authorization change, stale E
  refs, stale labels, and worker restart.

Rollback: remove `email.apply_labels,email.remove_labels,email.get_operation` from the operation allowlist,
disable the new worker, wait for claims to quiesce/expire, and reconcile every new parent-bound row to a
truthful terminal state. Never run the legacy worker or automatically reverse verified Gmail labels.

### E3 - Authoritative model and live canary gate

- [x] `E3-01` Run focused tests, full suite, compile, owned-file Ruff, architecture ratchets, Compose parse,
  diff check, clean export, and public-tree check locally and on authoritative Ubuntu.
- [x] `E3-02` Run three consecutive held-out Main acceptance passes with every P5F case mandatory, every
  safety case passing, zero failed token loops, and total pass rate at least 95% under the development
  headroom profile.
- [x] `E3-03` Run isolated copied-production-database canaries for catalog discovery, arbitrary dates,
  pagination, query->apply, query->remove, missing-label recovery, authorization denial, replay, partial
  failure, and restart. Provider calls use a fake writer only.
- [x] `E3-04` Promote reads first. Verify an authorized Discord request for an exact date, rolling range,
  no-date sender query, one mailbox, multiple mailboxes, label filter, and next page. Verify an
  unauthorized channel receives no mailbox metadata.
- [x] `E3-05` Seed a dedicated protected canary managed label and use only an unmistakably disposable
  canary email in the central Jarvis mailbox. Start the new worker only after readiness and zero eligible
  legacy rows. Through Discord, query the canary, apply the canary label, verify provider/local state and
  one receipt, remove it, verify all unrelated labels stayed unchanged, then repeat both requests to prove
  idempotency.
- [ ] `E3-06` Observe for 24 hours with zero unauthorized/duplicate/unintended label effects, no dead-letter
  increase, fresh worker heartbeat, no legacy queue claims, and truthful query coverage/truncation.

Checkpoint (2026-09-01): release candidate 35 passed 876 automated tests, owned-file Ruff, focused and
architecture checks, three consecutive 34/34 held-out model runs with zero failed token loops, and the
complete 9/9 live read matrix. Main keeps commitment and skill selection at low reasoning while tool-step
planning uses medium reasoning; exact trusted catalog matches ground one or many array selectors without
phrase branches.

The live write canary then found the one disposable message through Discord, added the dedicated managed
label, verified provider read-back and durable receipts, removed the label, and verified that unrelated
labels were unchanged. The final audit found three completed parents and three verified children: exactly
one provider add, one provider remove, and one verified remove no-op. The dedicated label is absent from
the message, the worker has no queued/claimed/dead-letter work, and all 333 quarantined legacy rows remain
ineligible. Label writes now survive a normal base-Compose restart with the API unable to run the worker
loop and the isolated worker holding the Modify-token mount. The final clean export and public-tree check
passed. The 24-hour observation began at `2026-09-01T15:58:02Z`; E3-06 cannot complete before
`2026-09-02T15:58:02Z`.

Rollout corrections now locked by regression tests:

- runtime dependencies are composition-owned and overwrite caller claims; authorized Discord Email turns
  receive `email_operations` only when the protected write capability is configured;
- the typed tool envelope carries authenticated adapter subject and external Discord user identity as
  separate fields, and the Email domain reauthorizes against the external user;
- the isolated OAuth directory, not a single token file, is the narrow writable mount because token
  refresh uses atomic file replacement; and
- after a host restart, a stale model-server container may retain an invalid GPU handle and silently use
  CPU even while host GPU checks pass. Recreate only that Compose service, then verify GPU visibility and
  model placement before model certification.

Activation adds domain `email` and exact certified operation IDs while retaining Lists. Any provider
mutation before E3-05, any legacy row claim, any unmanaged/system label change, any cross-channel data
leak, or any false complete result is an immediate rollback.

### E4 - Reversible read and Inbox state

Allowed production files are the E2 operation/storage/writer/worker files, the Email Markdown contract,
Core schema/migrations, generated skill artifacts, model acceptance cases/evaluator, and their focused
tests. Router phrase branches, a second worker/queue, new OAuth scope, arbitrary system-label parameters,
and source-account credentials are forbidden.

- [x] `E4-01` Add migration 011 as specified above, including fresh/upgrade/populated-row preservation,
  exact CHECK constraints, foreign-key validation, interrupted-migration rollback, and retained version-10
  reader behavior.
- [x] `E4-02` Publish `email.set_read_state(message_refs,state=read|unread)`,
  `email.archive_messages(message_refs)`, and `email.restore_to_inbox(message_refs)` as no-approval,
  independent-batch, redacted external writes using current scoped E-references only.
- [x] `E4-03` Generalize the existing Email operation executor and private recovery manifest without
  changing root/child identity semantics. A child may contain either enabled managed-label refs or one
  domain-owned fixed system-label sentinel, never both.
- [x] `E4-04` Extend the existing isolated writer/worker with exact desired-state read-before/write/read-
  after behavior. Read/unread may change only `UNREAD`; archive/restore may change only `INBOX`; restore
  refuses `SPAM|TRASH`; unrelated labels must remain byte-for-byte equivalent as sets.
- [x] `E4-05` Test one/many targets, every state transition, already-correct no-op, label+read+archive
  composition, reversed tool order, stale/cross-channel refs, duplicate targets, malformed sentinels,
  authorization/allowlist removal, crash recovery, retry/dead letter, provider conflict, and proof that
  trash/spam/sending/source accounts remain unreachable.

### E5 - Certification, canary, and restarted observation

- [ ] `E5-01` Pass focused/full/architecture/Ruff/compile/Compose/diff/clean-export/public-tree gates and
  three consecutive held-out Main certifications. Evaluation accepts any causally valid composition order
  and forbids phrase- or folder-name-specific expectations.
- [x] `E5-02` Against a copied production database and fake provider, prove query -> label -> read ->
  archive, restore -> unread, all no-op/replay/restart cases, exact parent/child receipts, and zero legacy
  claims.
- [ ] `E5-03` After backups and readiness, activate the three exact operation IDs and use only a disposable
  message in the central Jarvis mailbox for a reversible live canary: record starting labels, mark read,
  archive, restore, restore the original read state, and verify all unrelated labels are unchanged.
- [ ] `E5-04` Restart the 24-hour Email observation from the completed E5 canary. Require fresh worker
  heartbeat, zero unexpected effects/duplicates/dead-letter growth/legacy claims, and truthful operation
  status. The earlier E3 observation remains useful evidence but cannot certify the expanded surface.

E4/E5 checkpoint (2026-09-01): migration 011, the three fixed system-label tools, and the generalized
Email parent/child executor and isolated worker are implemented. Local verification passed `884` tests
with `2` skipped; the exact clean Ubuntu candidate passed `889` tests, owned-file Ruff, compile,
architecture, Compose, clean-export, and public-tree gates. The copied-production database canary passed
the complete reversible state, no-op, replay, partial-failure, recovery, and legacy-isolation matrix.

The exact production image is
`sha256:5f67581ac4b005d3d574f21d02da9b0e87ef0a08787f88425061aeeb01223319` (retained as
`p5f-email-state-rc23`). Core was backed up and verified before its explicit upgrade to schema version 11;
the source, protected environment, and database backup are retained in the private predeployment backup
set recorded by local operational context. The three operation IDs were added independently to the
production allowlist only after Jarvis and the Email worker were healthy.

The reversible provider canary used one prior disposable central-mailbox message and queued work through
the same typed executor and durable parent/child ledger used by Discord. Four provider operations marked it
read, archived it, restored it to Inbox, and restored its original unread state. All four verified on the
first attempt; exact starting provider labels were restored, unrelated labels were unchanged, and the
audit ended with zero open/dead-letter operations and no change to the legacy ledger (`333` cancelled,
`5` historical verified, zero active claims).

A subsequent real-user Discord audit found three Email requests in the ordinary live Jarvis channel had
received no Email discovery card: the Discord skill scope and Email-domain grant existed only in the
separate protected notification channel. The exact live Jarvis channel was added to both authorization
layers without moving or removing the existing approval/notification destination, protected configs were
backed up, and only Jarvis was restarted. Both deterministic authorization checks now pass. The provider
canary remains valid provider evidence, but `E5-03` stays open until a real user-authored Discord request
successfully queries Email through the corrected adapter scope. The earlier `2026-09-01T20:29:09Z`
observation timestamp is withdrawn; `E5-04` starts after that end-to-end canary succeeds.

`E5-01` deliberately remains open. The deterministic portions passed, and one candidate-equivalent model
run passed `47/47`; the exact rc23 run passed `46/47` with zero failed token loops and all safety and new
Email mailbox-state cases passing. The single miss was an intermittent previously certified Calendar
exact-argument case. Because three consecutive `47/47` runs were not achieved, this development canary is
not recorded as final model certification. Further work should evaluate aggregate semantic reliability
without reintroducing phrase-specific prompts or benchmark-fixture tuning.

Rollback removes the three E4 operation IDs first, waits for every E4 child to become verified or a
truthful terminal no-effect state, disables the worker only if needed, and retags the retained version-10
image. Do not down-migrate Core or auto-reverse a verified user-requested mailbox state; the live canary
explicitly restores its disposable message before rollback readiness is declared.

## Required tests and acceptance cases

Add or update:

- `tests/unit/test_email_agent_config.py`
- `tests/unit/test_email_agent_catalog.py`
- `tests/unit/test_email_agent_query.py`
- `tests/unit/test_email_agent_storage.py`
- `tests/unit/test_email_agent_service.py`
- `tests/unit/test_email_agent_operations.py`
- `tests/unit/test_email_operations_worker.py`
- `tests/unit/test_gmail_spam_writer.py` or the renamed compatibility test
- `tests/unit/test_core_schema_migrations.py`
- `tests/unit/test_durable_job_repository.py`
- `tests/unit/test_authorized_skill_executor.py`
- `tests/unit/test_main_tool_loop.py`
- `tests/unit/test_main_model_acceptance.py`
- `tests/unit/test_architecture_boundaries.py`
- `tests/integration/test_email_label_batch_recovery.py`
- `tests/integration/test_email_backfill_recovery.py`
- `benchmarks/models/main_acceptance_cases.json`

Held-out language cases must include:

- one exact local date, a named weekday/date, last N days, between two times, and no interval;
- one and multiple routed mailboxes, human-friendly aliases, missing and ambiguous mailboxes;
- exact sender address, domain, sender display text, multiple senders, and sender plus attachment/label;
- all/active/completed visibility, more than one page, cursor continuation, stale/expired cursor, and
  coverage outside the projection;
- `Done`, `To-do`, `Bills`, and `AYSO` label selection with punctuation/case variation but no parser branch;
- query one/many messages then apply one/many labels; remove one/many labels; already-correct state;
- missing label catalog entry, stale E-ref, duplicate refs, wrong channel/user/agent, disabled operation,
  provider timeout, dead letter, restart, and mixed child outcomes; and
- malicious email content attempting to add a label, invoke another tool, change authority, or hide a
  provider failure.

The evaluator accepts multiple valid tool orders. It must not require a conservative fixed sequence:
Main may list catalogs before querying, query before listing labels, or directly use a current exact ref.
It must prove that missing selectors can return to reasoning and that one/many labels/messages share the
same schemas.

## Verification commands

```bash
python -m pytest -q \
  tests/unit/test_email_agent_config.py \
  tests/unit/test_email_agent_catalog.py \
  tests/unit/test_email_agent_query.py \
  tests/unit/test_email_agent_storage.py \
  tests/unit/test_email_agent_service.py \
  tests/unit/test_email_agent_operations.py \
  tests/unit/test_email_operations_worker.py \
  tests/unit/test_gmail_spam_writer.py \
  tests/unit/test_core_schema_migrations.py \
  tests/unit/test_durable_job_repository.py \
  tests/unit/test_authorized_skill_executor.py \
  tests/unit/test_main_tool_loop.py \
  tests/unit/test_main_model_acceptance.py \
  tests/unit/test_architecture_boundaries.py \
  tests/integration/test_email_label_batch_recovery.py \
  tests/integration/test_email_backfill_recovery.py
python -m compileall -q app scripts tests
python -m ruff check <P5F-owned Python files>
python -m pytest -q
python scripts/export_clean_repo.py "$(mktemp -d)"
git diff --check
```

Every Compose command on Hardybot includes `--env-file .env`, uses the clean-export candidate image with
`--no-build`, and starts the Email worker through its tracked profile only after readiness passes.

## Stop conditions

Stop and roll back the affected operation IDs if any of these occurs:

- the new worker can claim a legacy queue row;
- a classifier or sync pass queues a Gmail label operation without an explicit user tool call;
- an apply removes an unrelated managed label, or a remove touches an unmanaged/system label;
- a provider effect lacks one durable child identity and eventual verified/dead-letter state;
- Main needs a new phrase, punctuation, date, sender, mailbox, or label branch for an ordinary request;
- a missing selector becomes a policy denial instead of a bounded observation;
- a query hides incomplete coverage or truncation;
- a cursor/reference crosses user, channel, query, expiry, or authorization scope;
- private Email content reaches generic history, memory, tickets, events, worker health, or logs; or
- the clean public export contains a protected address, ID, path, label provider ID, or credential fact.

## Remaining debt after P5F

- P7 attaches shared ticket/receipt/watchdog semantics where appropriate; P5F retains Email-owned verified
  receipts as the interim authority.
- P8D migrates local review/snooze and formally approved spam behavior to typed tools and attaches the
  shared P7 ticket/outcome bridge. It does not recreate read/unread, archive/restore, exclusive category-
  label application, or a second worker/queue.
- P9 may compose Email observations into Lists/Calendar only after the cross-domain declassification and
  approval rules pass.
- Dynamic creation/deletion of managed-label definitions from Discord requires a later policy decision.
- Original source-account mutation remains explicitly out of scope.
- A repeated full model turn can regenerate an otherwise identical provider request at a different
  `call_ordinal`, producing a second verified no-op receipt. Exact-envelope retry is idempotent and Gmail
  receives no duplicate effect, but cross-turn semantic receipt coalescing remains compatibility debt;
  do not remove the call ordinal from operation identity without a separate replay-design review.

## Evidence ledger

| Phase | Diff | Tests | Runtime evidence | Rollback evidence | Status |
| --- | --- | --- | --- | --- | --- |
| E0 | contained legacy automatic enqueue and added bounded audit/quarantine | focused and full regression coverage passed | 333 queued legacy rows cancelled, zero active claims, five verified rows retained | verified Core backup plus retained source/config/image | complete |
| E1 | six typed reads, catalogs, query v2, pagination, truthful coverage, and bounded backfill | focused and full regression coverage passed | 341-message historical projection plus promoted 9/9 live read matrix | exact Email read allowlist removal; projection retained | complete |
| E2 | migration 010, three typed operations, parent/child ledgers, additive writer, and tracked worker | copied-database, recovery, focused, and full tests passed | worker readiness, fake-provider copied-database canaries, and restart-safe live worker passed | write IDs removed; worker disabled; claims reconciled | complete |
| E3 | split reasoning lanes, generic one/many trusted catalog grounding, composition-owned dependencies, and split adapter/user identity | 876 tests; owned-file Ruff; three consecutive 34/34 model runs; clean export/public-tree passed | promoted 9/9 live reads; live add/remove canary and post-restart receipt audit passed; observation began 2026-09-01T15:58:02Z | retained prior images/config/source plus verified database backup and exact kill switches | observation_in_progress |
| E4 | planned migration 011 plus typed read/unread, archive, and restore operations over the existing parent/child ledger and worker | pending | no activation before E5 gates | exact operation-ID removal, quiesce/reconcile, retained version-10 image | implementation_in_progress |
| E5 | certification and reversible live mailbox-state canary | pending | pending | pending | not_started |
