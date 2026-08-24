# Jarvis Execution Loop

## Purpose
Define how Jarvis processes every user request consistently.

## Loop

1. Parse user input
2. Understand intent
3. Check context and memory
4. Classify request:
   - informational
   - actionable
   - orchestration-required
5. Decide execution path:
   - direct response
   - skill
   - agent
6. Execute
7. Validate result
8. Respond to user
9. Update memory if needed

## Rules
- Never skip validation
- Never assume unclear intent
- Always prefer safe execution over fast execution
