---
skill_id: skill.lists.core
skill_name: Lists
skill_user: all
skill_agents:
  - all
created_by: system
intents:
  - lists.create_list
  - lists.add_item
  - lists.get_items
  - lists.delete_list
  - lists.remove_item
  - lists.mark_item_done
execution_ref: app.skills.domains.lists.handler:run
storage_type: sql
storage_ref: app.skills.domains.lists.storage:SQLiteListsStorage(lists,list_items)
critical_level: 3
active: true
version: 2

main_handoff_context:
  always_pass_from_session:
    - pending_clarification
    - main_agent_token_session
    - token_session_turn_summaries
  domain_carryover:
    - last_list_name
    - last_successful_action
    - pending_list_confirmation
main_tools_contract_version: 1
main_tools:
  - tool_id: lists.list_collections
    contract_version: 1
    purpose: "Enumerate the current user's authorized personal and explicitly shared list collections. Call this with no arguments when a requested list name is missing or must be resolved."
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
      - pattern: /collections/*/collection_ref
        scope: same_domain
      - pattern: /collections/*/name
        scope: same_domain
    timeout_seconds: 5
    max_result_items: 100
    max_observation_chars: 4000
    legacy_intents: []
    input_schema:
      type: object
      additionalProperties: false
      required: []
      properties: {}
    observation_schema:
      type: object
      additionalProperties: false
      required:
        - collections
        - owner_scope
        - truncated
      properties:
        collections:
          type: array
          minItems: 0
          maxItems: 100
          items: &lists_collection_observation
            type: object
            additionalProperties: false
            required:
              - collection_ref
              - name
              - owner_scope
              - item_count
              - updated_at
            properties:
              collection_ref:
                type: string
                minLength: 1
                maxLength: 255
              name:
                type: string
                minLength: 1
                maxLength: 100
              owner_scope:
                type: string
                enum:
                  - personal
                  - shared
              item_count:
                type: integer
                minimum: 0
                maximum: 1000000
              updated_at:
                type: string
                minLength: 0
                maxLength: 64
        owner_scope:
          type: string
          enum:
            - personal_and_shared
        truncated:
          type: boolean

  - tool_id: lists.get_collection
    contract_version: 1
    purpose: "Read one exact authorized list collection and a bounded ordered item set."
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
      - pattern: /collection/collection_ref
        scope: same_domain
      - pattern: /items/*/item_ref
        scope: same_domain
    timeout_seconds: 5
    max_result_items: 100
    max_observation_chars: 6000
    legacy_intents:
      - lists.get_items
    input_schema: &lists_collection_selector_input
      type: object
      additionalProperties: false
      required: []
      properties:
        collection_ref:
          type: string
          minLength: 1
          maxLength: 255
        name:
          type: string
          minLength: 1
          maxLength: 100
        limit:
          type: integer
          minimum: 1
          maximum: 100
    observation_schema:
      type: object
      additionalProperties: false
      required:
        - items
        - owner_scope
        - truncated
        - candidates
      properties:
        collection: *lists_collection_observation
        items:
          type: array
          minItems: 0
          maxItems: 100
          items: &lists_item_observation
            type: object
            additionalProperties: false
            required:
              - item_ref
              - text
              - checked
              - position
            properties:
              item_ref:
                type: string
                minLength: 1
                maxLength: 255
              text:
                type: string
                minLength: 1
                maxLength: 500
              checked:
                type: boolean
              position:
                type: integer
                minimum: 1
                maximum: 1000000
        owner_scope:
          type: string
          enum:
            - personal
            - shared
            - unresolved
        truncated:
          type: boolean
        candidates:
          type: array
          minItems: 0
          maxItems: 5
          items: *lists_collection_observation

  - tool_id: lists.create_collection
    contract_version: 1
    purpose: "Create one named empty personal list collection only; this operation does not add items. Return its stable reference."
    interactive: true
    effect: local_write
    approval_rule: none
    approval_conditions: []
    idempotency: required
    sensitivity: private
    persistence: redacted
    effect_cardinality: single
    runtime_dependencies: []
    transferable_observation_fields:
      - pattern: /collection/collection_ref
        scope: same_domain
    timeout_seconds: 10
    max_result_items: 1
    max_observation_chars: 3000
    legacy_intents:
      - lists.create_list
    input_schema:
      type: object
      additionalProperties: false
      required:
        - name
      properties:
        name:
          type: string
          minLength: 1
          maxLength: 100
    observation_schema:
      type: object
      additionalProperties: false
      required:
        - collection
        - created
        - idempotent_replay
      properties:
        collection: *lists_collection_observation
        created:
          type: boolean
        idempotent_replay:
          type: boolean

  - tool_id: lists.add_items
    contract_version: 1
    purpose: "Atomically add one explicit ordered item array to one exact authorized list collection. Supply exactly one selector: before a trusted reference exists, put the human-supplied list name in name and omit collection_ref; after a tool returns collection_ref, copy that opaque collection_v1 value and omit name. If the named list is missing and the request clearly intends it to exist, inspect collections, create it, then retry this tool with the created collection_ref."
    interactive: true
    effect: local_write
    approval_rule: none
    approval_conditions: []
    idempotency: required
    sensitivity: private
    persistence: redacted
    effect_cardinality: atomic_batch
    runtime_dependencies: []
    transferable_observation_fields: []
    timeout_seconds: 10
    max_result_items: 50
    max_observation_chars: 4000
    legacy_intents:
      - lists.add_item
    input_schema:
      type: object
      description: "Exactly two properties: items and one of name or collection_ref. Never supply both selectors."
      additionalProperties: false
      required:
        - items
      minProperties: 2
      maxProperties: 2
      properties:
        collection_ref:
          type: string
          description: "Opaque collection_v1 reference copied only from a trusted tool observation; never put a human list name here."
          minLength: 1
          maxLength: 255
        name:
          type: string
          description: "Human-supplied list name from the request, used when no trusted collection_ref has been observed."
          minLength: 1
          maxLength: 100
        items:
          type: array
          minItems: 1
          maxItems: 50
          items:
            type: string
            minLength: 1
            maxLength: 500
    observation_schema:
      type: object
      additionalProperties: false
      required:
        - added_items
        - existing_item_count
        - failed_items
        - candidates
        - idempotent_replay
      properties:
        collection_ref:
          type: string
          minLength: 1
          maxLength: 255
        added_items:
          type: array
          minItems: 0
          maxItems: 50
          items: *lists_item_observation
        existing_item_count:
          type: integer
          minimum: 0
          maximum: 1000000
        failed_items:
          type: array
          minItems: 0
          maxItems: 50
          items:
            type: string
            minLength: 1
            maxLength: 100
        candidates:
          type: array
          minItems: 0
          maxItems: 5
          items: *lists_collection_observation
        idempotent_replay:
          type: boolean

  - tool_id: lists.update_item
    contract_version: 1
    purpose: "Update the text and/or checked state of one exact item reference in one exact authorized list. Discover the collection and item first; never infer an item reference from text."
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
    max_result_items: 1
    max_observation_chars: 3000
    legacy_intents:
      - lists.mark_item_done
    input_schema:
      type: object
      additionalProperties: false
      required:
        - collection_ref
        - item_ref
        - patch
      properties:
        collection_ref:
          type: string
          minLength: 1
          maxLength: 255
        collection_version:
          type: string
          description: "Current collection revision copied by the domain canonicalizer and rechecked at execution."
          minLength: 1
          maxLength: 64
        owner_scope:
          type: string
          description: "Resolved ownership scope supplied by the domain canonicalizer."
          enum:
            - personal
            - shared
        item_ref:
          type: string
          minLength: 1
          maxLength: 255
        patch:
          type: object
          additionalProperties: false
          required: []
          minProperties: 1
          maxProperties: 2
          properties:
            text:
              type: string
              minLength: 1
              maxLength: 500
            checked:
              type: boolean
    observation_schema:
      type: object
      additionalProperties: false
      required:
        - collection_ref
        - collection_version
        - item
        - changed
        - idempotent_replay
      properties:
        collection_ref:
          type: string
          minLength: 1
          maxLength: 255
        collection_version:
          type: string
          minLength: 1
          maxLength: 64
        item: *lists_item_observation
        changed:
          type: boolean
        idempotent_replay:
          type: boolean

  - tool_id: lists.remove_items
    contract_version: 1
    purpose: "Atomically remove one to 50 exact item references from one exact authorized list. Discover references first; ambiguity requires another read or clarification."
    interactive: true
    effect: local_write
    approval_rule: none
    approval_conditions: []
    idempotency: required
    sensitivity: private
    persistence: redacted
    effect_cardinality: atomic_batch
    runtime_dependencies: []
    transferable_observation_fields: []
    timeout_seconds: 10
    max_result_items: 50
    max_observation_chars: 4000
    legacy_intents:
      - lists.remove_item
    input_schema:
      type: object
      additionalProperties: false
      required:
        - collection_ref
        - item_refs
      properties:
        collection_ref:
          type: string
          minLength: 1
          maxLength: 255
        collection_version:
          type: string
          description: "Current collection revision copied by the domain canonicalizer and rechecked at execution."
          minLength: 1
          maxLength: 64
        owner_scope:
          type: string
          description: "Resolved ownership scope supplied by the domain canonicalizer."
          enum:
            - personal
            - shared
        item_refs:
          type: array
          minItems: 1
          maxItems: 50
          uniqueItems: true
          items:
            type: string
            minLength: 1
            maxLength: 255
    observation_schema:
      type: object
      additionalProperties: false
      required:
        - collection_ref
        - collection_version
        - removed_items
        - remaining_item_count
        - changed
        - idempotent_replay
      properties:
        collection_ref:
          type: string
          minLength: 1
          maxLength: 255
        collection_version:
          type: string
          minLength: 1
          maxLength: 64
        owner_scope:
          type: string
          description: "Resolved ownership scope supplied by the domain canonicalizer."
          enum:
            - personal
            - shared
        removed_items:
          type: array
          minItems: 1
          maxItems: 50
          items: *lists_item_observation
        remaining_item_count:
          type: integer
          minimum: 0
          maximum: 1000000
        changed:
          type: boolean
        idempotent_replay:
          type: boolean

  - tool_id: lists.clear_collection
    contract_version: 1
    purpose: "Delete every item from one exact authorized list while keeping the collection. Always requires formal approval bound to the current collection revision."
    interactive: true
    effect: destructive_local
    approval_rule: always
    approval_conditions: []
    idempotency: required
    sensitivity: private
    persistence: redacted
    effect_cardinality: atomic_batch
    runtime_dependencies:
      - action_approval
    transferable_observation_fields: []
    timeout_seconds: 10
    max_result_items: 1
    max_observation_chars: 3000
    legacy_intents: []
    input_schema: &lists_destructive_collection_input
      type: object
      additionalProperties: false
      required:
        - collection_ref
      properties:
        collection_ref:
          type: string
          minLength: 1
          maxLength: 255
        collection_version:
          type: string
          description: "Current collection revision copied by the domain canonicalizer and rechecked after approval."
          minLength: 1
          maxLength: 64
        owner_scope:
          type: string
          description: "Resolved ownership scope supplied by the domain canonicalizer."
          enum:
            - personal
            - shared
    observation_schema:
      type: object
      additionalProperties: false
      required:
        - collection_ref
        - collection_version
        - removed_item_count
        - changed
        - idempotent_replay
      properties:
        collection_ref:
          type: string
          minLength: 1
          maxLength: 255
        collection_version:
          type: string
          minLength: 1
          maxLength: 64
        removed_item_count:
          type: integer
          minimum: 0
          maximum: 1000000
        changed:
          type: boolean
        idempotent_replay:
          type: boolean

  - tool_id: lists.delete_collection
    contract_version: 1
    purpose: "Delete one exact authorized list and all of its items. Always requires formal approval bound to the current collection revision."
    interactive: true
    effect: destructive_local
    approval_rule: always
    approval_conditions: []
    idempotency: required
    sensitivity: private
    persistence: redacted
    effect_cardinality: single
    runtime_dependencies:
      - action_approval
    transferable_observation_fields: []
    timeout_seconds: 10
    max_result_items: 1
    max_observation_chars: 3000
    legacy_intents:
      - lists.delete_list
    input_schema: *lists_destructive_collection_input
    observation_schema:
      type: object
      additionalProperties: false
      required:
        - collection_ref
        - collection_version
        - deleted_item_count
        - deleted
        - changed
        - idempotent_replay
      properties:
        collection_ref:
          type: string
          minLength: 1
          maxLength: 255
        collection_version:
          type: string
          minLength: 1
          maxLength: 64
        deleted_item_count:
          type: integer
          minimum: 0
          maximum: 1000000
        deleted:
          type: boolean
        changed:
          type: boolean
        idempotent_replay:
          type: boolean
---

# Lists Skill

## Purpose

Manage persistent household lists.

This skill is responsible for:
- creating lists
- adding items
- reading list contents
- removing items
- marking items done
- deleting lists

This skill is not responsible for:
- general planning or brainstorming unless the result should be written to a list
- shopping recommendations unless explicitly requested as conversation first
- silently guessing the wrong list or wrong item

## When To Use This Skill

Use this skill when the user wants to perform a list action such as:
- "make a grocery list"
- "add milk to the grocery list"
- "what's on my grocery list?"
- "remove eggs from the grocery list"
- "mark paper towels done on Costco"
- "delete the camping list"

## Do Not Use This Skill

Do not use this skill when:
- the user is only discussing ideas without wanting list storage
- the target list is too ambiguous to resolve safely
- the user is asking for advice rather than a list action
- the user is referring to an external task system that this skill does not control

## Intent Mapping

### `lists.create_list`
Create a new named list.

Common phrases:
- "make a list called groceries"
- "start a camping checklist"
- "create a Home Depot list"

### `lists.add_item`
Add one or more items to an existing or newly confirmed list.

Common phrases:
- "add milk to groceries"
- "put batteries on the Costco list"
- "add it to that list" -> only if context safely resolves `last_list_name`

### `lists.get_items`
Read list contents.

Common phrases:
- "what's on groceries?"
- "show me the Costco list"
- "read me my weekend project list"

### `lists.remove_item`
Remove one or more items.

Common phrases:
- "remove milk from groceries"
- "take batteries off the Costco list"

### `lists.mark_item_done`
Mark an item complete without deleting it unless user chooses removal.

Common phrases:
- "mark milk done on groceries"
- "check off paper towels"

### `lists.delete_list`
Delete an entire list.

Common phrases:
- "delete the camping list"
- "remove my old hardware store list"

## Required Inputs

### Create List
- `list_name` required

### Add Item
- `list_name` required unless safely resolved from context
- `item_text` required

### Compound Create And Add
- A request that creates one list and immediately adds items is a typed Main plan.
- Main interprets the user's wording into the closed `items[]` schema; punctuation and conjunctions are
  not parsed by the Lists domain.
- Preserve the intended item text after model interpretation, including meaningful punctuation or labels.
- Execute one `lists.create_collection` call, observe its stable reference, then execute one atomic
  `lists.add_items` call for the complete bounded array.
- The same path handles one through 50 items; item count does not select a different handler or workflow.

### Get Items
- `list_name` required unless safely resolved from context

### Remove Item
- `list_name` required unless safely resolved from context
- `item_text` required

### Mark Item Done
- `list_name` required unless safely resolved from context
- `item_text` required
- `completion_mode` optional
  - allowed values:
    - `done`
    - `remove`
  - ask if missing and behavior matters

### Delete List
- `list_name` required

## Context Resolution Rules

Jarvis may use `last_list_name` only when:
- the immediately relevant prior context clearly refers to a single list
- no competing list target is likely
- the user uses a deictic reference such as:
  - "it"
  - "that list"
  - "add eggs too"

Jarvis must not use `last_list_name` when:
- multiple lists were recently discussed
- the prior list target is stale or unclear
- the user may have shifted topics

When unsafe, ask a short clarification.

## Output Schema

Return:
- `status`: `ok | needs_input | unknown_list | unknown_item | partial | error`
- `message`: short user-facing summary

Optional payloads:
- `list_name`
- `items`
- `added_items`
- `removed_items`
- `completed_items`
- `available_lists`
- `suggestions`
- `pending_confirmation`

## Execution Rules

1. Classify the request into a list intent.
2. Extract `list_name` and item fields.
3. Normalize list names using canonical comparison:
   - ignore case
   - ignore extra spaces
   - ignore minor punctuation differences
4. Prefer exact canonical matches.
5. Do not silently execute against a fuzzy match.
6. If the intended target is unclear, return clarification with ranked suggestions.
7. Execute only after the target list is explicit or safely confirmed.
8. Update carryover context:
   - `last_list_name`
   - `last_list_operation`
   - `last_successful_action`
9. Return a short result summary.

## Clarification Rules

Ask for clarification when:
- `list_name` is missing and cannot be safely resolved
- `item_text` is missing
- the list does not exist and multiple similar lists exist
- the item is unclear or multiple matching items exist
- `lists.mark_item_done` is requested but `completion_mode` matters and was not stated

Preferred clarification style:
- short
- single question
- include best suggestion when useful

Examples:
- "Which list should I add that to?"
- "Did you mean the Costco list?"
- "Do you want me to mark that done or remove it?"

## Duplicate and Matching Rules

### Lists
- list names should normalize to a single canonical form per owner
- near matches should produce suggestions, not silent execution

### Items
- duplicate items are allowed unless service policy prevents them
- item matching may use fuzzy suggestions for remove/done flows
- low-confidence item matches must ask

## Safe Defaults

- For `lists.add_item`, do not create a missing list silently unless product policy explicitly allows it
- For `lists.remove_item`, do not remove multiple items unless user intent is clear
- For `lists.delete_list`, require explicit list target
- For `lists.mark_item_done`, preserve history when possible instead of deleting automatically

## Execution Ownership

Main owns every interactive Lists turn.

## Main Jarvis Responsibilities

Main Jarvis should:
- handle conversational phrasing
- ask clarifying questions when needed
- resolve deictic follow-ups safely
- convert natural planning language into typed list tools without phrase-specific domain branches
- preserve continuity across turns
- execute bounded create-and-add requests as `create_collection` followed by one `add_items(items[])`

## Failure Behavior

### `needs_input`
Use when a required field is missing.

### `unknown_list`
Use when no safe list target exists.
Include suggestions when available.

### `unknown_item`
Use when the list exists but the item does not match safely.
Include suggestions when available.

### `partial`
Use when only part of a multi-item request succeeded.

### `error`
Use for storage, handler, or execution failures.

Never claim an item was added, removed, or completed unless the handler confirmed success.

## Storage Contract

Primary tables:
- `lists`
- `list_items`

Minimum expectations:
- persistent list metadata by owner
- stable item identity
- ordered items
- completion/removal support according to service policy
- auditability sufficient for troubleshooting

## Follow-Up Examples

### Safe deictic continuation
User: "Make a Costco list."
User: "Add paper towels."
-> resolve to `last_list_name = Costco`

### Unsafe deictic continuation
User: "Show me groceries and Home Depot."
User: "Add batteries."
-> ask which list

### Suggestion flow
User: "Add milk to groceres."
-> "Did you mean the Groceries list?"

## Learnability Checklist

- [x] Intent boundaries are explicit
- [x] Required entities are explicit
- [x] Main execution contract completed
- [x] Failure handoff contract completed
- [x] Main handoff context completed
- [x] Pronoun/deictic behavior documented
- [x] No silent fuzzy execution
