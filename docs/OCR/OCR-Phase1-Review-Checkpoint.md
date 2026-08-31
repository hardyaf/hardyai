# OCR Phase 1 deployment checkpoint

Date: 2026-08-24

Status: deployed on the authoritative Ubuntu host; synthetic acceptance, recovery, backup, restore,
and no-egress checks passed. Real personal documents remain outside this approval.

## Deployed boundary

This checkpoint covers only the secure Paperless archive bridge approved in `OCR-Plan.md`: the
private document ledger and encrypted ingress spool, generic durable jobs, restricted enqueue IPC,
provider-neutral archive ports, Paperless adapter, crash-safe archive worker, operator-only gateway,
offline policy, isolated Compose profile, install verification, coordinated backup, restore-drill
tooling, ADR, and operations runbook.

Docling, PaddleOCR, structured extraction, classification, vector search, channel attachments,
document-driven actions, sharing, deletion, and real restricted documents remain out of scope.

## Authoritative deployment evidence

- Host: the authoritative `hardybot` Ubuntu runtime; checkout: the configured authoritative
  deployment checkout.
- Core storage remains on `/dev/nvme1n1`. The second 1.9 TiB NVMe, `/dev/nvme0n1`, is now LUKS2 and
  mounted as `/dev/mapper/hardyai-documents` at `/mnt/hardyai-documents`; approximately 1.8 TiB was
  free after acceptance.
- TPM2 enrollment, immediate TPM reopen, persistent `crypttab`, and persistent `fstab` checks passed.
  A full host reboot/unattended-unlock observation is still required during an approved window.
- Paperless 3.0.5/API v10, PostgreSQL, and Valkey run from digest-pinned images. Paperless and the
  document gateway have no published host ports.
- Core Jarvis, the document worker, document gateway, Paperless, PostgreSQL, Valkey, and Ollama were
  healthy after final recreation. Core `/health` returned `{"status":"ok"}`.
- Host-side document install verification: 9 passed, 0 warnings, 0 failures.
- Full Windows regression: 502 passed, 1 skipped. The skip is the Linux Unix-socket round trip.
- Final clean Ubuntu/network-disabled regression: 503 passed, 0 skipped, 2 dependency warnings.
- Sanitized public-tree checks passed for every promoted release; protected `.env`, runtime data,
  credentials, and secrets were preserved outside the release artifact.

## Live acceptance evidence

- Separate non-admin Paperless archive and reader accounts have distinct tokens. The archive account
  has add/change/view document plus task-view permissions; change is used only to grant the reader
  object-level view permission. The reader has view-only document access.
- Paperless API v10 task compatibility is verified against `related_document_ids` and `result_data`.
- Eleven synthetic Paperless originals were reconciled to the read-only principal.
- PDF, JPEG, and PNG ingestion reached `ready`; exact duplicate uploads returned the existing HardyAI
  document ID; OCR-only `Utility` search returned mapped results; every downloaded original matched
  its source SHA-256 byte for byte.
- Core `document.archive.v1` durable-job payload audit passed for all jobs: only `document_id`,
  `intake_id`, and `sha256` were present. All audited jobs completed.
- Paperless outage drill passed: upload remained queued with a recorded transient failure and encrypted
  spool copy, then recovered to `ready` with an identical source after Paperless restarted.
- Coordinator outage drill passed: upload reported `awaiting_enqueue` and `enqueue_confirmed=false`,
  then worker restart recovered it to `ready` with an identical source.
- DNS, direct IPv4, and direct IPv6 probes failed from gateway, worker, and Paperless. PostgreSQL DNS
  resolution also failed. All three document networks reported `Internal=true`, no default gateway,
  and the intended service membership.
- All document containers were removed and recreated with `--pull never --no-build`; the synthetic
  ingest/search/download smoke test passed afterward.

## Backup and restore evidence

- Coordinated backup generation:
  `/mnt/hardyai-documents/backups/phase1-20260825T210000Z`.
- Manifest, artifact sizes/hashes, both SQLite `quick_check` results, PostgreSQL custom dump signature,
  and safe tar-member checks passed.
- Clean restore drill destination:
  `/mnt/hardyai-documents/restore-drills/phase1-20260825T210000Z-r3`.
- The drill used fresh encrypted directories and isolated temporary PostgreSQL, Valkey, and Paperless
  containers. Restored PostgreSQL contained 11 documents; API v10/server 3.0.5 checks passed; all 11
  original files matched the restored HardyAI ledger hashes; full-text search returned all 11 canaries.
- Temporary restore containers and network were removed. The successful restored copy and backup were
  retained on the encrypted NVMe.
- The earlier `phase1-20260825T204000Z` backup attempt stopped before writer shutdown because the
  exporter target did not yet exist. The reusable backup tool now creates that empty target first.
  The `r1` and `r2` restore attempts exposed and fixed the read-only validation mount traversal issue;
  their isolated failed drill directories were not reused.

## Remaining risks and gates

1. Observe one approved full-host reboot and confirm TPM auto-unlock, mount-before-Docker ordering,
   document health, and a repeat synthetic retrieval. Keep the recovery passphrase escrowed.
2. Configure durable monitoring/alerts for mount loss, free space, spool quota, queue/dead-letter age,
   worker heartbeat, provider readiness, backup age, and restore-drill age.
3. Resolve the deployment baseline warning: the host is Ubuntu 26.04 while existing deployment assets
   name Ubuntu 24.04 as their target.
4. Review and approve the household user/sensitivity access model, retention policy, backup RPO/RTO,
   and any non-loopback/TLS access before adding real normal/private documents.
5. Do not ingest identity, government, tax, financial, child, or other highly restricted material until
   the later storage/redaction/review phases and their separate approvals are complete.

Rollback remains the pre-deployment source/data generation
`pre-ocr-phase1-20260824T235710Z` under the protected deployment-backup root, plus the prior image
recorded there.
Disable the optional documents profile without deleting the LUKS mount, archive, backup generations,
or any accepted spool data.
