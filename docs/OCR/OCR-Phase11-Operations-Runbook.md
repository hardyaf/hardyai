# OCR Phase 11 operations and incident runbook

Status: applies to the enabled Phase 1-9 feature set; Phase 10 restricted processing remains disabled

Date: 2026-08-25

## Routine checks

Run every production Compose command from `$HARDYAI_RUNTIME_ROOT` with
`--env-file .env -f deploy/docker/compose.yaml`. Confirm Core, DocumentGateway, document worker,
Paperless, PostgreSQL, Valkey, Docling, PP-OCRv6, PaddleOCR-VL, Ollama, and accelerator admission are
healthy. No native-systemd Documents runtime may run in parallel.

Run `scripts/check_document_operations.py` at least every five minutes. Alert on low encrypted-volume
free space, spool quota/age, document dead letters, stale/degraded worker heartbeat, SQLite integrity,
and stale/missing coordinated backup. Its output contains counts and timestamps only.

Run the denied-egress check, content-boundary audit, job-payload audit, complete offline tests, GPU
coexistence benchmark, and coordinated backup verification for every release candidate. Keep the
generated release manifest with the protected release record.

## Backup and restore

Use `scripts/manage_document_backup.py backup` for the coordinated writer barrier across Core SQLite,
Documents SQLite/artifacts/spool, Paperless PostgreSQL/data/media/export. Verify the generation before
acceptance. Run `scripts/drill_document_restore.py` into a new, explicitly named directory within the
encrypted storage root; workers remain stopped until SQLite, artifact hashes, Paperless source mapping,
and pending jobs verify.

Restricted-field keys are not part of this release because Phase 10 is disabled. A future restricted
release must add separately escrowed keys and a clean-host key/data restore attestation.

## Incident containment

- Suspected content/credential egress: stop `document-gateway` and `document-worker`, preserve volumes
  and logs, revoke affected fixed-operation tokens, and run the boundary/egress audits. Do not delete
  evidence or originals.
- Parser/model CVE: disable the affected route flag, stop only that parser service, retain sources and
  immutable prior runs, pin a reviewed replacement, and reprocess explicitly.
- Corrupted derivative: mark the run failed/incomplete, preserve its hashes, regenerate from the
  canonical Paperless original, and never overwrite a human correction.
- Queue/dead-letter growth: keep Core conversational service up, stop new document intake if spool risk
  rises, inspect content-free error codes, repair the provider, then release/retry bounded work.
- Disk pressure: stop intake before the free-space floor. Do not purge originals, reviews, corrections,
  keys, or rollback images as an emergency shortcut.
- Token rotation: create the replacement with least privilege, update the protected file atomically,
  restart only consumers, prove read-back, then revoke the old token.

## Promotion and rollback

Promote synthetic documents first, then a small normal/private corpus, then financial documents under a
separate operator gate. Identity/government/tax remains blocked. Roll back by restoring the protected
pre-release `.env`, retagging the previous pinned application image, and recreating services with
`--pull never --no-build`. Preserve all durable data and observation history.
