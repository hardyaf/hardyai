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
  home.list_devices: migrate
  home.get_device_state: migrate
  home.get_switch_state: deactivate_stale
  home.list_switches: deactivate_stale
version: 3

main_handoff_context:
  always_pass_from_session:
    - pending_clarification
    - main_agent_token_session
    - token_session_turn_summaries
  domain_carryover:
    - last_switch_name
    - last_successful_action
    - pending_switch_confirmation
main_tools_contract_version: 1
main_tools:
  - tool_id: home.list_devices
    contract_version: 1
    purpose: "List configured devices from Jarvis's local simulated Home state. Use this read when a device must be discovered or an alias is unclear; returned device_ref values are canonical opaque selectors. This does not report physical-device truth."
    interactive: true
    effect: read
    approval_rule: none
    approval_conditions: []
    idempotency: not_applicable
    sensitivity: private
    persistence: redacted
    effect_cardinality: single
    runtime_dependencies: []
    transferable_observation_fields:
      - pattern: /devices/*/device_ref
        scope: same_domain
      - pattern: /devices/*/name
        scope: same_domain
    timeout_seconds: 5
    max_result_items: 100
    max_observation_chars: 6000
    legacy_intents:
      - home.list_switches
    input_schema:
      type: object
      additionalProperties: false
      required: []
      minProperties: 0
      maxProperties: 1
      properties:
        limit:
          type: integer
          minimum: 1
          maximum: 100
          description: "Maximum number of configured simulated devices to return."
    observation_schema:
      type: object
      additionalProperties: false
      required: [devices, source, simulated, truncated]
      properties:
        devices:
          type: array
          minItems: 0
          maxItems: 100
          items: &home_device_observation
            type: object
            additionalProperties: false
            required: [device_ref, name, state, alias_hints]
            properties:
              device_ref:
                type: string
                minLength: 10
                maxLength: 80
                description: "Canonical opaque device_v1 reference returned by the server."
              name:
                type: string
                minLength: 1
                maxLength: 100
              state:
                type: string
                enum: ["on", "off", unknown]
              alias_hints:
                type: array
                minItems: 0
                maxItems: 8
                uniqueItems: true
                items:
                  type: string
                  minLength: 1
                  maxLength: 100
              room_name:
                type: string
                minLength: 1
                maxLength: 100
              updated_at:
                type: string
                minLength: 1
                maxLength: 64
        source:
          type: string
          enum: [local_simulated_state]
        simulated:
          type: boolean
          const: true
        truncated:
          type: boolean

  - tool_id: home.get_device_state
    contract_version: 1
    purpose: "Read one configured device from Jarvis's local simulated Home state. Prefer a device_ref returned by home.list_devices. A human-supplied exact name or unique alias is allowed, but ambiguous, missing, or stale selectors return candidates instead of guessing. This does not report physical-device truth."
    interactive: true
    effect: read
    approval_rule: none
    approval_conditions: []
    idempotency: not_applicable
    sensitivity: private
    persistence: redacted
    effect_cardinality: single
    runtime_dependencies: []
    transferable_observation_fields:
      - pattern: /device/device_ref
        scope: same_domain
      - pattern: /candidates/*/device_ref
        scope: same_domain
    timeout_seconds: 5
    max_result_items: 3
    max_observation_chars: 3000
    legacy_intents:
      - home.get_switch_state
    input_schema:
      type: object
      additionalProperties: false
      required: []
      minProperties: 1
      maxProperties: 1
      properties:
        device_ref:
          type: string
          minLength: 10
          maxLength: 80
          description: "One canonical opaque device_v1 reference previously returned by Home discovery."
        name:
          type: string
          minLength: 1
          maxLength: 100
          description: "One human-supplied configured device name or deterministic alias; never an observed opaque reference copied as a name."
    observation_schema:
      type: object
      additionalProperties: false
      required: [candidates, match_status, source, simulated]
      properties:
        device: *home_device_observation
        candidates:
          type: array
          minItems: 0
          maxItems: 3
          items:
            type: object
            additionalProperties: false
            required: [device_ref, name, alias_hints]
            properties:
              device_ref:
                type: string
                minLength: 10
                maxLength: 80
              name:
                type: string
                minLength: 1
                maxLength: 100
              alias_hints:
                type: array
                minItems: 0
                maxItems: 8
                uniqueItems: true
                items:
                  type: string
                  minLength: 1
                  maxLength: 100
        match_status:
          type: string
          enum: [exact_ref, exact_name, unique_alias, ambiguous_alias, stale_reference, not_found]
        source:
          type: string
          enum: [local_simulated_state]
        simulated:
          type: boolean
          const: true

  - tool_id: home.set_device_state
    contract_version: 1
    purpose: "Set one exact configured device reference to on or off in Jarvis's local simulated Home state. The operation never targets a group, scene, room, alias, or all devices and does not claim physical-device truth."
    interactive: true
    effect: local_write
    approval_rule: none
    approval_conditions: []
    idempotency: required
    sensitivity: private
    persistence: redacted
    effect_cardinality: single
    runtime_dependencies: []
    transferable_observation_fields: []
    timeout_seconds: 10
    max_result_items: 3
    max_observation_chars: 3000
    legacy_intents:
      - home.set_switch
    input_schema:
      type: object
      additionalProperties: false
      required: [device_ref, state]
      properties:
        device_ref:
          type: string
          minLength: 10
          maxLength: 80
          description: "One canonical opaque device_v1 reference returned by Home discovery. Names and group selectors are forbidden."
        state:
          type: string
          enum: ["on", "off"]
    observation_schema:
      type: object
      additionalProperties: false
      required: [candidates, match_status, changed, idempotent_replay, source, simulated]
      properties:
        device: *home_device_observation
        candidates:
          type: array
          minItems: 0
          maxItems: 3
          items:
            type: object
            additionalProperties: false
            required: [device_ref, name, alias_hints]
            properties:
              device_ref:
                type: string
                minLength: 10
                maxLength: 80
              name:
                type: string
                minLength: 1
                maxLength: 100
              alias_hints:
                type: array
                minItems: 0
                maxItems: 8
                uniqueItems: true
                items:
                  type: string
                  minLength: 1
                  maxLength: 100
        match_status:
          type: string
          enum: [exact_ref, stale_reference]
        changed:
          type: boolean
        idempotent_replay:
          type: boolean
        source:
          type: string
          enum: [local_simulated_state]
        simulated:
          type: boolean
          const: true
---

# Lights Skill

## Purpose

Control configured house light switches with safe, deterministic behavior.

This skill is responsible for:
- listing configured simulated devices
- reading the simulated state of one exact configured device
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

## Typed Read Tools

### `home.list_devices`

List a bounded catalog of configured simulated devices. Use the returned opaque `device_ref` when a
later read must identify one device exactly.

### `home.get_device_state`

Read one configured simulated device by opaque reference, exact name, or unique deterministic alias.
Ambiguous, missing, and stale selectors return bounded candidates and require clarification.

The historical read names `home.get_switch_state` and `home.list_switches` remain legacy compatibility
aliases only. They are not projected to Main as tools.

## Intent Mapping

### `home.set_switch`
Set a known switch to `on` or `off`.

Common phrases:
- "turn on the kitchen light"
- "shut off the porch"
- "switch the mudroom light off"
- "turn it on" -> only if context safely resolves target

## Required Inputs

### Read Device State
- exactly one of `device_ref` or `name`
- prefer a current `device_ref` returned by `home.list_devices`
- a stale reference, ambiguous alias, or unknown name must clarify

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

1. For reads, select `home.list_devices` or `home.get_device_state`; for legacy control, classify the
   request as `home.set_switch`.
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
12. Every read states that the source is simulated local state and performs no action-log write.

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

## Execution Ownership

Main owns every interactive Lights turn.

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
- [x] Main execution contract completed
- [x] Failure handoff contract completed
- [x] Main handoff context completed
- [x] Pronoun/deictic behavior documented
- [x] No silent fuzzy execution
