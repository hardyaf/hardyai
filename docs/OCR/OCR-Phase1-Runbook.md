# OCR Phase 1 operations runbook

This runbook activates only the secure Paperless archive bridge described in `OCR-Plan.md`. Use synthetic documents until every acceptance and restore check below passes on the authoritative Ubuntu host.

## 1. Stop conditions

Do not deploy if any of these are unresolved:

- the selected storage root is not backed by LUKS2, is not mounted at boot before Docker, or lacks tested key recovery;
- the backup destination is not encrypted and capacity-tested;
- Docker/Compose or the pinned images are unavailable locally;
- separate non-admin Paperless archive and read users/tokens cannot be created;
- host-local internal-bridge/SSH access is insufficient but a gateway TLS name/certificate has not been selected;
- the current core database has not been backed up.

## 2. Provision host storage and secrets

Example layout (confirm the pinned images' PostgreSQL/Valkey UIDs first; this example uses UID 999 and deployment UID/GID 1001):

```bash
sudo install -d -m 0700 -o 999 -g 1001 \
  /mnt/hardyai-documents/paperless/{valkey,postgres}
sudo install -d -m 0700 -o 1001 -g 1001 \
  /mnt/hardyai-documents/paperless/{data,media,export} \
  /mnt/hardyai-documents/jarvis/spool \
  /mnt/hardyai-documents/control \
  /mnt/hardyai-documents/backups \
  /mnt/hardyai-documents/restore-drills
sudo install -d -m 0700 -o 1001 -g 1001 /etc/hardyai/documents
sudo install -m 0440 -o root -g 1001 /dev/null /etc/hardyai/documents/paperless_db_password
sudo install -m 0600 -o 1001 -g 1001 /dev/null /etc/hardyai/documents/paperless_secret_key
sudo install -m 0600 -o 1001 -g 1001 /dev/null /etc/hardyai/documents/paperless_archive_token
sudo install -m 0600 -o 1001 -g 1001 /dev/null /etc/hardyai/documents/paperless_read_token
sudo install -m 0600 -o 1001 -g 1001 /dev/null /etc/hardyai/documents/paperless_read_user_id
sudo install -m 0600 -o 1001 -g 1001 /dev/null /etc/hardyai/documents/jarvis_operator_api_key
```

Populate the database password, Paperless secret key, and operator key with independent random values. The service-account bootstrap below writes both tokens and the read-user ID. Do not put secret values in `.env`, shell history, tickets, logs, or this repository. Add these non-secret settings to the deployment `.env`:

```dotenv
DOCUMENTS_STORAGE_ROOT=/mnt/hardyai-documents
DOCUMENTS_SECRETS_ROOT=/etc/hardyai/documents
PAPERLESS_POSTGRES_UID=999
PAPERLESS_VALKEY_UID=999
DOCUMENTS_MIN_FREE_BYTES=1073741824
PAPERLESS_SERVER_VERSION=3.0.5
PAPERLESS_API_VERSION=10
```

Confirm the mount before Docker starts:

```bash
findmnt -T /mnt/hardyai-documents
lsblk -f
```

## 3. Build and initialize Paperless

Every Compose command must include `--env-file .env`.

```bash
docker compose --env-file .env -f deploy/docker/compose.yaml build jarvis
docker compose --env-file .env -f deploy/docker/compose.yaml --profile documents \
  up -d paperless-db paperless-broker paperless-webserver
```

Create the two non-admin service users and rotate their tokens without creating an administrator:

```bash
bash scripts/bootstrap_paperless_service_accounts.sh
```

The archive user receives only add/change/view document and task-view permissions; change is needed solely to grant the read user object-level view permission after archival. The read user receives view/search/download only. The script stores distinct tokens and the numeric read-user ID in their named `0600` files without printing token values. Do not publish the Paperless port or mount a consume directory.

Start the HardyAI processes:

```bash
docker compose --env-file .env -f deploy/docker/compose.yaml --profile documents \
  up -d --no-build document-worker document-gateway
docker compose --env-file .env -f deploy/docker/compose.yaml --profile documents ps
```

The gateway is ready only when storage, free-space floor, Unix socket, Paperless authentication, API v10, and server v3.0.5 all pass. Docker intentionally suppresses published ports on `internal` networks, so resolve a current internal address from the host and preserve a loopback Host header; the endpoint remains unpublished and operator-authenticated:

```bash
gateway_ip="$(docker inspect jarvis-poc-document-gateway-1 --format '{{range .NetworkSettings.Networks}}{{println .IPAddress}}{{end}}' | head -n1)"
curl --fail --resolve "localhost:8010:${gateway_ip}" http://localhost:8010/documents/ready
python scripts/verify_install.py --documents-only
```

## 4. Synthetic acceptance

Use a generated PDF/JPEG/PNG containing no real personal data. Authenticate locally, upload once, poll `GET /documents/{document_id}`, search with `GET /documents/search?query=...`, and download `GET /documents/{document_id}/source`. Verify the downloaded SHA-256 matches the input.

Repeat the same upload and confirm the returned document ID is unchanged. Terminate the worker after Paperless accepts a task, restart it, and confirm one ready mapping. Stop Paperless during an upload and confirm the spool remains and the job becomes retry/dead-letter rather than losing the source. Inspect core `durable_jobs` to ensure its payload contains only opaque IDs and the SHA-256—not a filename, title, body, OCR text, or token.

## 5. Denied-egress and cold-start test

The three document networks must report `Internal: true`; Paperless DB/Valkey must join data only, Paperless web edge+data only, worker edge only, and gateway control+edge only. From every document container, DNS resolution or TCP connection to a public canary must fail. Then:

```bash
docker compose --env-file .env -f deploy/docker/compose.yaml --profile documents down
docker compose --env-file .env -f deploy/docker/compose.yaml --profile documents \
  up -d --pull never --no-build
```

Repeat upload/search/retrieval without enabling internet. `OFFLINE_MODE=true` must reject any accidental Discord, Google, email, Plane, web-research, or remote-model setting before a document process starts.

## 6. Coordinated backup

The backup command briefly stops all document writers and core Jarvis after running the Paperless exporter. It always uses the checked-in Compose file and requires a new generation directory inside the encrypted storage root.

```bash
python scripts/manage_document_backup.py backup \
  --storage-root /mnt/hardyai-documents \
  --backup-root /mnt/hardyai-documents/backups \
  --generation phase1-YYYYMMDDTHHMMSSZ \
  --source-revision "$(git rev-parse HEAD)"
python scripts/manage_document_backup.py verify \
  /mnt/hardyai-documents/backups/phase1-YYYYMMDDTHHMMSSZ
```

Verification fails closed unless every required artifact is present with its declared size and SHA-256,
both SQLite databases pass `quick_check`, the PostgreSQL custom dump has its expected signature, and
every tar archive is readable and contains only relative regular-file/directory entries. It rejects
absolute paths, traversal, symlinks, hard links, devices, and FIFOs before any restore extraction.

Use `--leave-stopped` when a maintenance window requires inspection before restart. A failed/partial generation is never reused.

## 7. Clean restore drill

Restore only into newly created empty LUKS-backed directories and a fresh PostgreSQL volume. Keep the gateway and worker stopped. Verify `manifest.json` first. Reject archives containing absolute paths, `..`, devices, or symlinks before extraction.

1. Restore `core.db` with the existing guarded core database restore procedure.
2. Restore `documents.db` to the new Jarvis document root with mode `0600`.
3. Restore Paperless media/data and the accepted-spool archive into their fixed roots.
4. Start only PostgreSQL/Valkey; use `pg_restore` on `paperless-postgres.dump` into the empty `paperless` database.
5. Start Paperless and verify API/server headers, exporter inventory, document count, and original checksums.
6. Start the coordinator with the gateway still stopped. Confirm expired leases and `awaiting_enqueue` intakes recover, then wait for the queue to settle.
7. Start the gateway. For every synthetic canary, verify status, search, source mapping, and byte-identical SHA-256. If an exporter/importer restore changed provider identifiers, stop and reconcile from export UUID/checksum/task evidence; never guess numeric IDs or edit Paperless PostgreSQL directly.
8. Repeat the offline cold-start test before declaring the recovery generation usable.

Record exact image IDs, source revision, commands, manifest checksum, restore duration, orphan count, and every failure. Phase 1 is not production-ready until this drill has passed on the authoritative Ubuntu host.

## 8. Rollback

Set `DOCUMENTS_ENABLED=false` where applicable and stop the optional profile:

```bash
docker compose --env-file .env -f deploy/docker/compose.yaml --profile documents \
  stop document-gateway document-worker paperless-webserver paperless-db paperless-broker
```

Leave the LUKS mount, databases, media, backups, and spool intact. Never delete a spool item merely because a job failed. Core Jarvis remains on its existing profile and should continue serving `/health` independently.
