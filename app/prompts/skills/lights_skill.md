---
skill_id: skill.home.lights
skill_name: Lights
skill_user: all
skill_agents:
  - all
created_by: system
intents:
  - home.set_switch
execution_ref: app.skills.domains.lights.handler:run
storage_type: sql
storage_ref: app.skills.domains.lights.storage:SQLiteLightsStorage(switches,switch_actions_log)
critical_level: 2
active: true
interactive: true
operation_dispositions:
  home.set_switch: migrate
  home.list_devices: deferred
  home.get_device_state: deferred
  home.get_switch_state: deactivate_stale
  home.list_switches: deactivate_stale
version: 2

micro_enabled: true
micro_functions:
  - function_id: lights.set_switch
    intent: home.set_switch
    regex_contract: "direct single-switch control with deterministic on/off extraction"
    supported_actions:
      - set_known_switch_on
      - set_known_switch_off
    required_entities:
      - switch_name
      - action
    unsupported_or_escalate:
      - ambiguous_switch_reference
      - missing_switch_name
      - missing_action
      - multi_target_request
      - scene_or_group_request
      - policy_restricted_target
      - unsafe_deictic_reference
micro_failure_handoff:
  baseline_context_keys:
    - micro_intent
    - micro_confidence
    - micro_entities
    - micro_ambiguity_flags
    - required_missing_fields
    - token_session_turn_summaries
  capability_context_keys:
    - last_switch_name
    - available_switches
    - last_switch_action
    - pending_switch_confirmation

main_handoff_context:
  always_pass_from_session:
    - pending_clarification
    - main_agent_token_session
    - token_session_turn_summaries
  domain_carryover:
    - last_switch_name
    - last_successful_action
    - pending_switch_confirmation
---

# Lights Skill

## Purpose

Control configured house light switches with safe, deterministic behavior.

This skill is responsible for:
- turning a known switch on
- turning a known switch off
- preserving continuity for short follow-up references

This skill is not responsible for:
- broad home automation planning
- unsupported device classes
- scenes, routines, or grouped actions unless explicitly implemented
- silently guessing the wrong switch

## When To Use This Skill

Use this skill when the user wants to control or check a configured light switch.

Examples:
- "turn on the kitchen light"
- "switch off the mudroom light"
- "turn it off" -> only if prior context safely resolves the switch

## Do Not Use This Skill

Do not use this skill when:
- the user is discussing lighting generally rather than controlling a switch
- the target switch cannot be safely identified
- the request refers to unsupported automation concepts
- the request is about wiring, hardware installation, or electrical advice rather than device control

## Intent Mapping

### `home.set_switch`
Set a known switch to `on` or `off`.

Common phrases:
- "turn on the kitchen light"
- "shut off the porch"
- "switch the mudroom light off"
- "turn it on" -> only if context safely resolves target

The future read operations are `home.list_devices` and `home.get_device_state`. They are deferred and
must not be advertised or dispatched until their typed implementations are added. The historical names
`home.get_switch_state` and `home.list_switches` are stale compatibility metadata only.

## Required Inputs

### Set Switch
- `switch_name` required unless safely resolved from context
- `action` required
  - allowed values:
    - `on`
    - `off`

## Context Resolution Rules

Jarvis may use `last_switch_name` only when:
- the immediately relevant prior context clearly refers to one switch
- no competing switch target is likely
- the user uses a short follow-up such as:
  - "turn it off"
  - "is it on?"
  - "switch it back on"

Jarvis must not use `last_switch_name` when:
- multiple switches were recently discussed
- the prior target is stale or unclear
- the user may have shifted topics
- the request could refer to a room, group, or scene instead of one switch

When unsafe, ask a short clarification.

## Output Schema

Return:
- `status`: `ok | needs_input | unknown_switch | partial | error`
- `message`: short user-facing summary

Optional payloads:
- `switch_name`
- `canonical_switch_name`
- `state_after`
- `available_switches`
- `suggestions`
- `pending_confirmation`

## Execution Rules

1. Classify an executable request as `home.set_switch`.
2. Extract `switch_name` and `action` if present.
3. Normalize the switch reference:
   - ignore case
   - ignore extra spaces
   - ignore trivial punctuation differences
4. Resolve aliases using configured runtime switch metadata.
5. Prefer exact or canonical alias matches.
6. Do not silently execute against a weak or ambiguous match.
7. If the target is unclear, return clarification with suggestions.
8. Execute only after the switch target is explicit or safely confirmed.
9. Persist state and action history when an action occurs.
10. Update carryover context:
   - `last_switch_name`
   - `last_switch_action`
   - `last_successful_action`
11. Return a short result summary.

## Clarification Rules

Ask for clarification when:
- `switch_name` is missing and cannot be safely resolved
- `action` is missing for a control request
- multiple switches match the same phrase
- the user refers to a room or vague location that maps to more than one switch
- the user asks for a grouped action that this skill does not support directly

Preferred clarification style:
- short
- single question
- include best suggestion when useful

Examples:
- "Which light do you want me to turn off?"
- "Did you mean the porch light?"
- "Do you want the kitchen ceiling light or the sink light?"

## Matching and Alias Rules

### Switches
- each switch should have one canonical name
- aliases may map to that canonical switch
- alias collisions must never silently resolve to the wrong switch
- near matches should produce suggestions, not execution

### Actions
- accepted canonical values:
  - `on`
  - `off`
- common natural-language forms should normalize:
  - "turn on"
  - "switch on"
  - "lights on"
  - "turn off"
  - "shut off"
  - "switch off"

## Safe Defaults

- never control a switch unless target and action are both safe and explicit
- do not treat a room name as a valid single switch unless metadata says it is a unique alias
- repeated same-state actions are acceptable and should be treated idempotently from the user perspective
- never claim a light changed state unless the handler confirmed success

## MicroJarvis Contract

### Allowed Directly by Micro
- `home.set_switch`

### Micro May Proceed Only When
- the target switch is explicit or safely resolved
- the action is explicit for `home.set_switch`
- the request is single-target and deterministic
- no clarification is needed

### Escalate to Main Jarvis When
- the switch reference is ambiguous
- the user asks for multiple switches at once
- the user requests a room-wide or grouped action
- the user uses a deictic reference without safe context
- the phrasing is conversational enough to require reasoning
- there is any policy or safety restriction on the target
- the request mixes home control with broader planning

## Main Jarvis Responsibilities

Main Jarvis should:
- resolve conversational or ambiguous light references safely
- ask clarifying questions when needed
- preserve continuity across follow-up turns
- translate natural phrasing into safe single-switch commands when possible
- surface unsupported grouped or scene-style requests clearly

## Failure Behavior

### `needs_input`
Use when a required field is missing.

### `unknown_switch`
Use when no safe switch target exists.
Include suggestions when available.

### `partial`
Use only if future support allows a multi-target request where some targets succeed and others do not.

### `error`
Use for invalid action values, execution failures, storage failures, or handler errors.

Never report a successful state change unless the execution layer confirmed it.

## Storage Contract

Primary tables:
- `switches`
- `switch_actions_log`

Minimum expectations:
- canonical switch identity
- current switch state
- alias-aware lookup support
- action history with timestamps
- enough logging for troubleshooting and auditability

## Follow-Up Examples

### Safe deictic continuation
User: "Turn on the porch light."
User: "Turn it off."
-> resolve to `last_switch_name = porch light`

### Unsafe deictic continuation
User: "Turn on the porch light and the kitchen light."
User: "Turn it off."
-> ask which light

### Suggestion flow
User: "Turn on the poarch light."
-> "Did you mean the porch light?"

## Learnability Checklist

- [x] Intent boundaries are explicit
- [x] Required entities are explicit
- [x] Micro contract completed
- [x] Failure handoff contract completed
- [x] Main handoff context completed
- [x] Pronoun/deictic behavior documented
- [x] No silent fuzzy execution
