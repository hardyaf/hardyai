# OCR Phase 2/3 CPU Checkpoint

Status: deployed and accepted CPU-only checkpoint

Prepared: 2026-08-24

Validated: 2026-08-25

This checkpoint completes the planned Phase 2 platform seams and the approved Phase 3 PDF slice with hardened,
CPU-only, born-digital PDF parsing. It deliberately stops before PP-OCRv6, PP-Structure, PaddleOCR-VL,
GPU scheduling, semantic retrieval, extraction/classification, or downstream actions.

## Delivered scope

- The shared `durable_jobs` ledger now owns priority/resource lanes, progress/stages, lease renewal,
  fencing, total deadlines, provider-operation reconciliation, cancellation, terminal dead letters, and
  operator requeue. Ticket callers use the compatibility seam instead of a second queue.
- A provider-neutral `HumanReviewService` owns durable quality, correction, metadata-proposal, and
  downstream-proposal review state with optimistic hash binding, actor/reason audit, idempotent decisions,
  expiry, supersession, and applied/executed states.
- Documents uses append-only source versions and processing runs, idempotent stage commits, immutable
  content-addressed artifacts, normalized pages/blocks/tables/cells, and an explicit active-run projection.
- The Main-only Documents skill offers bounded ingest instructions, status, search, evidence/source link,
  explicit reprocessing, review listing, and metadata proposals through the local gateway. Micro remains
  disabled. Content-bearing responses declare the generic `restricted_read` persistence policy, which
  suppresses generic recent turns, conversation history, memory, tickets, and Plane payloads.
- Watched-folder intake uses the same validated/hash/idempotent ingest service as HTTP after stable-file
  detection and atomic claim. Paperless-origin reconciliation is bounded, hash verified, owner scoped,
  opaque-ID based, and marks missing sources only after a complete discovery pass. Both features have
  independent enable flags.
- Docling Serve is digest pinned and isolated on an internal network with an API key, CPU device, one
  worker, read-only root, bounded file/page/time/output limits, no host port, and remote services/plugins/
  custom configuration/telemetry disabled. The adapter exposes only async local file conversion; URL
  conversion is not implemented.
- Phase 3 stores full provider JSON plus normalized JSON and safely escaped reproducible Markdown as
  immutable derivatives. Search/evidence returns the active run plus page, block, bbox, char span, and
  provider reference. Native quality rejects near-empty, partial, malformed-layout, bad-evidence, invalid-
  character, and bad-reading-order results into review/incomplete state.
- A content-free external-corpus benchmark contract and evaluator records manifest/provider/config/image
  hashes and structural metrics without checking source text or private fixtures into Git.

## Intentional Phase 3 limitation

Only born-digital PDF input is enabled for Docling. JPEG/PNG remain archived and searchable through the
Paperless Phase 1 baseline until conventional OCR is selected in Phase 4. Office parsing is not claimed:
it remains disabled until its converter/sandbox attack surface is separately reviewed. A scanned PDF with
no native text should become `needs_review`/`processing_incomplete`; it must not silently pass or trigger a
GPU/remote fallback.

## Production enablement

Prerequisites:

1. The Phase 1 encrypted mount, Paperless mapping, backup/restore generation, and no-egress profile remain
   healthy.
2. `${DOCUMENTS_STORAGE_ROOT}/jarvis/artifacts` and `import` exist, are owned by the configured Jarvis
   UID/GID, and reside on the verified encrypted NVMe.
3. `${DOCUMENTS_SECRETS_ROOT}/docling_api_key` contains a random key, is owned for the runtime, and has no
   group/other permissions.
4. The exact Docling image digest is present and its offline readiness/conversion smoke test passes before
   processing is enabled.

Enable:

```dotenv
DOCUMENTS_PROCESSING_ENABLED=true
DOCUMENTS_DOCLING_ENABLED=true
DOCUMENTS_WATCH_ENABLED=false
DOCUMENTS_ORIGIN_RECONCILIATION_ENABLED=false
DOCLING_SERVER_VERSION=1.30.0
DOCLING_IMAGE_DIGEST=sha256:0244089785d5ccb7570dfaa593cdc81ec64a1aadc63ffa9dce065064b0a6a807
```

Start with `--profile documents --profile documents-phase3` and always pass `--env-file .env`.
Watched import and Paperless-origin reconciliation require an explicit owner ID and their own later flag.

## Acceptance gate before Phase 4

- Run the full repository test suite and install/architecture checks on a clean export.
- Validate Compose, exact image digest/version, internal networks, absent host port/GPU, API-key rejection,
  remote URL rejection, and offline environment controls.
- Upload the deterministic native prose, multi-column, and table PDFs; wait for immutable Docling runs;
  verify reading order, table cells, source hash, evidence links, safe Markdown, and bounded search.
- Exercise provider stop/restart, pending-task reconciliation, worker restart, stale fencing, cancellation,
  dead letter/requeue, and a malformed/near-empty result.
- Confirm core `/health` and an ordinary Main conversation remain responsive during CPU processing.
- Inspect generic SQLite events, memory, tickets, jobs, session/context exports, Plane payloads, and logs for
  synthetic content canaries. Only the restricted Documents store/artifact payload and direct response may
  contain document text.
- Run the Phase 1 coordinated backup and clean-volume restore with the new documents schema/artifacts and
  confirm pending work can be recovered with workers initially stopped.
- Record the native benchmark report as `native-docling-pdf-v1`. Do not enable Phase 4 from a vendor score.

## Deployed acceptance record

- Application release: `hardyai-phase23-20260825-final9`, sanitized source commit
  `ae3fe585ac59b93895a24da6809f8be3bcbc05e7`, application image
  `sha256:99b687d73e243c4fbd2201cbe3574c8c3c45d69f9eeeaa70b3eb878150afb659`.
- Full suite: 529 passed and 2 skipped on Windows; the exact clean export passed 531 tests with no network
  on Ubuntu. The only Ubuntu warnings were existing FastAPI/Discord dependency deprecations.
- Runtime/install: core, gateway, worker, Docling, Paperless, PostgreSQL, and Valkey were healthy; the
  strict document verifier passed 10/10. Docling remained CPU-only, had no host port/GPU reservation, and
  blocked DNS plus direct IPv4/IPv6 egress.
- Native processing: prose, multi-column, and table PDFs completed through `native_docling` with immutable
  provider/normalized/Markdown artifacts, full block evidence coverage, bounded search, and source links.
  A near-empty PDF became `needs_review` with no active run or implicit fallback.
- Recovery/control: provider stop/restart, stale provider-operation resubmission, worker restart, pending
  recovery, cancellation, requeue, fencing, and core health during provider outage passed. The live ledger
  audit found 18 completed archive jobs and 7 completed processing jobs with only their exact opaque payload
  schemas.
- Main/persistence: an authenticated natural-language request resolved to `documents.find` through Main.
  The acceptance audit then scanned 43 generic SQLite tables and 466 text columns plus core/gateway/worker
  logs with zero synthetic canary exposure. That audit first caught a pre-result telemetry/persistence gap;
  the final release applies intent-level fail-closed policy, excludes Documents from tickets before
  execution, and records content-free request/decision telemetry. Fourteen synthetic test cells from the
  failed attempts were replaced with an explicit redaction marker and SQLite was checkpointed/compacted;
  no user document data was changed.
- Benchmark: the encrypted, content-free `native-docling-pdf-v1` report passed 3/3 cases (prose, columns,
  and table), including pages, minimum blocks, reading order, evidence coverage, and table structure. The
  report is stored under `${DOCUMENTS_STORAGE_ROOT}/jarvis/benchmarks/reports/`.
- Recovery generation: the isolated clean-volume drill restored and verified 17 Paperless originals and
  11 Jarvis artifacts from `pre-phase23-v6-20260825T0305Z` without changing production data.

All listed gates passed for this CPU/PDF checkpoint. Passing them does not authorize Phase 4 or GPU work.

## Rollback

Set `DOCUMENTS_DOCLING_ENABLED=false` and `DOCUMENTS_PROCESSING_ENABLED=false`, then recreate the worker,
gateway, and core without the `documents-phase3` profile. Preserve Docling image/model inventory,
`documents.db`, artifacts, reviews, jobs, Paperless originals, and backups. Paperless-only Phase 1 search and
source retrieval remain available. Additive schema changes require no destructive downgrade; an older image
must first prove it tolerates the new columns or use the retained pre-promotion core DB backup.

## Remaining risks and human boundaries

- General private document reads remain limited to the authenticated operator path. Financial extraction,
  identity/government/tax workflows, and every downstream mutation remain out of scope for this checkpoint.
- The pinned Docling image is available and completed offline conversion. Preserve the exact image/model
  inventory and complete any transitive model-license review before broader distribution or model changes;
  production lazy downloads remain forbidden.
- Metadata approval records a human decision but does not yet apply a Paperless mutation automatically.
  Reprocessing, cancellation of another user's job, metadata changes, and all downstream actions remain
  explicit operator actions.
- Phase 4 may add a benchmark-selected conventional OCR route. Phase 5—not this checkpoint—must establish
  shared GPU admission before any GPU fallback can run.
