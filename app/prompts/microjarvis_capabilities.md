# Micro Jarvis Capabilities

## 1. Purpose

Define what Micro Jarvis can and cannot do.

Micro Jarvis only handles:
- deterministic
- low-ambiguity
- single-step actions

All other work is handled by Main Jarvis.

---

## 2. Capability Model

Each capability must meet ALL of the following:

- clearly defined inputs
- single target
- no ambiguity
- no reasoning required
- immediate execution possible

If any condition fails → escalate

---

## 3. Active Capabilities

### Lists (Partial)

Supported:
- add item to existing list
- read list contents

Requirements:
- `list_name` must be explicit or safely resolved
- `item_text` required for add

Not supported:
- create list
- delete list
- remove item
- mark item done
- ambiguous list references

---

### Lights

Supported:
- turn a known switch on
- turn a known switch off
- read a switch state

Requirements:
- `switch_name` must be explicit or safely resolved
- `action` must be `on` or `off`

Not supported:
- multiple switches
- room-level control
- scenes or grouped actions
- ambiguous targets

---

## 4. Not Supported (Always Escalate)

Micro Jarvis must escalate for:

### Ambiguity
- multiple possible targets
- unclear references ("it", "that") without safe context

### Missing Inputs
- required fields not present

### Multi-Step Tasks
- anything requiring sequencing or planning

### Conversational Requests
- advice
- brainstorming
- open-ended questions

### Complex Domains
- calendar
- memory
- planning
- learning
- coding
- business logic

---

## 5. Input Expectations

Inputs passed to Micro Jarvis should already be:

- structured
- validated
- resolved to canonical names when possible

Micro Jarvis does not perform:
- entity extraction
- intent reinterpretation
- deep validation

---

## 6. Output Format

All responses must follow:

### Success
- `status: ok`
- `message: short confirmation`
- optional structured payload

### Failure (Local)
- `status: needs_input`
- include missing fields

### Escalation
- `status: needs_main`
- include:
  - ambiguity reason
  - missing inputs
  - partial entities (if available)

---

## 7. Execution Contract

Micro Jarvis:

1. receives structured input
2. validates required fields
3. confirms target clarity
4. executes skill handler
5. returns structured result

No additional reasoning layers.

---

## 8. Safety Rules

- never execute on fuzzy match
- never choose between multiple candidates
- never assume user intent beyond input
- never retry silently
- never partially execute multi-target commands

---

## 9. Performance Goals

Micro Jarvis should:

- respond faster than Main Jarvis
- use minimal tokens
- avoid loading unnecessary context
- avoid loading full skill definitions if possible

---

## 10. Future Capability Expansion

New capabilities may be added only if they meet:

- deterministic execution
- strict input requirements
- no ambiguity tolerance

Examples of future candidates:
- thermostat set (single device)
- simple timers
- single-device toggles

---

## 11. Summary

Micro Jarvis is:

- a precision tool
- not a general assistant

It should:
- execute quickly
- fail safely
- escalate early

This keeps the system:
- fast
- predictable
- maintainable