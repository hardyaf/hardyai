# Jarvis Ubuntu Docker Runtime

This profile is the authoritative always-on Jarvis runtime for the current Ubuntu deployment.
Windows is not a production runtime.

It runs:

- Jarvis on Python 3.12 with one Uvicorn worker
- Ollama on the private Compose network with one NVIDIA GPU reservation
- SQLite in the checkout's ignored `data/` directory
- Discord inside the Jarvis process when explicitly enabled
- optional internal-only SearXNG conversational research profile
- optional delayed ticket-review and Plane projection worker profiles
- optional one-cycle manual Gmail mailbox worker profile for an external scheduler

The dashboard binds to loopback unless `JARVIS_BIND_ADDRESS` is explicitly set in the root `.env`.
Only use a private LAN address for this POC. Operator routes are authenticated, but the deployment is
not intended for direct public-Internet exposure.

Required deployment-only `.env` values:

```dotenv
JARVIS_UID=1001
JARVIS_GID=1001
JARVIS_BIND_ADDRESS=127.0.0.1
JARVIS_MICRO_MODEL=qwen2.5:7b
JARVIS_MAIN_MODEL=gpt-oss:20b
JARVIS_MODELS_ENABLED=false
JARVIS_DISCORD_ENABLED=false
JARVIS_OPERATOR_API_KEY=replace-with-a-strong-random-value
CALENDAR_GOOGLE_ENABLED=false
WEB_RESEARCH_ENABLED=false
```

Start the deterministic core first:

```bash
mkdir -p data secrets/live
chmod 700 data secrets secrets/live
chmod 600 .env secrets/live/discord_permissions.yaml
docker compose --env-file .env -f deploy/docker/compose.yaml up -d --build
docker compose --env-file .env -f deploy/docker/compose.yaml ps
```

Pull the lightweight routing model and the larger main model, then set
`JARVIS_MODELS_ENABLED=true`:

```bash
docker compose --env-file .env -f deploy/docker/compose.yaml exec ollama ollama pull qwen2.5:7b
docker compose --env-file .env -f deploy/docker/compose.yaml exec ollama ollama pull gpt-oss:20b
docker compose --env-file .env -f deploy/docker/compose.yaml up -d jarvis
```

The runtime constrains Ollama to two loaded models and one request per model.
Micro uses Qwen 2.5 7B and Main uses GPT-OSS 20B on the 24 GB RTX 3090. Explicit
per-lane context allocations must be verified with `ollama ps` after deployment;
do not infer residency from the model's advertised maximum context.

Discord uses an explicit model-entry boundary. With the production
`DISCORD_COMMAND_PREFIX=!`, only `!phrase` or `! phrase` enters MicroJarvis.
Allowed unprefixed messages still receive a response, but they bypass Micro and
go to Main for conversation, action repair, or typed planning. The adapter records
the distinction as `micro_command_explicit`; a missing value fails closed to Main.

To enable conversational web research, generate a `SEARXNG_SECRET_KEY`, set
`WEB_RESEARCH_ENABLED=true`, and start the `research` profile:

```bash
docker compose --env-file .env -f deploy/docker/compose.yaml --profile research up -d
```

The SearXNG service has no published host port. See
`docs/web-research.md` for the data-flow, child-safety default, verification,
and current snippets-only limitation.

To enable Google Calendar, install `google_permissions.yaml` and the OAuth
client credentials under `secrets/live/`, but keep the refreshable token store
on the writable `data/` mount. In `google_permissions.yaml`, set:

```yaml
oauth:
  client_credentials_file: "google_client_credentials.json"
  token_store_path: "data/google_tokens.json"
  scopes:
    - "https://www.googleapis.com/auth/calendar.readonly"
    - "https://www.googleapis.com/auth/calendar.events"
    - "https://www.googleapis.com/auth/gmail.readonly"
```

Copy an existing token store to `data/google_tokens.json` (or complete OAuth
interactively before starting the headless container), restrict all credential
files, set `CALENDAR_GOOGLE_ENABLED=true`, and recreate Jarvis:

```bash
chmod 600 secrets/live/google_permissions.yaml \
  secrets/live/google_client_credentials.json data/google_tokens.json
docker compose --env-file .env -f deploy/docker/compose.yaml up -d jarvis
```

The `secrets/live` mount remains read-only. Google access tokens are short-lived,
so the token store must be writable for refreshes to survive container restarts.
Calendar and conversational Gmail reads share the house token store. Every refresh
must preserve the full configured scope superset; a Gmail-only refresh would mint a
temporarily downscoped access token and cause Calendar reads to return HTTP 403 until
another full-scope refresh. Keep all three scopes above in the protected policy.

For hourly invitation/forwarded-`.ics` reconciliation, enable the Gmail API for the OAuth project,
reauthorize the house token with the Gmail Readonly scope, then set:

```dotenv
CALENDAR_INBOX_ENABLED=true
CALENDAR_INBOX_TIMEZONE=America/New_York
CALENDAR_INBOX_START_HOUR=8
CALENDAR_INBOX_END_HOUR=20
```

The worker scans at most 100 messages per hourly run by default, derives allowed senders from non-house
calendar bindings unless explicitly overridden, never changes Gmail state, and never backfills mail from
before its first activation.

For the shared read-only email agent, first create the protected permissions file on the host:

```bash
python scripts/configure_email_agent.py \
  --permissions-template deploy/ubuntu/email_agent_permissions.example.yaml \
  --google-account-key house \
  --discord-permissions-file secrets/live/discord_permissions.yaml \
  --discord-guild-id REPLACE_WITH_DISCORD_GUILD_ID \
  --discord-channel-id REPLACE_WITH_DISCORD_CHANNEL_ID \
  --discord-external-user-id REPLACE_WITH_DISCORD_USER_ID \
  --enable-sync
chmod 600 secrets/live/email_agent_permissions.yaml
```

Add an exact `skill.email.agent` user/channel entry to `secrets/live/discord_permissions.yaml`, then
reauthorize the house Google account with Gmail Readonly using
`python scripts/authorize_calendar_inbox.py`. The Compose service pins attachment extraction, label
writes, remote models, and historical backfill off. It mounts the protected permissions through the
existing read-only `secrets/live` volume and stores only bounded metadata, summaries, classifications,
sync state, and channel-scoped E-number references in the Jarvis SQLite database.

Before production use, send one harmless canary to each configured plus-address route and confirm it
appears through Discord. Add each additional user's exact immutable ID and channel scope only in the
protected permission files.

Manual Spam and read-and-complete changes use a separate Gmail Modify token that is never mounted in the
Jarvis API container. Legacy `email-spam` service and setting names are retained for compatibility.
Authorize it on the host into `secrets/email-spam-worker/token.json`, enable
`EMAIL_AGENT_SPAM_WRITES_ENABLED=true`, and have a bounded host scheduler invoke one worker cycle at a
time. For example:

```bash
mkdir -p secrets/email-spam-worker
chmod 700 secrets/email-spam-worker
python scripts/authorize_email_spam_writer.py
chmod 600 secrets/email-spam-worker/token.json
docker compose --env-file .env -f deploy/docker/compose.yaml --profile email-spam \
  run --rm email-spam-worker
```

The worker accepts only already-queued immutable Gmail message IDs. Its fixed operations either apply
`SPAM` and remove `INBOX`, or remove `UNREAD`; every operation is verified by read-back. It has no HTTP
endpoint or model access. The
Compose service deliberately exits after one bounded cycle; schedule repeated invocations externally.

After ticket capture has been inspected, start the optional workers with:

```bash
docker compose --env-file .env -f deploy/docker/compose.yaml --profile tickets up -d
docker compose --env-file .env -f deploy/docker/compose.yaml --profile plane up -d
```

See `docs/action-ticket-verification.md` for rollout order, Plane privacy defaults,
operator authentication, and backup/restore commands.

After the model-backed smoke test passes, set
`JARVIS_DISCORD_ENABLED=true` and recreate Jarvis:

```bash
docker compose --env-file .env -f deploy/docker/compose.yaml up -d jarvis
docker compose --env-file .env -f deploy/docker/compose.yaml logs --tail=100 jarvis
```

Run the preflight inside the same container/network context:

```bash
docker compose --env-file .env -f deploy/docker/compose.yaml exec jarvis \
  python scripts/verify_install.py \
  --require-models \
  --require-discord \
  --probe-models \
  --api-url http://127.0.0.1:8000 \
  --smoke-turn \
  --timeout-seconds 120
```
