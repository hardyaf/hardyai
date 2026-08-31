# Skill Template (Learnability Contract)

Use this file as the required starting point for every new skill.

If the micro/main handoff sections are not filled out, the skill is not learnable.

```yaml
---
skill_id: skill.example.core
skill_name: Example Skill
skill_user: all
skill_agents:
  - all
created_by: system
intents:
  - example.intent
execution_ref: app.skills.domains.example.handler:run
storage_type: sql
storage_ref: "app.skills.domains.example.storage:SQLiteExampleStorage(example_table)"
safety_level: normal
version: 1

# Typed Main tool contract (required for a future interactive tool).
# P2 owns compilation and validation; leaving main_tools empty publishes no tool.
main_tools_contract_version: 1
main_tools:
  - tool_id: example.read
    contract_version: 1
    purpose: "Read one bounded example resource."
    interactive: true
    effect: read
    approval_rule: none
    approval_conditions: []
    idempotency: not_applicable
    sensitivity: normal
    persistence: standard
    effect_cardinality: single
    runtime_dependencies: []
    transferable_observation_fields: []
    timeout_seconds: 30
    max_result_items: 20
    max_observation_chars: 4000
    legacy_intents:
      - example.intent
    input_schema:
      type: object
      additionalProperties: false
      required:
        - entity_name
      properties:
        entity_name:
          type: string
          minLength: 1
          maxLength: 200
    observation_schema:
      type: object
      additionalProperties: false
      required:
        - status
      properties:
        status:
          type: string
          enum:
            - ok

# Every current operation also needs an explicit migration disposition.
# Allowed planning values: migrate, scheduler_only, adapter_only,
# operator_only, context_only, prohibited, deactivate_stale, or deferred.
operation_dispositions:
  example.intent: migrate

# Micro capability contract (required)
micro_enabled: false
micro_functions:
  - function_id: example.read
    intent: example.intent
    regex_contract: "document robust matching pattern(s) and edge cases"
    supported_actions:
      - "read only"
    required_entities:
      - "entity_name"
    unsupported_or_escalate:
      - "ambiguous references"
      - "create/update/delete actions"

# Failure handoff contract (required)
micro_failure_handoff:
  baseline_context_keys:
    - micro_intent
    - micro_confidence
    - micro_entities
    - micro_ambiguity_flags
    - required_missing_fields
    - agent_id
    - agent_display_name
    - token_session_turn_summaries
  capability_context_keys:
    - "last_<domain>_reference"
    - "available_<domain>_entities"

# Main handoff context contract (required for all skills)
main_handoff_context:
  always_pass_from_session:
    - "pending_clarification"
    - "token_session_turn_summaries"
  domain_carryover:
    - "last_<domain>_reference"
    - "last_successful_action"

# Optional for knowledge/conversation skills
research_policy:
  web_lookup_enabled: true
  knowledge_confidence_threshold: 0.70
  freshness_triggers:
    - latest
    - current
    - today
    - recent
    - news
  source_requirements:
    - include_links
    - include_date_context
    - no_fabricated_sources
---
```

## 1. Purpose

What this skill does and what it explicitly does not do.

## 2. Trigger Patterns / Intent Mapping

Phrases, regex patterns, and intent routing rules.

## 3. Input Schema

Required and optional inputs.

## 4. Output Schema

Result format, status values, and user-facing message shape.

## 5. Execution Steps

Deterministic runtime steps.

## 6. Clarification Rules

When to ask follow-ups and what fields are missing.

## 7. Duplicate / Conflict Handling

How collisions and repeats are handled.

## 8. Storage Contract

Where read/write data lives and required integrity rules.

## 9. Failure Behavior

Failure modes, error text, and retry policy.

## 10. MicroJarvis Contract (Required)

### 10.1 Micro functions that are allowed
- Which functions micro may execute directly.
- Regex robustness requirements for each function.

### 10.2 Escalation triggers to Main Jarvis
- Explicit reasons micro must escalate.

### 10.3 Failure handoff payload to Main Jarvis
- Baseline fields passed on handoff.
- Capability-specific context keys that must be included.

## 11. Main Handoff Context Contract (Required)

- What context Main always receives when this skill is involved.
- Domain carryover keys from session history required for continuity.

## 12. Learnability Checklist (Required)

- [ ] Micro contract completed (`micro_enabled`, `micro_functions`, escalation rules)
- [ ] Failure handoff contract completed (baseline + capability keys)
- [ ] Main handoff context contract completed
- [ ] At least one pronoun/deictic follow-up example documented
- [ ] Skill can continue correctly after micro failure -> main handoff

## 13. Research Policy (Optional, Required for Knowledge Skills)

- When to trigger research lookup (`knowledge_confidence` threshold + freshness triggers).
- What source/citation behavior is required.
- What fallback message is used when research is unavailable.
