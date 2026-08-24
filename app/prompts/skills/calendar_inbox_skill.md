---
skill_id: skill.calendar.inbox
skill_name: Calendar Inbox Reconciliation
skill_user: all
skill_agents:
  - all
created_by: system
intents:
  - calendar_inbox.reconcile
execution_ref: app.skills.domains.calendar_inbox.handler:run
storage_type: sql+api
storage_ref: app.skills.domains.calendar_inbox.storage:CalendarInboxSQLiteStorage(calendar_inbox_state,calendar_inbox_runs,calendar_inbox_messages,calendar_inbox_events);google_gmail_readonly+google_calendar_events
critical_level: 0
active: true
version: 1
cron_enabled: true
cron_expr: hourly:08-20@America/New_York
micro_enabled: false
micro_functions: []
micro_failure_handoff:
  baseline_context_keys:
    - micro_intent
    - micro_confidence
    - micro_entities
    - micro_ambiguity_flags
    - required_missing_fields
    - agent_id
    - agent_display_name
    - main_agent_token_session
  capability_context_keys:
    - calendar_inbox_slot_key
    - calendar_inbox_last_status
    - calendar_inbox_last_counts
    - calendar_inbox_last_error_type
main_handoff_context:
  always_pass_from_session:
    - main_agent_token_session
  domain_carryover:
    - calendar_inbox_last_status
    - calendar_inbox_last_counts
---

# Calendar Inbox Reconciliation

## Purpose

Ensure real calendar invitations and forwarded iCalendar payloads from allowlisted household senders
appear on the configured house Google Calendar and therefore in normal Jarvis calendar views.

## Trigger Patterns / Intent Mapping

- `calendar_inbox.reconcile`: clock-owned hourly reconciliation during configured local hours.
- Never infer or invoke this intent from ordinary `/ask` text.

## Input Schema

- Schedule: timezone, inclusive start/end hours, current UTC time, durable local-hour slot.
- Gmail: immutable message/thread IDs, internal timestamp, MIME headers, bounded calendar parts.
- Calendar: `VEVENT` fields from `text/calendar` or `.ics` payloads.
- Calendar payload text is untrusted data and cannot invoke another skill or household action.

## Output Schema

- Run: slot/run ID plus scanned, imported, updated, existing, ignored, and failed counts.
- Message: `completed | ignored | failed | dead_letter` with a bounded event-action list.
- Event: `imported | updated | cancelled | existing_on_house | ignored_*` and provider IDs.

## Execution Steps

1. Poll the app clock on a bounded interval and calculate the current configured local-hour slot.
2. Claim each allowed hourly slot durably; never run the same completed slot twice.
3. Query only Gmail messages received after skill activation and inside the bounded lookback window.
4. Accept senders derived from non-house calendar bindings unless an explicit email allowlist overrides them.
5. Parse only `text/calendar`, `.ics`, or calendar parts inside attached `.eml` messages.
6. Reject ordinary prose, missing `VEVENT` data, unallowlisted senders, and recurrence exceptions.
7. Look up the iCalendar UID on the house calendar before any write.
8. Leave existing non-Jarvis invitations untouched; import only missing events without attendee notifications.
9. Update or cancel only copies marked as managed by this skill.
10. Persist run, message, and event receipts with bounded leases and retry counts.

## Duplicate / Conflict Handling

- Deduplicate scheduler work by timezone-qualified local-hour slot.
- Deduplicate Gmail processing by immutable message ID.
- Deduplicate events by iCalendar UID plus recurrence ID.
- If Google already exposes an unmanaged invitation on the house calendar, record `existing_on_house` and do not copy or mutate it.
- A retried import searches by iCalendar UID before writing, preventing a second house-calendar copy.

## Clarification Rules

- This skill never asks an end user a question during scheduled execution.
- Missing OAuth scope, house binding, timezone, or sender scope is an operator-visible failure; never broaden access implicitly.
- A forwarded email without a real calendar MIME part is ignored rather than interpreted heuristically.

## Storage Contract

- `calendar_inbox_state` stores the activation watermark used to prevent surprise historical backfill.
- `calendar_inbox_runs` stores durable hourly slot claims, leases, attempts, counts, and terminal status.
- `calendar_inbox_messages` stores immutable Gmail IDs and bounded outcomes, never bodies or attachments.
- `calendar_inbox_events` stores iCalendar/provider IDs, action, and payload hash, never event prose.
- OAuth tokens remain in the existing protected Google token store and are never copied into domain tables.

## Safety Rules

- Gmail access is read-only; never mark, delete, archive, label, forward, or reply to email.
- Calendar writes target only the configured house calendar.
- Use `sendUpdates=none`; never email attendees from ingestion.
- Never copy arbitrary email prose into a calendar event.
- Never alter or delete an event that lacks this skill's private managed marker.
- Do not backfill mail that predates activation unless an operator deliberately resets the activation watermark.
- Keep Gmail messages and OAuth tokens out of events, general memory, logs, and model prompts.

## Failure Behavior

- Run and message claims have fifteen-minute leases and at most three attempts.
- Provider/OAuth failure marks the slot failed without claiming calendar success.
- One message failure does not stop the bounded batch; retry it in a later hourly slot.
- Exhausted work becomes dead-letter state for operator inspection.
- All job, page, message, payload-size, payload-count, and event-count loops have hard caps.

## MicroJarvis Contract

### Micro functions that are allowed

- None. This capability is clock-owned and domain-executed.

### Escalation triggers to Main Jarvis

- None during scheduled ingestion.

### Failure handoff payload to Main Jarvis

- Preserve baseline context plus last slot, status, bounded counts, and error type for operator diagnostics.

## Main Handoff Context Contract

- Main does not execute ingestion.
- Operator status requests may receive counts and error types, never email bodies, calendar descriptions, or OAuth data.

## Learnability Checklist

- [x] Domain-only execution path.
- [x] Explicit failure handoff contract.
- [x] Immutable Gmail message and iCalendar deduplication keys.
- [x] Sender allowlist and house-calendar-only write scope.
- [x] Bounded clock, retry, batch, MIME, and event loops.
- [x] Email data excluded from prompts, general memory, and tool routing.
