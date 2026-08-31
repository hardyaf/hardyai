# OCR Phase 6 Checkpoint

Status: deployed and enabled on authoritative Ubuntu

Date: 2026-08-25

## Delivered

- Added the exact versioned `document-taxonomy-v1` registry and typed schemas for financial
  documents, notes, business cards, contracts, insurance, warranties, protected identity classes,
  and unknown documents.
- Added provider-neutral, tool-free `DocumentClassifierPort` and `StructuredExtractorPort`
  contracts. The enabled first provider is deterministic and local; it has no tools, intents,
  approvals, recipients, network calls, or arbitrary output fields.
- Added fixed-operation masking for SSNs, long numbers, and labeled account, policy, passport,
  license, record, member, claim, and tax identifiers. Exact restricted values are rejected again at
  schema validation and correction persistence.
- Added append-only classifications, field observations, human-decision projections, correction
  precedence, conflict state, low-confidence shared reviews, and bounded authenticated operator
  classification/field views in the existing Documents domain.
- Added allowlisted `safe_title` Paperless metadata writes with source-version binding, durable local
  operation idempotency, approval hash checks, and mandatory read-back verification. Jarvis document
  classes are not written into Paperless taxonomy.
- Added `protected_pending` routing. Identity, government, tax, more-restrictive sensitivity canaries,
  and any value that remains restricted after masking stop before ordinary artifacts, blocks, fields,
  search, or review values are persisted.
- Closed the Paperless visibility window: with Phase 6 enabled, the archive worker defers the read-user
  grant until classification. Protected or redacted sources remain revoked and Paperless-text fallback
  is denied. Safe masked Jarvis blocks remain searchable and evidence-addressable.
- Kept exact protected source download disabled until Phase 10. Phase 6 does not add a second archive,
  review authority, contact store, task store, or memory store.

## Persistence and migration

- `documents.db` schema contract is version 10.
- New append-only tables are `document_classifications`, `document_field_observations`,
  `document_field_decisions`, and `document_metadata_sync`.
- `documents.selected_document_class`, `classification_state`, and `archive_text_visible` are bounded
  projections. Human corrections are never deleted or replaced by reprocessing.
- Exact restricted values are not permitted in these rows. Phase 10 owns any future encrypted exact
  value store and accessor.

## Acceptance evidence

- Focused Phase 6 and existing Documents/Paperless/worker suites passed on Ubuntu.
- Complete offline suite passed: `591 passed, 4 warnings`.
- Source was mounted read-only during tests; only test databases and the one legacy
  conversation-history scratch path used ephemeral tmpfs mounts.
- Production feature gate: `DOCUMENTS_SAFE_EXTRACTION_ENABLED=true`.
- Deployed application digest:
  `sha256:bfbc914860045aab7496739bcbcb953ea527d0c57d9810dc667e120d3d5d3737`.
- `jarvis`, `document-gateway`, and `document-worker` run that exact digest, are healthy, and reported
  zero restarts after recreation.
- Live worker reports the Phase 6 flag enabled and schema contract version 10.

## Rollback

1. Set `DOCUMENTS_SAFE_EXTRACTION_ENABLED=false` in the protected production `.env`.
2. Retag `jarvis-poc-app:ocr-phase5-1-rc1` as `jarvis-poc-app:local`.
3. Recreate only `jarvis`, `document-gateway`, and `document-worker` with
   `docker compose --env-file .env -f deploy/docker/compose.yaml`.
4. Preserve schema-10 rows and review history; never down-migrate by deleting observations or
   decisions.

The protected rollback configuration is
`$HARDYAI_PRIVATE_RELEASE_ROOT/pre-phase6-20260825-rc1.env` (mode `0600`).

## Remaining boundaries

- The deterministic extractor establishes safe infrastructure, not production accuracy. Real examples
  should become a de-identified/versioned tuning and benchmark corpus before confidence thresholds or
  field coverage are broadened.
- Metadata changes and all corrections remain human-approved. The model cannot approve or execute
  them.
- Exact account, policy, identity, government, tax, record, signature, secret, or child-identity
  values remain hard-disabled until Phase 10 encryption, isolated access, key recovery, and clean
  restore gates pass.
- Phase 7 and later downstream promotions remain disabled at this checkpoint.
