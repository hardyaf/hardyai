---
skill_id: skill.fixture.core
skill_name: Fixture
skill_user: all
skill_agents:
- all
created_by: test
intents:
- fixture.read
execution_ref: app.skills.domains.lists.handler:run
storage_type: sql
storage_ref: fixture
micro_enabled: false
micro_functions: []
micro_failure_handoff: {}
main_handoff_context:
  always_pass_from_session:
  - pending_clarification
main_tools_contract_version: 1
main_tools:
- tool_id: fixture.read
  contract_version: 1
  purpose: Read one bounded fixture resource.
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
  - pattern: /summary
    scope: same_domain
  timeout_seconds: 10
  max_result_items: 5
  max_observation_chars: 1000
  legacy_intents:
  - fixture.read
  input_schema:
    type: object
    additionalProperties: false
    required:
    - query
    properties:
      query:
        type: string
        minLength: 1
        maxLength: 120
  observation_schema:
    type: object
    additionalProperties: false
    required:
    - summary
    properties:
      summary:
        type: string
        minLength: 0
        maxLength: 500
- tool_id: fixture.invalid
  contract_version: 1
  purpose: Read one bounded fixture resource.
  interactive: true
  effect: provider_admin
  approval_rule: none
  approval_conditions: []
  idempotency: not_applicable
  sensitivity: private
  persistence: redacted
  effect_cardinality: single
  runtime_dependencies: []
  transferable_observation_fields:
  - pattern: /summary
    scope: same_domain
  timeout_seconds: 10
  max_result_items: 5
  max_observation_chars: 1000
  legacy_intents:
  - fixture.read
  input_schema:
    type: object
    additionalProperties: false
    required:
    - query
    properties:
      query:
        type: string
        minLength: 1
        maxLength: 120
  observation_schema:
    type: object
    additionalProperties: false
    required:
    - summary
    properties:
      summary:
        type: string
        minLength: 0
        maxLength: 500
---

## Purpose

Fixture.

## Trigger Patterns / Intent Mapping

Fixture.

## Input Schema

Fixture.

## Output Schema

Fixture.

## Execution Steps

Fixture.

## Clarification Rules

Fixture.

## Duplicate / Conflict Handling

Fixture.

## Storage Contract

Fixture.

## Failure Behavior

Fixture.

## MicroJarvis Contract

Fixture.

## Main Handoff Context Contract

Fixture.

## Learnability Checklist

Fixture.
