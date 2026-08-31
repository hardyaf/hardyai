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

micro_enabled: true
micro_functions:
  - function_id: calendar.view
    intent: calendar.view
    regex_contract: "direct bounded calendar view with deterministic date extraction"
    supported_actions:
      - read_calendar
    required_entities:
      - when_hint
    unsupported_or_escalate:
      - calendar.add_event
      - calendar.update_event
      - calendar.delete_event
      - calendar.invite
      - ambiguous_time_reference
micro_failure_handoff:
  baseline_context_keys:
    - micro_intent
    - micro_confidence
    - micro_entities
    - micro_ambiguity_flags
    - required_missing_fields
    - token_session_turn_summaries
  capability_context_keys: []

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

## MicroJarvis Contract

### Micro functions that are allowed

- None.

### Escalation triggers to Main Jarvis

- All calendar requests route to Main Jarvis.

### Failure handoff payload to Main Jarvis

- Include baseline micro decision context for interpretability.
- Include `required_missing_fields` when micro classification indicates missing required inputs.
- Include `last_event_reference`, `last_calendar_action`, and the condensed session summary.
- Resolve deictic follow-ups such as "make that all day" from the latest unambiguous calendar event.
- If no safe event reference is available, preserve `deictic_event_reference` and ask which event.

## Main Jarvis Responsibilities

Since micro is disabled, all requests go through Main Jarvis.

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
