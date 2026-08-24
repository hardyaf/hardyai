# Action ticket verification operations

Jarvis now owns a local action-ticket ledger and delayed correctness queue. Plane is an optional
projection; it is never the execution database or evidence that a household action succeeded.

## Safe rollout

Use the feature in stages. Start with capture and inspect tickets before enabling delayed model
reviews or repairs:

```dotenv
ACTION_TICKETS_ENABLED=true
ACTION_TICKET_CAPTURE_MODE=shadow
ACTION_TICKET_REVIEW_ENABLED=false
ACTION_TICKET_AUTO_REMEDIATION_ENABLED=false
PLANE_ENABLED=false
JARVIS_OPERATOR_API_KEY=generate-a-long-random-secret
```

Then enable reviews. A review is due one hour after the action's last material completion by default.
The worker waits for a quiet live-inference window and processes a bounded batch sequentially:

```dotenv
ACTION_TICKET_REVIEW_ENABLED=true
ACTION_TICKET_REVIEW_DELAY_SECONDS=3600
# Reconcile a captured/executing request if Jarvis stops before recording a receipt.
ACTION_TICKET_EXECUTION_WATCHDOG_SECONDS=300
ACTION_TICKET_REVIEW_POLL_SECONDS=10
ACTION_TICKET_REVIEW_LIVE_IDLE_SECONDS=15
ACTION_TICKET_REVIEW_BATCH_SIZE=1
ACTION_TICKET_REVIEW_MAX_ATTEMPTS=3
ACTION_TICKET_REVIEW_MODEL_PROVIDER=ollama
# Blank means MAIN_REPAIR_MODEL_NAME.
ACTION_TICKET_REVIEW_MODEL_NAME=
ACTION_TICKET_REVIEW_MODEL_TIMEOUT_SECONDS=180
ACTION_TICKET_REVIEW_MODEL_NUM_CTX=12288
ACTION_TICKET_REVIEW_MODEL_NUM_PREDICT=1024
ACTION_TICKET_REVIEW_CONTEXT_MAX_CHARS=32000
ACTION_TICKET_AUTO_REMEDIATION_ENABLED=false
```

Only Lists `create_list` and `add_item` repairs are currently allowlisted for autonomous remediation.
They use child tickets, stable idempotency keys, a maximum generation, and the same delayed review.
Leave remediation disabled until capture and review behavior has been observed on disposable lists.

## Source guarantees

- Lists are verified from the SQLite list and item rows.
- Google Calendar creates are verified by calendar ID and Google event ID using a non-interactive
  provider read. Missing credentials produce `UNVERIFIABLE`, never a guessed success.
- The local calendar remains in-memory, so its tickets are explicitly `UNVERIFIABLE`.
- Home/light verification reads only the simulated SQLite switch state and labels that limitation;
  it does not claim a physical bulb changed.
- Unsupported capabilities remain visible as `UNVERIFIABLE` or reconciliation work.

Execution messages and ticket transcripts establish intent but are never correctness proof.

## Workers

For native Ubuntu/systemd:

```bash
sudo bash deploy/ubuntu/install-ticket-workers-systemd.sh --repo-root /absolute/path/to/Jarvis --user codex
systemctl status jarvis-ticket-review jarvis-plane-sync
journalctl -u jarvis-ticket-review -f
```

The installer enables only workers whose feature flags are true. For Compose:

```bash
docker compose --env-file .env -f deploy/docker/compose.yaml --profile tickets up -d
docker compose --env-file .env -f deploy/docker/compose.yaml --profile plane up -d
```

`/health` exposes non-sensitive queue counts and worker heartbeats. Raw transcripts, evidence,
identity bindings, and reconciliation controls require `X-Jarvis-Operator-Key`:

```bash
curl -H "X-Jarvis-Operator-Key: $JARVIS_OPERATOR_API_KEY" http://127.0.0.1:8000/tickets
curl -H "X-Jarvis-Operator-Key: $JARVIS_OPERATOR_API_KEY" http://127.0.0.1:8000/tickets/jobs
```

Do not put this key in browser JavaScript or expose these endpoints without a trusted access gate.

## Plane projection

Create a dedicated Plane project with its normal Backlog/Started/Completed-style states, then set:

```dotenv
PLANE_ENABLED=true
PLANE_API_BASE_URL=https://plane.example.test
PLANE_API_KEY=private-api-key
PLANE_WORKSPACE_SLUG=household
PLANE_PROJECT_ID=project-uuid
PLANE_SYNC_RAW_TRANSCRIPT=false
PLANE_API_TIMEOUT_SECONDS=30
```

Jarvis uses Plane's REST API, includes the local ticket ID as `external_id`, and reconciles that ID
before retrying an ambiguous create. Raw family transcripts are disabled by default. Plane outages
retry independently and do not roll back completed household actions.

## Discord child identities

Discord payloads use the immutable numeric author ID, stable message ID, and a per-user channel
session. Bindings are managed through the protected operator API. Two generic persona documents ship
as starting points: `kid_spark` and `kid_quest`.

Example binding:

```bash
curl -X PUT \
  -H "X-Jarvis-Operator-Key: $JARVIS_OPERATOR_API_KEY" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8000/operator/identities \
  -d '{"source":"discord","external_user_id":"123456789","external_display_name":"Kid","user_id":"kid-one","agent_id":"kid_spark","age_band":"6-8","presentation_profile":"child_simple","policy_profile":"child_conversation_only","active":true}'
```

`child_conversation_only` blocks household tool execution and cancels any stale pending action before
it can be completed. `child_simple` removes process/debug labels from the delivered assistant text.
Persona prompts cannot expand the policy allowlist.

When action ticketing is enabled, raw `/events`, `/memory/recent`, and session-context exports also
require the operator key. The dashboard remains usable for commands and safe status summaries, but
its deep raw trace panel needs an authenticated operator client or trusted proxy.

## Backup, integrity, and guarded restore

The backup command uses SQLite's online backup API, so WAL state is captured consistently:

```bash
.venv/bin/python scripts/manage_database.py backup --destination ./data/backups
.venv/bin/python scripts/manage_database.py verify
```

Stop Jarvis and both workers before restoring. Restore requires an explicit flag and preserves the
existing target as a timestamped pre-restore copy:

```bash
sudo systemctl stop jarvis-ticket-review jarvis-plane-sync jarvis
.venv/bin/python scripts/manage_database.py --database ./data/jarvis_v2.db \
  restore ./data/backups/jarvis_v2-YYYYMMDDTHHMMSSZ.sqlite3 --replace
.venv/bin/python scripts/manage_database.py verify
sudo systemctl start jarvis jarvis-ticket-review jarvis-plane-sync
```

Never run tests against the service database. `tests/conftest.py` forces a disposable database before
runtime import; custom test commands must preserve that isolation.
