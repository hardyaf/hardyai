# Agent Registry

## Purpose
Define available agents and when to use them.

## Agents

### Micro Jarvis
- Purpose: Fast, deterministic execution
- Handles:
  - lights
  - lists (simple)
- Limitations:
  - no reasoning
  - no ambiguity handling

### Main Jarvis
- Purpose: Reasoning and orchestration
- Handles:
  - complex tasks
  - ambiguous input
  - multi-step workflows

## Future Agents
- Calendar Agent
- Email Agent
- Finance Agent

## Rules
- Use micro when safe and deterministic
- Use main when ambiguity exists
- Jarvis remains responsible for final outcome
