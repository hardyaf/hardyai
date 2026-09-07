# Discord Command Envelope

## Contract

The Discord adapter decides whether an accepted message uses the optional UI prefix before stripping
that prefix. With the production prefix `!`:

| Discord input | Envelope text | Semantic lane |
| --- | --- | --- |
| `!what is on my calendar today` | `what is on my calendar today` | Main |
| `! what is on my calendar today` | `what is on my calendar today` | Main |
| `what is on my calendar today` | unchanged | Main |

The `/ask` context carries `command_prefix_explicit` and `discord_routing_lane=main`. Prefix state is
provenance only: it cannot grant a capability, change authorization, or select another model. Prefix
stripping happens only after the trusted adapter envelope exists.

Guild, channel, immutable user identity, role, child, and skill-scope authorization remain independent
deterministic checks. Direct API and command-pack callers enter the same Main boundary and fail closed
when the Main decision model is unavailable.

## Main commitment behavior

Every accepted semantic input starts from a neutral routing envelope and proceeds to Main's typed
turn-commitment inference. Main may return complete conversation, request a bound clarification, or
start a bounded typed-tool loop. The runtime capability catalog—not an old intent name or the prefix—
determines which tools are discoverable.

Pending clarification replies are resolved against the stored Main action binding. Current identity,
channel, capability, and policy checks run again before any tool execution. A pending record therefore
preserves context but never preserves authority.

## Capability projection

Before Main interprets a turn, the router builds an ephemeral projection from the active SQLite skill
registry and live domain state. It exposes only safe capability metadata, Main tool IDs/contracts, and
current configured/authorized status. It never exposes execution refs, storage refs, credentials, raw
SQL rows, restricted content, or full skill Markdown.

Backward-readable database values such as `micro_jarvis`, `micro_decision`, and legacy skill columns
remain compatibility history only. New turns, events, tickets, prompts, configuration, and compiled
artifacts do not write or activate them.

## Safety properties

- Main is the only semantic reasoning plane.
- Deterministic code owns authorization, approvals, schema validation, budgets, effects, and receipts.
- Child and protected-channel policy is rechecked on every tool call and resumed approval.
- Private-notes capture and scheduled operations retain their adapter/domain ownership.
- Disabled, unknown, stale, or non-projected operation names cannot become executable.
