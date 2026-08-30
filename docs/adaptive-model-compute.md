# Adaptive Model Compute Budget

Status: implemented for every Ollama-backed model lane; PaddleOCR-VL uses its bounded quality ceiling because its provider does not expose a reliable token-exhaustion signal.

## Policy

Configured output-token counts are efficient starting points, not cost or quality ceilings. When Ollama reports `length`/token-limit completion, or the observed generation count reaches the requested allowance, the same model call is retried with a larger output budget. The default sequence doubles the budget for at most four total attempts and never exceeds eight times the lane's starting budget. A repeated exhaustion at that boundary is treated as a failed loop and returns through the lane's existing failure contract.

The policy applies to Micro classification, Main repair, Main conversation/turn commitment, research decisions, email classification, email summaries, and action-ticket review. It does not retry network, authorization, or provider failures as token problems.

PaddleOCR-VL currently receives 4,096 `max_new_tokens` up front. Its pipeline API does not return a dependable generated-token count or stop reason, so speculative reruns would waste accelerator availability without proving truncation. This is compatibility debt: if the provider exposes a trustworthy exhaustion signal, it should adopt the same adaptive policy.

## Reuse map

| Concern | Decision | Authority |
|---|---|---|
| Model sizing and metrics | Adapt | Existing `OllamaCallObserver` owns the shared bounded retry policy and content-free metrics. |
| Escalation history | Reuse | `EventLogService` records `model.compute_budget.escalated`. |
| Private feedback delivery | Adapt | Existing private-notes channel configuration identifies one protected delivery channel. |
| Deferred delivery | Reuse | The shared durable-job ledger owns claims, leases, retries, receipts, dead letters, and restart recovery. |
| Dashboard visibility | Adapt | Existing operator status response exposes the latest escalation and recent count. |

## Data ownership

| Datum | Authoritative owner | Contents and lifecycle |
|---|---|---|
| Per-attempt inference metric | Event log | Lane, model, budgets, counts, timings, outcome, and stop reason. No prompt or response content. Retention follows the existing event policy. |
| Private notice delivery job | Durable job ledger | Content-free escalation identity and budget transition. Completed after a Discord provider receipt; bounded retry/dead-letter rules apply. |
| Private notice destination | Protected Discord permissions | Exactly one `private_notes_channels` entry may opt in with `compute_budget_notices: true`. The public tree contains examples only. |
| Runtime status projection | Dashboard | Derived from recent event-log rows; it is not a second store. |

## Operator signals

- `model.ollama_call`: every bounded attempt.
- `model.compute_budget.escalated`: each automatic budget increase. Private delivery is consolidated to the first increase per logical call.
- `model.compute_budget.failed_loop`: every bounded attempt exhausted; this receives its own private failure notice.
- `model.compute_budget.notice_enqueue_failed`: telemetry was recorded, but the private delivery job could not be persisted.
- `last_sequence_metrics.failed_loop=true`: the lane exhausted every bounded attempt.

Frequent escalation in one lane is a tuning signal: shorten an oversized prompt, correct an overly verbose schema, raise that lane's efficient starting point, or repair a loop that repeatedly produces unusable output. It is not a cost alarm.

## Rollback

Set `MODEL_ADAPTIVE_TOKEN_BUDGET_ENABLED=false` to return every Ollama lane to a single fixed-budget attempt. Private notices can be disabled independently by removing `compute_budget_notices: true` from the protected private-channel entry. Event history and completed delivery receipts remain inspectable.
