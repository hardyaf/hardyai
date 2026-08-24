# Discord Micro Command Envelope

## Contract

The Discord adapter decides whether an accepted message is eligible for MicroJarvis before it strips
the configured prefix. With the production prefix `!`:

| Discord input | Envelope text | Routing lane |
| --- | --- | --- |
| `!what is on my calendar today` | `what is on my calendar today` | Micro eligible |
| `! what is on my calendar today` | `what is on my calendar today` | Micro eligible |
| `what is on my calendar today` | unchanged | Main only |

The `/ask` context carries both `micro_command_explicit` and the inspectable
`discord_routing_lane`. The router trusts only the boolean value `true` to admit Discord text to
Micro; a missing, false, or non-boolean value bypasses Micro and emits `pipeline.micro.bypassed`.

This is a routing boundary, not a channel permission boundary. Guild, channel, user, role, child,
and skill-scope authorization still runs independently.

## Main handoff behavior

Unprefixed Discord input becomes a synthetic `unknown` decision owned by Main. Main may answer it as
conversation, repair it into one allowlisted action, or produce a typed bounded plan. The synthetic
decision intentionally cannot execute a tool directly.

An explicit bang command that Micro cannot resolve follows the normal Micro -> Main failure handoff.
The handoff includes intent, confidence, entities, ambiguity flags, required missing fields, agent
identity, recent turns, the condensed session summary, and domain-specific working-context hints.

Pending clarification turns retain the boundary. An unprefixed answer is interpreted by Main against
the pending action; Micro is not called merely to probe whether the user changed topics. A prefixed
reply may enter Micro as a new explicit command.

Main conversation inference has its own typed commitment boundary after this handoff. It chooses a
complete `conversation`, a skill-bound `clarify_action`, or an `execute_action`. A clarification stores
the chosen intent and missing fields, so a terse reply such as `all unread` completes the same action
instead of falling into generic conversation. Main cannot promise to fetch or change something in
conversation mode; only a validated action envelope can reach the dispatcher.

## Main capability projection

Before Main interprets a turn, the router builds an ephemeral capability projection from the active
skills in SQLite. Membership is selected deterministically from the current user and agent. Each
domain handler may then add content-free runtime state such as `configured`, `authorized_here`, an
availability label, and a safe access note for the current request context.

The projection exposes only an allowlisted summary: skill ID/name, documented intents, executable
Main intents, executable Micro intents, scheduler presence, and current availability. Unknown or stale
intent names remain non-executable even if an older SQL row still documents them. It never exposes execution
paths, storage references, credentials, raw SQL rows, or full skill markdown. Main uses the broad
projection to interpret the request; after selecting an intent, normal router and domain authorization
still run before execution.

Main owns capability questions for both model lanes. It may explain which explicit `!` commands Micro
can execute and which actions belong to Main. It must not present documented-only intents as live
actions. Micro does not narrate or reinterpret its
own configuration.

The email skill demonstrates the scope distinction. Main can say that shared email is supported while
also reporting that it is available only in an authorized private email channel. The projection does
not broaden the grant, and the email domain repeats the authorization check during execution.

## Safety properties

- Prefix state is captured before prefix stripping, so downstream code never guesses from normalized text.
- Discord API calls missing the envelope marker fail closed to Main.
- Child conversation-only policy is checked again after Main action repair and before tool execution.
- Private-notes capture remains an adapter-owned no-response path and does not enter `/ask`.
- Other interfaces retain their existing routing behavior; this contract is Discord-specific.
- Capability metadata is advisory context for Main; it cannot grant access or bypass domain policy.
- A Main action commitment is advisory until the router validates the scoped capability projection,
  required fields, identity policy, and confidence.
- Follow-up execution receives the current Discord request context, so an earlier pending interaction
  cannot retain or manufacture private-channel authorization.
