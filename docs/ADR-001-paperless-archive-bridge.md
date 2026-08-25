# ADR-001: Paperless archive ownership for Documents Phase 1

Status: accepted for Phase 1 implementation; production activation still requires the Ubuntu preflight

Date: 2026-08-24

## Decision

Paperless-ngx is the canonical owner of every permanent original document binary. HardyAI keeps an encrypted, fsynced ingress copy only until a worker has downloaded the Paperless original and verified the exact byte count and SHA-256. The transient copy is then removed. `documents.db` owns the private intake, state, checksum, and opaque source mapping; core `jarvis_v2.db` owns only content-free durable-job control rows.

Phase 1 pins:

- Paperless-ngx 3.0.5: `sha256:65a4cabf0169ea7fbd90ab7bb28ba3f8b5909613635acda1a03ad606f34b456b`
- Paperless API v10, verified through `X-Api-Version`; server release verified through `X-Version`
- PostgreSQL 18 manifest: `sha256:06cad38a5d9f5d24b4d83d86def30795d5e4b757fedbf5281172b576dedcd941`
- Valkey 8 manifest: `sha256:f0ba225266310efba5fb33383e21c64fbd07907304224786c780606e7ebd7327`
- Python 3.12 slim-bookworm base: `sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134`
- `PAPERLESS_OCR_MODE=auto`
- `PAPERLESS_CONSUMER_DELETE_DUPLICATES=true`

The gateway receives only the Paperless read/search token. The archive coordinator receives only the create/task/archive token. Core Jarvis receives neither. Paperless is not host-published. The document gateway is host-published on loopback only and rejects non-loopback cleartext requests even if an operator credential is supplied.

## Isolation and durability

The gateway, coordinator, and Paperless communicate only on internal Compose networks. Gateway-to-coordinator enqueue uses a fixed-operation, size-bounded Unix socket with mode `0600`; the gateway never mounts core SQLite. An accepted upload is represented truthfully as `awaiting_enqueue` until the coordinator confirms the durable core job, then `queued`. Startup recovery retries all `awaiting_enqueue` rows idempotently.

All Paperless data/media/export, PostgreSQL data, Valkey persistence, `documents.db`, and ingress spool live below `DOCUMENTS_STORAGE_ROOT`. Production verification requires that root to resolve to a LUKS device. Compose uses `create_host_path: false` so a missing mount cannot silently become an unencrypted directory.

The recovery generation consists of:

- Paperless exporter output;
- a PostgreSQL custom-format dump;
- Paperless data/search state and media archives;
- online-verified snapshots of core SQLite and `documents.db`;
- the accepted-but-unarchived spool;
- a manifest with checksums, source revision, API version, and server version.

## Consequences

Paperless is GPL-3.0 software and its image/source obligations must remain in the release bill of materials. HardyAI does not directly mutate Paperless PostgreSQL. Direct Paperless UI and consume-directory ingestion remain unsupported because either path would bypass the HardyAI intake mapping. Deletion, metadata mutation, sharing, Docling, PaddleOCR, classification, extraction, downstream actions, and real identity/highly restricted documents remain outside Phase 1.

If the profile is stopped or `DOCUMENTS_ENABLED=false`, core Jarvis remains operational and all encrypted document state remains intact. Rollback never deletes an ingress file unless verified archival was recorded.
