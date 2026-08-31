---
skill_id: skill.core.memory
skill_name: Memory
skill_user: all
skill_agents:
  - all
created_by: system
intents:
  - memory.store_fact
  - memory.get_fact
  - memory.update_fact
  - memory.delete_fact
  - memory.list_memories
execution_ref: app.skills.domains.memory.handler:run
storage_type: sql
storage_ref: app.skills.domains.memory.storage:SQLiteMemoryStorage(memories,memory_tags,memory_access_log)
critical_level: 3
active: false
interactive: false
operation_disposition: deactivate_stale
version: 1

micro_enabled: false
micro_functions: []
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
    - last_memory_reference
    - last_memory_topic
    - last_memory_action
    - pending_memory_confirmation
---

# Memory Skill

## Purpose

Store and retrieve durable household memory that improves continuity, personalization, and task execution over time.

This skill is responsible for:
- storing explicit user-provided facts meant to persist
- recalling previously stored memories
- updating stored memories when they change
- deleting stored memories when asked
- listing relevant memories by topic or scope

This skill is not responsible for:
- replacing normal conversational reasoning
- storing every transient message
- silently storing speculative or inferred facts as truth
- acting as a raw conversation log

## Memory Model

This skill manages **persistent structured memory**, not temporary working context.

Use this skill for information that is:
- likely to matter again
- stable or semi-stable over time
- useful for future task execution
- intentionally provided or confirmed by the user

Examples:
- preferred names
- household routines
- durable preferences
- project conventions
- known family roles
- recurring constraints
- device names and meanings
- definitions Jarvis should reuse later

Do not store:
- one-off requests with no future value
- raw emotional venting unless user explicitly wants it remembered
- weak guesses or speculative inferences
- highly sensitive personal data unless system policy explicitly allows it

## When To Use This Skill

Use this skill when the user wants Jarvis to remember, recall, update, forget, or review persistent information.

Examples:
- "remember that Casey hates scratchy socks"
- "what do you remember about Taylor's schedule preferences?"
- "update that, we moved watering plants to Sunday"
- "forget the old internet password note"
- "show me what you remember about the house lights"

## Do Not Use This Skill

Do not use this skill when:
- the information is only needed for the current turn
- the user is brainstorming and has not committed to a fact
- the content is too vague to store cleanly
- the content is a raw transcript better handled by session context
- the memory would violate system privacy or sensitivity policy

## Intent Mapping

### `memory.store_fact`
Store a new persistent memory.

Common phrases:
- "remember that..."
- "save this for later"
- "keep in mind that..."
- "Jarvis should know that..."

### `memory.get_fact`
Retrieve one or more stored memories.

Common phrases:
- "what do you remember about..."
- "do you know whether..."
- "remind me what you know about..."
- "what have we said about..."

### `memory.update_fact`
Change an existing memory.

Common phrases:
- "update that"
- "actually, change it to..."
- "that's no longer true"
- "replace the old one with this"

### `memory.delete_fact`
Delete a stored memory.

Common phrases:
- "forget that"
- "delete that memory"
- "remove the old note about..."
- "don't remember that anymore"

### `memory.list_memories`
List stored memories for a topic, category, or entity.

Common phrases:
- "what do you remember about the kids?"
- "show me the house preferences"
- "list what you know about our routines"

## Required Inputs

### Store Fact
- `memory_text` required
- `topic` recommended
- `scope` optional
  - examples:
    - person
    - household
    - project
    - device
    - routine
- `importance` optional
  - allowed values:
    - low
    - normal
    - high
    - critical

### Get Fact
At least one of:
- `topic`
- `entity_name`
- `query_text`

### Update Fact
- `memory_reference` required unless a single safe prior memory is in context
- updated content required

### Delete Fact
- `memory_reference` required unless a single safe prior memory is in context

### List Memories
At least one of:
- `topic`
- `scope`
- `entity_name`

## Storage Criteria

A fact is worth storing only if at least one of these is true:
- the user explicitly asks to remember it
- it will likely improve future task execution
- it is a durable household preference or rule
- it defines a recurring workflow, relationship, or system convention
- it is a stable descriptor of how Jarvis should behave

A fact should usually not be stored if:
- it is only useful this turn
- it is highly likely to change soon
- it is redundant with already stored memory
- it is too vague to retrieve later
- it is an unverified inference

## Memory Quality Rules

Stored memories should be:
- concise
- atomic when possible
- phrased as usable facts
- tagged for retrieval
- timestamped
- attributable when relevant

Good stored form:
- "Taylor prefers short, direct summaries unless she asks for detail."
- "Casey dislikes scratchy socks."
- "The porch switch refers to the front exterior light."

Bad stored form:
- long raw paragraphs
- ambiguous statements without subject
- speculative guesses
- duplicate near-copies of existing memories

## Context Resolution Rules

Jarvis may use `last_memory_reference` only when:
- the immediately relevant prior turn clearly discussed a single memory
- there is no competing memory candidate
- the user uses follow-up phrasing such as:
  - "update that"
  - "forget it"
  - "change that to Sunday"

Jarvis must not assume when:
- multiple memories were recently discussed
- the topic is broad
- the prior memory is stale or unclear

When unsafe, ask a short clarification.

## Output Schema

Return:
- `status`: `ok | needs_input | ambiguous_memory | not_found | duplicate | partial | error`
- `message`: short user-facing summary

Optional payloads:
- `memory_id`
- `memory_text`
- `normalized_memory_text`
- `topic`
- `scope`
- `importance`
- `matching_memories`
- `suggestions`
- `pending_confirmation`

## Execution Rules

1. Classify the request into a memory intent.
2. Extract structured fields:
   - memory text
   - topic
   - scope
   - entity
   - reference target
3. For store requests:
   - check whether the content meets storage criteria
   - normalize into a concise usable fact
   - detect strong duplicates
4. For retrieval requests:
   - search by topic, entity, tags, and semantic text
   - rank exact and high-confidence matches first
5. For update/delete requests:
   - resolve the target memory safely
   - do not modify weak matches
6. If ambiguity exists:
   - return clarification with top candidates
7. Execute only after target and action are clear.
8. Update carryover context:
   - `last_memory_reference`
   - `last_memory_topic`
   - `last_memory_action`
9. Return a concise result summary.

## Clarification Rules

Ask for clarification when:
- the fact is too vague to store well
- multiple memories match a retrieval or update request
- delete/update target is not safely identified
- the user asks to remember something but the actual fact is unclear
- topic or entity matters for future retrieval and is missing

Preferred clarification style:
- short
- single question
- include best suggestion when useful

Examples:
- "What exactly should I remember?"
- "Which memory do you want me to update?"
- "Do you want me to store that under Casey or under household routines?"

## Duplicate Rules

On store:
- exact duplicates should not create new entries
- very strong near-duplicates should return `duplicate` or offer update/merge behavior
- weaker similarity should not block storage automatically

On update:
- prefer updating the existing canonical memory rather than creating a second version when clearly appropriate

## Safe Defaults

- do not store vague fragments as permanent memory
- do not store inferred facts as confirmed truth
- do not delete or overwrite memory without confident target resolution
- do not claim something is remembered unless storage confirms success
- prefer concise normalized memory text over raw user wording when meaning is preserved

## MicroJarvis Contract

### Micro functions that are allowed

- None.

### Escalation triggers to Main Jarvis

- All memory requests route to Main Jarvis.

### Failure handoff payload to Main Jarvis

- Include baseline micro decision context for interpretability.
- Include `required_missing_fields` when micro classification indicates missing required inputs.

## Main Jarvis Responsibilities

Since micro is disabled, all memory requests go through Main Jarvis.

Main Jarvis must:
- judge whether information is worth storing
- normalize memories into concise durable facts
- retrieve memories by meaning as well as keywords
- ask clarifying questions when memory targets are ambiguous
- distinguish between short-term conversational context and persistent memory
- avoid over-storing transient clutter

## Failure Behavior

### `needs_input`
Use when required information is missing or too vague.

### `ambiguous_memory`
Use when multiple memories could match the request.

### `not_found`
Use when no relevant memory is found.

### `duplicate`
Use when the same memory already exists strongly enough that a second copy should not be created.

### `partial`
Use when listing or batch operations only succeed in part.

### `error`
Use for storage failures, handler failures, or invalid requests.

Never claim something is stored, updated, or deleted unless the execution layer confirmed it.

## Storage Contract

Primary tables:
- `memories`
- `memory_tags`
- `memory_access_log`

Minimum expectations:
- stable memory identity
- normalized memory text
- optional raw source text
- tags/topics/entities
- timestamps for created and updated
- importance level
- soft delete or hard delete support according to system policy
- access logging sufficient for debugging and ranking

Suggested core fields for `memories`:
- `id`
- `owner`
- `memory_text`
- `normalized_text`
- `topic`
- `scope`
- `entity_name`
- `importance`
- `created_at`
- `updated_at`
- `is_deleted`

Suggested core fields for `memory_tags`:
- `id`
- `memory_id`
- `tag`

Suggested core fields for `memory_access_log`:
- `id`
- `memory_id`
- `action`
- `timestamp`

## Retrieval Behavior

When retrieving memory:
- prefer exact topic/entity matches first
- then high-confidence semantic matches
- rank by:
  - direct relevance
  - importance
  - recency
  - frequency of access, if supported

For user-facing results:
- summarize cleanly
- do not dump excessive raw storage detail unless asked
- group related memories when helpful

## Follow-Up Examples

### Safe continuation
User: "Remember that Casey hates scratchy socks."
User: "Update that to wool socks, not all scratchy socks."
-> safe to resolve prior memory

### Unsafe continuation
User: "Remember that Casey hates scratchy socks."
User: "Remember that Taylor prefers concise summaries."
User: "Update that."
-> ask which memory

### Duplicate flow
User: "Remember that Taylor prefers concise summaries."
User later: "Remember Taylor likes short direct answers."
-> likely duplicate or merge suggestion

### Topic listing
User: "What do you remember about house devices?"
-> list device-related memories, not all memories

## Learnability Checklist

- [x] Intent boundaries are explicit
- [x] Required fields are explicit
- [x] Persistent vs temporary memory is clearly separated
- [x] Duplicate handling is defined
- [x] Ambiguity handling is defined
- [x] No silent storage of weak guesses
- [x] No hallucinated memory operations
