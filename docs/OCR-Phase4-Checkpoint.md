# OCR Phase 4 Deployment Checkpoint

Status: deployed on the authoritative Ubuntu runtime; live post-restart Discord upload confirmation pending

Date: 2026-08-25

## Delivered scope

Phase 4 adds conventional PP-OCRv6 processing for JPEG and PNG documents without adding a second queue,
document store, review system, or GPU control path. The implementation reuses the existing durable-job
ledger, document run/artifact schema, `HumanReviewService`, Paperless original archive, provider adapter
boundary, and DocumentGateway authorization model.

The deployed path includes:

- an isolated PaddleOCR 3.7.0 HTTP service and typed client/adapter;
- PP-OCRv6 tiny, small, and medium detection/recognition weights provisioned locally with a checksummed
  manifest and read-only model files;
- deterministic image routing to conventional OCR while born-digital PDFs retain the Phase 3 Docling
  route;
- normalized geometry, per-block confidence, language, provider version, configuration digest, and
  source/run provenance under artifact schema version 2;
- fail-closed quality handling that sends near-empty/low-quality results to `needs_review`;
- retry-safe processing through the existing `document.process.v1` durable job type;
- Discord PDF/JPEG/PNG intake in every policy-authorized channel, including attachment-only messages,
  with attachment scope bounded to guild, channel, user, four document IDs, and 30 minutes; and
- Main-only, scope-checked document follow-up access using opaque attachment IDs rather than document
  content in Discord/Core transport metadata.

PP-StructureV3, PaddleOCR-VL, document GPU access, accelerator arbitration, extraction/classification,
critical-field approval, and downstream mutations remain disabled.

## Exact release and runtime

- Sanitized release: `hardyai-ocr-phase4-20260825-rc6`
- Sanitized commit: `b5ef6570abc45310b8e0657921e09b81a17770c6`
- Transfer archive SHA-256:
  `3560dd47c1f4e30331d8c4ce0d2c53357ff51ee3c78bd65fce696740881617cc`
- Application image:
  `sha256:27aad9688644e0a2f0c1790aebcd63639561e80e286893af5ad980c903e613f8`
- PaddleOCR image:
  `sha256:d80b0c2d2647475c683e0685452b6269d7ac49ace6cc26f453f473d5b5defda3`
- PaddleOCR release/model family: `3.7.0`, PP-OCRv6
- Selected production tier/device: `tiny`, CPU, four threads
- Authoritative checkout: `/home/codex/jarvis-poc`
- Durable storage/model root: `/mnt/hardyai-documents`

Every Compose operation used `--env-file .env`. Jarvis, the document worker, DocumentGateway, and Discord
attachment ingress run the same application image. All were healthy with zero restarts after promotion.
The PaddleOCR service was healthy with zero restarts and no host port binding.

## Benchmark result

All benchmark runs used disposable, read-only, no-network, non-root CPU containers with four CPUs and the
sealed local model files. Reports are retained mode `0600` under
`/mnt/hardyai-documents/jarvis/benchmarks/reports/`.

| Tier | Mean fixture time | Passed | Result |
|---|---:|---:|---|
| tiny | 0.1064 s | 3/3 | Selected: smallest and fastest; all required canaries exact |
| small | 0.1878 s | 3/3 | Passed; low-light similarity 0.973 |
| medium | 0.5728 s | 3/3 | Passed; low-light similarity 0.9865 |

The three synthetic fixtures covered clean, skewed, and low-light printed images. Required date, account,
and total canaries were exact. The result selects a conservative baseline; it does not authorize critical
fields or claim handwriting/general-photo accuracy.

GPU benchmarking was not performed. The user-approved boundary keeps document workloads off the RTX 3090
until Phase 5 implements shared cross-process accelerator admission and proves conversation coexistence.

## Verification evidence

- The exact clean RC6 export passed the public-tree release scan before and after Ubuntu testing.
- The full Ubuntu suite ran with networking disabled: `554 passed`, four dependency deprecation warnings.
- Both release images built with build networking disabled; dependency/model layers came from the retained
  local cache and sealed model root.
- Models started offline from fixed paths and the startup manifest revalidated selected file size and
  SHA-256 values.
- Missing and incorrect OCR API keys returned `401`; the OpenAPI route remained unavailable.
- The production OCR container runs as `1001:1001`, with a read-only root, all capabilities dropped,
  `no-new-privileges`, no GPU device request, no host port, and only the internal
  `documents-inference` network.
- External DNS and direct external IPv4 connection probes from the OCR container were blocked.
- The real archived Discord phone image that exposed the final compatibility edge is a valid 3072x4080,
  two-frame MPO. The fix accepts Pillow's `mpo` label only for already validated `image/jpeg` inputs;
  PNG and size/pixel policies remain strict. A disposable exact-image smoke test completed through
  PP-OCRv6 tiny on CPU.
- The audited durable retry completed in production. Because the image yielded only five recognized
  characters, the run correctly became `needs_review` with one block at mean confidence 0.8071 rather
  than being falsely marked complete.
- After cutover, both Ollama models reported `100% GPU`; Main `/ask` probes completed in 12.147 seconds
  cold and 7.293 seconds warm. OCR did not receive a GPU device.

## Backup and rollback

The coordinated pre-Phase-4 backup is retained at
`/mnt/hardyai-documents/backups/pre-phase4-20260825-rc3` and passed its verification manifest. The prior
source, environment, application image, and OCR image tags are retained.

Rollback is additive and non-destructive:

1. Set `DOCUMENTS_PADDLEOCR_ENABLED=false` in the protected Ubuntu `.env`.
2. Recreate Jarvis, the document worker, and DocumentGateway with all required profiles and
   `--env-file .env`.
3. Stop the `documents-phase4` OCR service/profile.
4. If required, retag the retained pre-Phase-4 application image and restore the protected pre-promotion
   environment file.
5. Keep schema v8, originals, artifacts, review records, models, and backups intact unless a separately
   approved recovery requires restoration.

## Remaining review gates and risks

- A fresh Discord upload is required after the final bot restart because attachment follow-up scope is
  intentionally in-memory and channel/user-local. The automated scoping and handoff tests passed; record
  the live result here before declaring Discord acceptance closed.
- Three synthetic fixtures establish the model-selection baseline, not broad document-class accuracy.
  Expand the non-sensitive holdout before enabling new classes or lowering review thresholds.
- Near-empty OCR remains review-only. The Phase 5 fallback must not be enabled until shared GPU admission,
  difficult-document benchmark evidence, hallucination/disagreement policy, and conversation-latency
  soak tests pass.
- Critical fields, metadata changes, corrections, exports, and downstream actions remain human-approved.
