# Main Turn Commitment Contract

## Purpose

Main may speak naturally, ask a follow-up, or use a registered skill. It may not promise future tool
work in ordinary conversation text. Before a production Main response leaves the model boundary, the
model must return one normalized turn decision:

- `conversation`: a complete prose response that requires no tool or future work.
- `clarify_action`: one allowlisted intent, partial entities, explicit missing fields, and a question.
- `execute_action`: one allowlisted intent, complete entities, and no missing fields.

This is a semantic model decision, not a growing router phrase table. The router remains responsible
for validation, authorization, persistence, execution, receipts, and user-visible success or failure.

## Execution boundary

For an action decision, the router verifies all of the following before dispatch:

1. The intent is part of the typed `MAIN_ACTION_INTENTS` vocabulary.
2. The current runtime capability projection documents the intent in `main_intents`.
3. The capability is currently configured and authorized for this user, agent, and channel.
4. Child/profile policy allows the action.
5. Domain context contracts report no required fields missing.
6. Main's confidence meets the configured low-confidence floor.

The domain handler repeats its own authorization and safety checks. A model decision cannot grant
permissions, choose an execution path, or bypass the domain policy.

The projection may include bounded, content-free intent contracts supplied by a domain handler. These
describe the purpose, read/write class, and accepted entity fields for similar intents. For example,
the email contract distinguishes collection summaries (`email.list_recent`) from a summary of one
identified message (`email.summarize`). These contracts improve semantic selection without putting
natural-language trigger phrases in the router.

Before selecting an intent, production Main loads the compact runtime contracts for candidate skills
that are both configured and authorized in the current catalog. This is intentionally broader than
post-classification on-demand loading because intent selection itself needs the skill semantics. It is
bounded to the projected skills and 64 candidate intents; restricted skills are not loaded as action
candidates.

The decision prompt requires a scope/cardinality audit before choosing among similar intents. Main
must compare semantic purposes, reject a lexical intent-name match that narrows or broadens the user's
request, and ask only for fields that preserve the requested operation. This is a general reasoning
rule, not an email phrase map.

## Bound clarification lifecycle

`clarify_action` creates a durable pending interaction with the intended skill, intent, partial
entities, missing fields, question, confidence, and short operational rationale. A later reply is
resolved against that pending action before Micro or the general conversation lane runs.

For example, a request to summarize email may be bound to `email.list_recent` while Main asks which
messages to include. A reply such as `all unread` supplies the stored `query`; it is not treated as a
new generic conversation. If the reply cannot be resolved, the pending action remains open and no
tool runs.

Pending completion always receives the current request context. Private-channel scopes and domain
authorization are therefore checked again on the follow-up turn.

## Failure behavior

- Invalid or unavailable typed model output fails closed with a retry message. Jarvis does not emit
  unconstrained future-tense action prose as a fallback.
- Unsupported, unconfigured, or unauthorized actions return the projected access note and do not run.
- Low-confidence action decisions ask the user to restate the action and scope.
- A tool result that still needs fields reuses the existing pending-interaction lifecycle.
- Every opened, denied, and executed commitment emits an inspectable event.

Legacy/test conversation backends that implement only `respond()` retain their previous behavior.
The production Ollama conversation backend implements the typed decision method.

GPT-OSS is prompted to return JSON and its output is parsed and normalized by Jarvis. Do not set
Ollama's transport-level `format: json` option for this model: the deployed GPT-OSS/Ollama combination
returns an empty response with that option even though it follows the same JSON instruction normally.
