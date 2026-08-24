---
skill_id: skill.conversation.general
skill_name: Conversation
skill_user: all
skill_agents:
  - all
created_by: system
intents:
  - conversation.general
  - unknown
execution_ref: app.skills.domains.conversation.handler:run
storage_type: hybrid
storage_ref: app.skills.domains.conversation.storage:ConversationSQLiteStorage(conversation_topics,conversation_topic_history) + data/skill_history/conversation
critical_level: 2
active: true
version: 1
micro_enabled: false
micro_functions: []
research_policy:
  web_lookup_enabled: true
  knowledge_confidence_threshold: 0.70
  freshness_triggers:
    - latest
    - current
    - today
    - recent
    - news
    - release notes
  source_requirements:
    - include_links
    - include_date_context
    - no_fabricated_sources
micro_failure_handoff:
  baseline_context_keys:
    - micro_intent
    - micro_confidence
    - micro_entities
    - micro_ambiguity_flags
  capability_context_keys: []
main_handoff_context:
  always_pass_from_session:
    - main_agent_token_session
  domain_carryover:
    - last_successful_action
---

# Conversation Skill

## Purpose

Handle non-tool turns: explanation, planning, brainstorming, and guidance.

## Trigger Patterns / Intent Mapping

- `conversation.general`
- `unknown` when no executable tool action is appropriate.
- Route here when user asks an informational question without a direct command.

## Input Schema

- Free-form natural language request.
- Optional contextual hints from micro classification.
- Optional handoff context from prior turns (`main_agent_token_session`, pending clarifications).

## Output Schema

- `status`: `conversation | planned | not_actionable | needs_clarification`
- `message` or conversational text response.
- Optional metadata for future research flow:
  - `knowledge_confidence` (0..1)
  - `research_required` (boolean)
  - `research_query` (string)
  - `sources` (list)

## Execution Steps

1. Interpret user goal in conversation mode.
2. Decide if request is:
   - direct conversation answerable from current context, or
   - information lookup question needing research.
3. If answerable with confidence, respond directly.
4. If confidence is low and research capability is available, run research lookup and answer with sources.
5. If research capability is unavailable, be explicit and ask whether user wants a best-effort answer or a follow-up later.
6. Never claim a tool action occurred unless execution actually ran.

## Clarification Rules

- Ask follow-up questions when user constraints are missing.
- Ask for direct command phrasing if user intent is executable but ambiguous.
- For ambiguous informational questions, ask one narrowing question before researching.
- If user asks "who are you / what can you do", answer from identity/persona/capability docs before generic fallback.

## Knowledge Confidence / Research Policy

- Treat these as likely research-required:
  - requests containing "latest", "today", "current", "news", "price", "recent", "release notes".
  - niche factual questions outside boot + critical skill context.
- If `knowledge_confidence < 0.70`, set `research_required=true`.
- Prefer model answer without research only when:
  - answer is timeless/basic and confidence is high.
- Research outputs should include:
  - concise answer,
  - 1-5 source links,
  - date awareness when relevant.
- Never fabricate sources.

## Duplicate / Conflict Handling

- No storage dedupe needed beyond normal interaction log behavior.

## Storage Contract

- Behavior prompt in markdown.
- Topic aggregate history in `conversation_topics`.
- Per-turn topic history in `conversation_topic_history`.
- File mirror in `data/skill_history/conversation/<user_id>/`:
  - `history.jsonl`
  - `topics_snapshot.json`

## Failure Behavior

- Return fallback conversational response when model/tooling is unavailable.
- If research fails, return a transparent fallback:
  - what was attempted,
  - what is still unknown,
  - one next-step question for the user.

## MicroJarvis Contract

### Micro functions that are allowed

- None.

### Escalation triggers to Main Jarvis

- All conversation requests route to Main Jarvis.

### Failure handoff payload to Main Jarvis

- Include baseline micro decision context for interpretability.

## Main Handoff Context Contract

- Include `main_agent_token_session` to preserve conversational continuity.
- Carry `last_successful_action` for continuity after tool turns.

## Learnability Checklist

- [x] Micro contract completed.
- [x] Failure handoff contract completed.
- [x] Main handoff context contract completed.
- [x] Deictic/pronoun follow-up behavior documented.
- [x] Micro failure -> main handoff continuity documented.
