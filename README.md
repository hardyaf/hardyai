# Jarvis

Jarvis is a modular household assistant platform. It is not a coding-agent-only project: the current
POC coordinates conversation, lists, calendars, home controls, email triage, private notes, bounded
web research, session memory, and action verification through registered skill domains.

The v0 deployment works. The present engineering focus is a safe first-pass agent loop with explicit
authorization, inspectable state, bounded concurrency, durable writes, and clean separation between
Micro, Main, skills, sessions, memory, and adapters.

## Current runtime decisions

- The authoritative runtime is one always-on Ubuntu 24.04 server using
  [`deploy/docker/compose.yaml`](deploy/docker/compose.yaml). Windows scripts and scheduled tasks are
  rollback-only legacy material and are not part of new development or the clean source export.
- Every production Compose command must include `--env-file .env`; otherwise model, Discord, and bind
  settings can silently fall back to disabled or loopback defaults.
- The production GPU profile is sized for an NVIDIA RTX 3090 with 24 GB VRAM.
- MicroJarvis uses `qwen2.5:7b`. It is an explicit, fast command classifier/executor.
- Main repair and conversation use `gpt-oss:20b`. Main owns general conversation, semantic action
  repair, clarification, and typed multi-step plans.
- SQLite is authoritative for sessions, skills, domain state, action tickets, and the durable job
  ledger. Runtime data and protected configuration never belong in Git.
- Optional web research is SearXNG-only, snippets-only, read-only, bounded, and disabled by default.
- Action-ticket verification and autonomous remediation are feature-flagged off by default.

## Request and authorization boundaries

Discord has two deliberate lanes:

- A message beginning with the configured prefix (`!` in production) is an explicit Micro command.
  Both `!phrase` and `! phrase` are accepted.
- An accepted message without the prefix bypasses Micro and enters Main as an `unknown` handoff.
- The Discord adapter records `micro_command_explicit`; the router fails closed to Main when that
  trusted field is absent or false.
- Prefix removal happens only after the adapter creates the command envelope.

The embedded Discord adapter calls the bounded in-process turn service. It does not make a loopback
HTTP request to `/ask`.

Operator routes require `JARVIS_OPERATOR_API_KEY` in production. A valid header can create a
short-lived, signed, HttpOnly dashboard session; cookie-authenticated mutations additionally require a
CSRF token. HTTP callers cannot forge Discord identity, child policy, channel authorization, or skill
scope fields.

Skill execution is registry-only and fails closed. A model decision, caller payload, pending record,
or remembered context never grants authority. Deterministic code checks the active user, agent,
channel, child policy, skill configuration, required fields, and confidence immediately before domain
execution.

## Turn and write guarantees

The API uses a bounded turn service with:

- fixed maximum concurrency and queue capacity;
- per-session/channel serialization;
- an execution timeout; and
- synchronous router work moved off the async event loop.

Interaction-memory writes use the durable SQLite job ledger. The API durably enqueues a versioned,
idempotent write before responding, and reports `delivery.memory` as `queued`, `committed`, or
`failed`. The worker recovers leased or pending work after restart and uses bounded retry/dead-letter
behavior. Domain writes that return success are committed synchronously.

## Architecture

```text
app/api/                 HTTP routes, trusted principals, operator auth, security headers
app/core/                request pipeline, Micro/Main routing, planning, session state
app/skills/              registry, capability projection, execution dispatcher, domain packages
app/services/            bounded turn service, durable writes, adapters, schedulers, integrations
app/db/                  SQLite schema, migrations, and persistence
app/prompts/             public identity, persona, and skill contracts
deploy/docker/           authoritative Compose deployment profile
scripts/                 configuration, verification, and clean-export utilities
tests/                   unit and integration characterization tests
```

New skill behavior belongs in `app.skills.domains.*` plus its Markdown contract. Do not restore legacy
`app/skills/handlers/*` paths or compensate for model misses with router phrase branches.

## Local development

Python 3.12 is the supported runtime.

```text
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
copy .env.example .env
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

On Linux, use `.venv/bin/python` and `cp`. Models and external integrations are disabled in the
example configuration. In production, set a strong random `JARVIS_OPERATOR_API_KEY` before startup.

Run the verification gates:

```text
python scripts/check_public_tree.py --root .
python -m ruff check app scripts tests --select E9,F63,F7,F82
python -m pip_audit --requirement requirements.txt --strict
python -m compileall -q app scripts tests
python -m pytest -q
```

## Docker deployment

Create `.env` from `.env.example`, keep it untracked, and set at least the UID/GID, bind address,
operator key, and desired integration flags. Pull the selected models once:

```text
docker compose --env-file .env -f deploy/docker/compose.yaml up -d ollama
docker compose --env-file .env -f deploy/docker/compose.yaml exec ollama ollama pull qwen2.5:7b
docker compose --env-file .env -f deploy/docker/compose.yaml exec ollama ollama pull gpt-oss:20b
docker compose --env-file .env -f deploy/docker/compose.yaml up -d --build
docker compose --env-file .env -f deploy/docker/compose.yaml ps
```

Do not expose the POC directly to the public Internet. Use a private network and retain the operator
authentication boundary even on trusted LANs.

## Protected integrations

Only templates and reserved example values belong in source control. Live OAuth files, tokens,
mailboxes, Discord IDs, identity mappings, contact names, calendars, and household profiles belong in
ignored protected configuration.

For the email agent, copy and edit the example taxonomy first. The configurator deliberately refuses
to invent household routes, categories, or users:

```text
python scripts/configure_email_agent.py \
  --permissions-template deploy/ubuntu/email_agent_permissions.example.yaml \
  --google-account-key house \
  --discord-permissions-file secrets/live/discord_permissions.yaml \
  --discord-guild-id REPLACE_WITH_GUILD_ID \
  --discord-channel-id REPLACE_WITH_CHANNEL_ID \
  --discord-external-user-id REPLACE_WITH_USER_ID \
  --enable-sync
```

Calendar contact suggestions likewise come from the protected permissions file; no household names or
addresses are compiled into the application.

## Clean-history repository workflow

The legacy repository remains a private forensic and rollback source. Do not publish it after merely
deleting sensitive files: historical commits remain reachable.

Create the sanitized source repository from the current worktree:

```text
python scripts/export_clean_repo.py ../Jarvis-clean --init-git
```

The exporter uses an explicit allowlist, omits logs, data, secrets, internal worklogs, the reference
repository, Windows runtime remnants, and private deployment notes, then runs the publication checker
before it can initialize a new one-commit history. Keep the new remote private until CI passes and a
separate credential-rotation checklist is complete.

## Safety defaults

- No destructive household action without an approval gate.
- No unbounded loops, queues, retries, plans, research, or worker claims.
- Child conversation-only identities cannot execute household actions.
- Web evidence is untrusted context and cannot invoke skills.
- Raw ticket and identity APIs require the operator boundary.
- Credentials found in any Git history must be rotated; history cleanup is not revocation.

See [`SECURITY.md`](SECURITY.md) for reporting and publication rules.
