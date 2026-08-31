# OCR Phase 5.1 Deployment Checkpoint

Status: deployed on the authoritative Ubuntu runtime; fresh user Discord upload confirmation pending

Date: 2026-08-25

## Delivered scope

Phase 5.1 completes the asynchronous Discord attachment lifecycle. It does not add classification,
structured extraction, metadata mutation, or downstream actions.

The deployed path now:

- records one idempotent, content-free `document.discord_completion.v1` job after attachment acceptance;
- stores only document and Discord correlation IDs in Core, never filenames, source bytes, OCR text, or
  extracted values;
- wakes the matching job when the document worker commits a terminal processing state;
- retains bounded status polling so a core-database/document-database crash window cannot lose delivery;
- rechecks the current guild, channel, user, role, and separate `document_response_channel_ids` policy;
- requests the final result only through the sensitivity-bounded Documents query facade;
- sends from Core with mentions disabled and a deterministic nonce;
- persists the returned Discord message ID before completing the job; and
- retries transient delivery failures while dead-lettering permanent authorization failures visibly.

The document worker has no Discord credential, channel client, source-text payload, or outbound Discord
path. Phase 5 human-review requirements remain unchanged: difficult VLM results return a truthful
`needs_review` message and never expose candidate text as verified.

## Exact release and runtime

- Sanitized source release: `hardyai-ocr-phase5-1-20260825-rc1`
- Application/worker image tag: `jarvis-poc-app:ocr-phase5-1-rc1`
- Application/worker image digest:
  `sha256:e5bff558055cbd4c9e7d3108f7d9aa9077da59c9fbbdfdc442a8c2d57d74f494`
- Authoritative checkout: `$HARDYAI_RUNTIME_ROOT`
- Shared durable ledger: `$HARDYAI_RUNTIME_ROOT/data/jarvis_v2.db`

Every production Compose operation used `--env-file .env`. Core and the document worker were the only
services recreated for this delivery change.

## Verification evidence

- The sanitized candidate passed the public-tree release scan with 485 copied files.
- The exact sanitized candidate passed the complete suite with networking disabled: `576 passed`, with
  only the four known dependency deprecation warnings.
- The focused notification/Discord/Documents/worker suite passed: `66 passed`.
- Coverage proves idempotent registration, content-free payloads, no-attempt polling deferral, terminal
  wake-up, restart recovery after a persisted Discord receipt, deterministic nonce use, policy-removal
  denial, dead-letter visibility, and one-message completion.
- A non-posting deployed smoke used an ephemeral queue and the live bounded DocumentGateway. It prepared
  the expected human-review presentation, recorded a simulated Discord receipt, and completed the job.
- The deployed notification heartbeat is `ready` with no error; Core and the document worker are healthy,
  use the exact image digest above, and recorded zero restarts.
- All surrounding OCR, archive, accelerator, and conversation services remained healthy after the
  two-service recreation.

## Policy and security

Automatic results remain default-off. Enabling the feature requires both:

1. `DISCORD_DOCUMENT_NOTIFICATIONS_ENABLED=true`; and
2. an explicit channel entry under `document_response_channel_ids` in the protected Discord policy.

The deployed policy includes the existing attachment-capable Jarvis and email-agent channels. The silent
private-notes capture channel remains excluded. Delivery reloads and rechecks the protected policy at
send time, so removing a channel or user grant prevents output even when the attachment was accepted
earlier.

The bounded final message can contain verified conventional OCR text when processing is `complete`.
`needs_review`, `processing_incomplete`, `failed`, and `cancelled` return state-specific safe messages.
No document output enters a remote model, unrelated connector, generic memory, or the durable job payload.

## Rollback

1. Set `DISCORD_DOCUMENT_NOTIFICATIONS_ENABLED=false` in the protected Ubuntu environment.
2. Recreate Core with the required profiles and `--env-file .env`.
3. Leave notification jobs in the shared ledger for operator inspection; do not bulk-delete them.
4. Continue attachment ingestion, OCR, reviews, and user-requested `documents.get` status normally.

The retained Phase 5 image remains available for a full code rollback. Disabling notifications alone is
the preferred additive rollback because it does not affect sources, processing runs, reviews, or OCR.

## Remaining gate and risks

- A fresh attachment from the user is required to prove Discord itself receives the automatic terminal
  reply after the final restart. Automated and non-posting deployed checks pass.
- Discord is an external side effect. The job stores the returned message ID before completion and uses a
  deterministic nonce on retry. A process loss in the narrow interval after Discord accepts a message but
  before Core persists its receipt may still depend on Discord nonce deduplication.
- Phase 6 classification and extraction remain disabled and separate.
