# OCR Phase 10 restricted-workflow threat model

Status: implementation boundary deployed disabled; security gate not satisfied

Date: 2026-08-25

## Protected assets

Exact identity, government, tax, policy, account, membership, claim, and record identifiers are
`highly_restricted`. Ordinary OCR blocks, search, Main context, memory, jobs, logs, Paperless text,
and general document fields must never retain those values.

## Required controls before enablement

- A reviewed authenticated-encryption adapter with envelope keys, unique nonces, associated-data
  binding, versioned key IDs, rotation, and tamper detection.
- An isolated encrypted value store and worker that are not mounted into Core, the general Gateway,
  parser, or ordinary document worker.
- Adult/operator-only field scopes; child, guest, Discord, and ordinary document APIs denied.
- Purpose-bound, content-free audit for every allow/deny/read/review/export attempt.
- Redacted-by-default retrieval, bounded one-field responses, `Cache-Control: no-store`, and no bulk
  decrypt/export route.
- Separately protected recovery keys and a successful clean-host key/data restore attestation.
- Independent security review recorded by immutable review identifier.

## Current gate result

The pinned offline application image has no approved authenticated-encryption library or restricted
store adapter. Phase 10 therefore has no production cipher, store, extractor, retrieval, or export
implementation. `DOCUMENTS_RESTRICTED_WORKFLOW_ENABLED=true` must fail startup until all readiness
inputs are present. Existing identity/government/tax documents remain `protected_pending`.

This is intentional fail-closed behavior, not a claim of Phase 10 data-plane completion.
