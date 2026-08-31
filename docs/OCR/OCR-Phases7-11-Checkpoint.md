# OCR Phases 7-11 Deployment Checkpoint

Status: Phases 7-9 and 11 deployed; Phase 10 boundary deployed and disabled

Date: 2026-08-25

## Delivered feature set

Phase 7 adds reviewed note action proposals, canonical Lists execution, and shared provenance. Repeated
approval is idempotent and Documents does not mirror Lists state. Fact-memory proposals remain pending and
truthfully unavailable because no executable canonical fact-memory service exists.

Phase 8 adds provider-neutral person/contact ports, explainable matching, proposal comparisons, and source
provenance. ADR-002 deliberately defers the authority decision. No local contact directory, write adapter,
token, or shadow contact storage was added; production remains `capability_unavailable` for contact writes.

Phase 9 adds deterministic financial/date/decimal validation, totals reconciliation, recurring/prior-period
and anomaly candidates, and evidence-grounded contract, insurance, and warranty intelligence. It creates no
payment, signature, filing, email, or unreviewed reminder action. Exact account/policy identifiers remain
suppressed. The optional keyed recurring-account token is unavailable until its key design is approved and
provisioned.

Phase 10 adds only the reviewed fail-closed boundary: threat model, cipher/store interfaces, readiness
contract, restricted access-audit schema, bounded gateway denial/readiness responses, and worker startup
refusal. The production flag is false. There is no authenticated cipher adapter, isolated exact-value store,
restricted extractor, key escrow, or exact-value data plane. Identity, government, tax, and unmaskable
restricted sources therefore remain `protected_pending`.

Phase 11 adds content-free operational health, a release manifest/BOM with exact Compose image IDs and
Python package/license inventory, install-time feature dependency and restricted-readiness checks, and an
operations/incident/rollback runbook. The shared read-only SQLite connection remains owned by `app/db`, so
operations code does not bypass the repository's connection-authority boundary.

## Exact final release

- Sanitized source: `clean-tree-rc4` under the protected Phase 11 release directory; public-tree scan passed
  with 520 copied files.
- Application image tag: `jarvis-poc-app:ocr-phase11-rc3`.
- Application image digest:
  `sha256:edfbb386e4e9801eb02d19c234ee086b369cf5d199465f99cf6e096bca40e95f`.
- Conventional PP-OCR image digest:
  `sha256:d80b0c2d2647475c683e0685452b6269d7ac49ace6cc26f453f473d5b5defda3`.
- PaddleOCR-VL image digest:
  `sha256:91d3e74a0e4f79bbe7f86d9c1ff85f4b0146d6890f9f2d1da3ea6d4828cb2a58`.
- Protected manifest: `$HARDYAI_RELEASE_ROOT/ocr-phase11-20260825/manifest.json`, mode `0600`.
- Protected rollback configuration:
  `$HARDYAI_PRIVATE_RELEASE_ROOT/pre-phase11-20260825-rc2.env`, mode `0600`.

Core, DocumentGateway, document worker, accelerator admission, and Discord attachment ingress run the final
application digest. All are healthy with zero restarts. Docling, conventional OCR, PaddleOCR-VL, Ollama,
Paperless, PostgreSQL, and Valkey remain healthy on their pinned/recorded images. No parallel native-systemd
Documents service is active.

## Verification and recovery evidence

- Focused operations/install/architecture gates passed during development.
- Working-tree and exact sanitized-export suites both passed offline: `621 passed, 4 warnings`. The warnings
  are the known dependency deprecations; source was read-only and networking was disabled.
- Host install verifier: 12 passed, 0 warnings, 0 failures, including LUKS, separate credentials, pinned
  Paperless images, offline controls, downstream feature dependencies, and disabled restricted processing.
- Content-free operational health returned `ok`: empty spool, no document dead letters, fresh idle worker
  heartbeat, valid Core/Documents SQLite quick checks, fresh coordinated backup, and ample free space.
- Job audit found only allowlisted opaque fields for all archive and processing jobs.
- Known archive and fresh VLM canaries were absent from all 478 scanned Core text columns.
- DNS, direct public IPv4, and direct public IPv6 were denied from DocumentGateway, document worker,
  Docling, conventional OCR, PaddleOCR-VL, and accelerator admission.
- Coordinated backup `pre-phase11-20260825-rc2` verified with no hash or integrity failures.
- Isolated restore drill recovered 23 Paperless originals, verified 49 Jarvis artifacts, found 23 PostgreSQL
  documents, and passed archive/source mapping. Temporary containers and the internal drill network were
  removed; the protected drill directory remains available for inspection.
- Resident Discord conversation passed 3/3 responses with p95 7.90 seconds. Concurrent conversation/VLM
  passed 3/3 plus 3/3, with conversation p95 9.64 seconds and VLM mean 5.00 seconds. A post-alignment smoke
  passed at 8.56 seconds conversation and 4.71 seconds VLM, with no OOM or service restart.

## Production gates and rollback

Enabled flags are safe extraction, note proposals, contact proposals, and financial/contract intelligence.
The contact flag exposes the capability-gated proposal surface, not a write authority. Restricted processing
remains false.

Rollback retags the retained Phase 10 image, restores the protected pre-Phase-11 environment, and recreates
the affected Compose services with `--env-file .env`, `--pull never`, and `--no-build`. Preserve databases,
artifacts, originals, review/correction history, proposal receipts, backups, restore drills, and release
manifests. Do not down-migrate or bulk-delete downstream user data.

## Remaining gates and tuning work

- Complete Phase 10 only after independent security review, a vetted authenticated-encryption provider,
  isolated restricted storage, separately escrowed keys, and a clean key/data recovery attestation.
- Select one canonical contact authority before enabling contact writes.
- Implement or select the repository-wide fact-memory authority before promoting memory proposals.
- Approve and provision the keyed recurring-account token design before exact recurring matching.
- Expand the de-identified/versioned real-document corpus and tune classifiers, extraction, confidence, and
  evidence quality without weakening safety gates.
- Conversation latency is bounded and far below the former minute-scale regression, but the measured 7.90s
  idle p95 is slower than the earlier approximately three-second warm baseline and remains an optimization
  target.
