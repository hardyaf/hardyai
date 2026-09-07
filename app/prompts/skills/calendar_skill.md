---
skill_id: skill.productivity.calendar
skill_name: Calendar
skill_user: all
skill_agents:
  - all
created_by: system
intents:
  - calendar.add_event
  - calendar.view
  - calendar.update_event
  - calendar.delete_event
execution_ref: app.skills.domains.calendar.handler:run
storage_type: api
storage_ref: google_calendar_oauth
critical_level: 3
active: true
legacy_skill_ids:
  - skill.calendar.core
version: 2

main_handoff_context:
  always_pass_from_session:
    - pending_clarification
    - main_agent_token_session
    - token_session_turn_summaries
  domain_carryover:
    - last_event_reference
    - last_time_reference
    - last_calendar_action
    - pending_event_confirmation
main_tools_contract_version: 1
main_tools:
  - tool_id: calendar.query_events
    contract_version: 1
    purpose: "Query one authorized Calendar scope over an inclusive-start, exclusive-end interval. Use default only for the authorized default; preserve explicit scope selectors. Copy requested title/topic words into text. All constraints compose. Use time_basis=local_calendar for dates/local wall times and absolute for rolling or explicitly absolute instants; the server corrects local offsets from its timezone."
    interactive: true
    effect: read
    approval_rule: none
    approval_conditions: []
    idempotency: not_applicable
    sensitivity: private
    persistence: no_store
    effect_cardinality: single
    runtime_dependencies: []
    transferable_observation_fields:
      - pattern: /events
        scope: cross_domain
      - pattern: /normalized_range
        scope: same_domain
      - pattern: /calendar_scope
        scope: same_domain
      - pattern: /source
        scope: same_domain
      - pattern: /truncated
        scope: same_domain
    timeout_seconds: 30
    max_result_items: 100
    max_observation_chars: 8000
    legacy_intents:
      - calendar.view
    input_schema:
      type: object
      additionalProperties: false
      required: [start, end, calendar_scope, time_basis]
      properties:
        start:
          type: string
          format: date-time
          maxLength: 64
          description: "Inclusive aware RFC 3339 value. With local_calendar, preserve the intended local wall-clock fields in the server Time timezone (do not convert those fields to UTC); the server corrects the offset. With absolute, encode the exact instant."
        end:
          type: string
          format: date-time
          maxLength: 64
          description: "Exclusive aware RFC 3339 value. With local_calendar, preserve the intended next local wall-clock boundary; the server corrects DST/offsets independently. With absolute, encode the exact instant."
        calendar_scope:
          type: string
          minLength: 1
          maxLength: 100
          description: "One explicit person/calendar selector (plain or possessive, such as Alex or Alex's calendar), or the literal default for the authorized default Calendar."
        time_basis:
          type: string
          enum: [local_calendar, absolute]
          description: "local_calendar for named dates or local wall-clock boundaries; absolute for rolling intervals ending now or explicitly absolute instants."
        text:
          type: string
          minLength: 1
          maxLength: 200
          description: "Event-title/topic text explicitly requested by the user; preserve it whenever present."
        order:
          type: string
          enum: [oldest, newest]
        limit:
          type: integer
          minimum: 1
          maximum: 100
    observation_schema:
      type: object
      additionalProperties: false
      required: [events, normalized_range, calendar_scope, source, truncated]
      properties:
        events:
          type: array
          minItems: 0
          maxItems: 100
          items:
            type: object
            additionalProperties: false
            required: [event_ref, calendar_ref, resource_version, title, start, end, all_day, location, calendar_name]
            properties:
              event_ref:
                type: string
                minLength: 16
                maxLength: 80
              calendar_ref:
                type: string
                minLength: 16
                maxLength: 100
              resource_version:
                type: string
                minLength: 16
                maxLength: 100
              title:
                type: string
                minLength: 1
                maxLength: 200
              start:
                type: string
                maxLength: 64
              end:
                type: string
                maxLength: 64
              all_day:
                type: boolean
              location:
                type: string
                maxLength: 300
              calendar_name:
                type: string
                minLength: 1
                maxLength: 100
        normalized_range:
          type: object
          additionalProperties: false
          required: [start, end, timezone]
          properties:
            start:
              type: string
              format: date-time
              maxLength: 64
            end:
              type: string
              format: date-time
              maxLength: 64
            timezone:
              type: string
              minLength: 1
              maxLength: 64
        calendar_scope:
          type: object
          additionalProperties: false
          required: [requested, display_name, resolved, is_default, candidates]
          properties:
            requested:
              type: string
              minLength: 1
              maxLength: 100
            display_name:
              type: string
              minLength: 1
              maxLength: 100
            resolved:
              type: boolean
            is_default:
              type: boolean
            candidates:
              type: array
              minItems: 0
              maxItems: 10
              uniqueItems: true
              items:
                type: string
                minLength: 1
                maxLength: 100
        source:
          type: object
          additionalProperties: false
          required: [kind, synchronized, coverage_complete, queried_at]
          properties:
            kind:
              type: string
              enum: [google_calendar_live, local_in_memory]
            synchronized:
              type: boolean
            coverage_complete:
              type: boolean
            queried_at:
              type: string
              format: date-time
              maxLength: 64
        truncated:
          type: boolean
  - tool_id: calendar.create_event
    contract_version: 1
    purpose: "Create one event without attendees using exact start/end values. Never add invitees; use calendar.create_event_with_invites for outbound invitations. The server resolves the Calendar target, timezone, and resource version before operation identity."
    interactive: true
    effect: external_write
    approval_rule: none
    approval_conditions: []
    idempotency: required
    sensitivity: private
    persistence: standard
    effect_cardinality: single
    runtime_dependencies: []
    transferable_observation_fields:
      - pattern: /event_ref
        scope: same_domain
      - pattern: /resource_version
        scope: same_domain
    timeout_seconds: 30
    max_result_items: 1
    max_observation_chars: 4000
    legacy_intents: [calendar.add_event]
    input_schema:
      type: object
      additionalProperties: false
      required: [title, start, end, all_day, timezone, calendar_scope]
      properties: &calendar_create_properties
        title: {type: string, minLength: 1, maxLength: 200}
        start: {type: string, minLength: 10, maxLength: 64}
        end: {type: string, minLength: 10, maxLength: 64}
        all_day: {type: boolean}
        timezone: {type: string, minLength: 1, maxLength: 64}
        calendar_scope: {type: string, minLength: 1, maxLength: 100}
        calendar_ref: {type: string, minLength: 16, maxLength: 100}
        resource_version: {type: string, minLength: 16, maxLength: 100}
        location: {type: string, minLength: 1, maxLength: 300}
        description: {type: string, minLength: 1, maxLength: 2000}
    observation_schema: &calendar_write_observation
      type: object
      additionalProperties: false
      required: [action, sync_status, provider_event_id, event_ref, calendar_ref, resource_version, idempotent_replay, event]
      properties:
        action: {type: string, enum: [created, updated, deleted]}
        sync_status: {type: string, enum: [synced, not_synced]}
        provider_event_id: {type: string, minLength: 0, maxLength: 1024}
        event_ref: {type: string, minLength: 16, maxLength: 100}
        calendar_ref: {type: string, minLength: 16, maxLength: 100}
        resource_version: {type: string, minLength: 16, maxLength: 100}
        idempotent_replay: {type: boolean}
        event:
          type: object
          additionalProperties: false
          required: [title, start, end, all_day, timezone, location, attendee_emails, deleted]
          properties:
            title: {type: string, minLength: 0, maxLength: 200}
            start: {type: string, minLength: 0, maxLength: 64}
            end: {type: string, minLength: 0, maxLength: 64}
            all_day: {type: boolean}
            timezone: {type: string, minLength: 1, maxLength: 64}
            location: {type: string, minLength: 0, maxLength: 300}
            attendee_emails:
              type: array
              minItems: 0
              maxItems: 20
              uniqueItems: true
              items: {type: string, minLength: 3, maxLength: 254}
            deleted: {type: boolean}
  - tool_id: calendar.create_event_with_invites
    contract_version: 1
    purpose: "Create one exact event and send invitations to one or more explicit email addresses. This is outbound communication and always pauses for formal approval."
    interactive: true
    effect: outbound_communication
    approval_rule: always
    approval_conditions: []
    idempotency: required
    sensitivity: private
    persistence: redacted
    effect_cardinality: single
    runtime_dependencies: [action_approval]
    transferable_observation_fields:
      - pattern: /event_ref
        scope: same_domain
      - pattern: /resource_version
        scope: same_domain
    timeout_seconds: 30
    max_result_items: 1
    max_observation_chars: 4000
    legacy_intents: []
    input_schema:
      type: object
      additionalProperties: false
      required: [title, start, end, all_day, timezone, calendar_scope, invitee_emails]
      properties:
        <<: *calendar_create_properties
        invitee_emails:
          type: array
          minItems: 1
          maxItems: 20
          uniqueItems: true
          items: {type: string, minLength: 3, maxLength: 254}
    observation_schema: *calendar_write_observation
  - tool_id: calendar.update_event
    contract_version: 1
    purpose: "Update only the supplied non-attendee fields of one exact event. Use event_ref/event_start from calendar.query_events; the server re-resolves the target and binds its current resource version before a conditional write."
    interactive: true
    effect: external_write
    approval_rule: none
    approval_conditions: []
    idempotency: required
    sensitivity: private
    persistence: standard
    effect_cardinality: single
    runtime_dependencies: []
    transferable_observation_fields:
      - pattern: /event_ref
        scope: same_domain
      - pattern: /resource_version
        scope: same_domain
    timeout_seconds: 30
    max_result_items: 1
    max_observation_chars: 4000
    legacy_intents: [calendar.update_event]
    input_schema:
      type: object
      additionalProperties: false
      required: [event_ref, event_start, calendar_scope, timezone, patch]
      properties:
        event_ref: {type: string, minLength: 16, maxLength: 100}
        event_start: {type: string, minLength: 10, maxLength: 64}
        calendar_scope: {type: string, minLength: 1, maxLength: 100}
        timezone: {type: string, minLength: 1, maxLength: 64}
        calendar_ref: {type: string, minLength: 16, maxLength: 100}
        resource_version: {type: string, minLength: 16, maxLength: 100}
        patch:
          type: object
          additionalProperties: false
          required: []
          minProperties: 1
          properties:
            title: {type: string, minLength: 1, maxLength: 200}
            start: {type: string, minLength: 10, maxLength: 64}
            end: {type: string, minLength: 10, maxLength: 64}
            all_day: {type: boolean}
            location: {type: string, minLength: 1, maxLength: 300}
            description: {type: string, minLength: 1, maxLength: 2000}
    observation_schema: *calendar_write_observation
  - tool_id: calendar.delete_event
    contract_version: 1
    purpose: "Delete one exact event using event_ref/event_start from calendar.query_events. The server re-resolves the target and binds its current resource version before a conditional delete. Always requires formal approval."
    interactive: true
    effect: destructive_external
    approval_rule: always
    approval_conditions: []
    idempotency: required
    sensitivity: private
    persistence: redacted
    effect_cardinality: single
    runtime_dependencies: [action_approval]
    transferable_observation_fields: []
    timeout_seconds: 30
    max_result_items: 1
    max_observation_chars: 4000
    legacy_intents: [calendar.delete_event]
    input_schema:
      type: object
      additionalProperties: false
      required: [event_ref, event_start, calendar_scope, timezone]
      properties:
        event_ref: {type: string, minLength: 16, maxLength: 100}
        event_start: {type: string, minLength: 10, maxLength: 64}
        calendar_scope: {type: string, minLength: 1, maxLength: 100}
        timezone: {type: string, minLength: 1, maxLength: 64}
        calendar_ref: {type: string, minLength: 16, maxLength: 100}
        resource_version: {type: string, minLength: 16, maxLength: 100}
    observation_schema: *calendar_write_observation
---

# Calendar Skill

## Purpose

Manage scheduled events with accurate time, date, and context handling.

This skill is responsible for:
- creating events
- retrieving events
- updating events
- deleting events

This skill prioritizes:
- correctness over speed
- clarity over assumption
- explicit confirmation for destructive or ambiguous actions

This skill is not responsible for:
- guessing unclear times or dates
- silently modifying events
- interpreting vague scheduling without confirmation

## When To Use This Skill

Use this skill when the user wants to interact with their calendar.

Examples:
- "schedule a meeting tomorrow at 2"
- "what do I have today?"
- "move my dentist appointment to Friday"
- "delete my 3pm meeting"

## Do Not Use This Skill

Do not use this skill when:
- the user is discussing plans but not scheduling
- time references are too vague without clarification
- the request is about reminders that are not tied to calendar events (unless your system maps them)

## Intent Mapping

### `calendar.add_event`
Create a new calendar event.

Common phrases:
- "schedule a meeting tomorrow at 2"
- "add soccer practice Wednesday at 5"
- "put a reminder on my calendar for Friday morning"

### `calendar.view`
Retrieve events.

Common phrases:
- "what do I have today?"
- "what's on my calendar tomorrow?"
- "what's my schedule this week?"

### `calendar.update_event`
Modify an existing event.

Common phrases:
- "move my dentist appointment to Friday"
- "change my 3pm meeting to 4"
- "update soccer practice to 6pm"

### `calendar.delete_event`
Delete an event.

Common phrases:
- "delete my meeting at 3"
- "cancel my dentist appointment"
- "remove soccer practice"

## Required Inputs

### Create Event
- `title` required
- `start_time` required
- `date` required unless derivable from time expression
- `duration` or `end_time` recommended
- `timezone` assumed from system unless overridden

### Get Events
- `date_range` required
  - examples:
    - today
    - tomorrow
    - this week
    - specific date

### Update Event
- `event_reference` required
- at least one field to update:
  - `new_when_hint`
  - `new_event_title`
  - `all_day`

### Delete Event
- `event_reference` required

## Time Interpretation Rules

### Absolute Time
- "April 10 at 3pm" → exact
- "3pm today" → resolve using current date

### Relative Time
- "tomorrow" → next calendar day
- "next Friday" → next occurrence of Friday not today
- "this Friday" → nearest upcoming Friday in current week

### Ambiguous Time
Must clarify when:
- "later"
- "in the afternoon"
- "after lunch"
- "this evening"

### Time Defaults
Only apply defaults when safe:
- if user says "schedule a meeting tomorrow" → ask for time
- do not default to arbitrary times unless system policy defines one

## Context Resolution Rules

Jarvis may use context when:
- the user refers to "that meeting", "it", "the appointment"
- only one clear prior event exists

Jarvis must not assume when:
- multiple events match
- the reference is stale
- the user changed topic

When unsafe, ask clarification.

## Output Schema

Return:
- `status`: `ok | needs_input | ambiguous_event | not_found | error`
- `message`: short user-facing summary

Optional payloads:
- `event_id`
- `title`
- `start_time`
- `end_time`
- `date`
- `events`
- `suggestions`
- `pending_confirmation`

## Execution Rules

1. Classify intent.
2. Extract structured fields:
   - title
   - time
   - date
   - duration
3. Normalize time:
   - convert to system timezone
   - ensure valid datetime
4. Validate completeness.
5. Resolve event reference if updating/deleting.
6. If ambiguity exists:
   - return clarification with suggestions
7. Execute only when:
   - required inputs are present
   - event reference is unambiguous
8. Update context:
   - `last_event_reference`
   - `last_calendar_action`
   - `last_time_reference`
9. Return concise confirmation.

## Clarification Rules

Ask for clarification when:
- time is missing or ambiguous
- date is unclear
- multiple events match reference
- duration is needed but missing

Preferred style:
- short
- single question

Examples:
- "What time should I schedule that?"
- "Which meeting do you mean?"
- "Do you want to move it to 3pm or keep the same duration?"

## Event Matching Rules

When resolving an event:
- match by:
  - title
  - time
  - date
- prefer exact matches
- if multiple matches:
  - return top candidates
  - ask user to choose

Never:
- modify or delete based on weak match

## Safe Defaults

- never create events with missing critical fields
- never update or delete without confident match
- never assume duration unless system defines default
- always confirm destructive actions if ambiguity exists

## Execution Ownership

Main owns every interactive Calendar turn.

## Main Jarvis Responsibilities

All interactive Calendar requests go through Main Jarvis.

Main Jarvis must:
- interpret natural language time expressions
- resolve ambiguity safely
- ask for clarification when needed
- maintain continuity across turns
- avoid hallucinating events or confirmations

## Failure Behavior

### `needs_input`
Missing required fields.

### `ambiguous_event`
Multiple possible matches.

### `not_found`
No matching event found.

### `error`
Execution failure or API issue.

Never claim success without confirmation from execution layer.

## External System Contract

- integrates with Google Calendar via OAuth
- must:
  - handle API failures gracefully
  - confirm event creation/update/delete success
  - maintain consistent timezone handling
  - support event lookup and modification

## Follow-Up Examples

### Safe continuation
User: "Schedule a meeting tomorrow at 3."
User: "Move it to 4."
-> safe to resolve

### Unsafe continuation
User: "Schedule 2 meetings tomorrow."
User: "Move it to 4."
-> ask which meeting

### Suggestion flow
User: "Delete my meeting."
-> "Which meeting do you want to delete?"

## Learnability Checklist

- [x] Intent boundaries are explicit
- [x] Required fields are explicit
- [x] Time rules clearly defined
- [x] No silent assumptions
- [x] Ambiguity handled safely
- [x] No hallucinated execution
