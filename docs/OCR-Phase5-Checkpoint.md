# OCR Phase 5 Deployment Checkpoint

Status: deployed on the authoritative Ubuntu runtime; fresh user Discord upload confirmation pending

Date: 2026-08-25

## Delivered scope

Phase 5 adds a difficult-image PaddleOCR-VL fallback and one shared accelerator-admission boundary. It
does not add classification, extraction, autonomous metadata changes, or downstream actions.

The deployed path includes:

- a durable SQLite priority queue with leases, fencing tokens, heartbeats, bounded waits, recovery, and
  explicit lanes for every production Ollama/VLM caller;
- a single authenticated admission proxy between Jarvis workloads and the GPU backends, with no direct
  production route from Core or workers to Ollama;
- a protected-model policy that keeps `gpt-oss:20b` resident for Main conversation and permits only
  `qwen2.5:7b` to be evicted before document VLM work;
- the complete offline PaddleOCR-VL 1.6 pipeline, including its layout stage, behind a typed image-only
  adapter at concurrency one;
- automatic VLM fallback only after conventional image OCR fails the Phase 4 quality gate;
- bounded comparison against the conventional run, disagreement/critical-field reasons, and mandatory
  human review for every VLM result;
- isolated, read-only, non-root, capability-free, no-host-port GPU services on internal networks; and
- a Discord acknowledgment sent before accepted attachment submission begins:
  `I got it - processing now.`

Native PDFs continue to use Docling. Clean printed images continue to use PP-OCRv6 on CPU. Phase 6
classification/extraction and all downstream mutations remain disabled.

## Exact release and runtime

- Sanitized release: `hardyai-ocr-phase5-20260825-rc8`
- Tested code candidate: `hardyai-ocr-phase5-20260825-rc7`, sanitized commit
  `95c9d01a1ca04398df98e060af9b648e76bccaa8`
- Tested code-candidate archive SHA-256:
  `e781987044b0d1ad65aba4e1109f32750a9ecd30f33fb626477793c33125be72`
- Application image:
  `sha256:7cd83450c7ed74e3651d3ea9a219aafbd5a55153de400b272d3ea06e7f906ea6`
- Derived PaddleOCR-VL image:
  `sha256:91d3e74a0e4f79bbe7f86d9c1ff85f4b0146d6890f9f2d1da3ea6d4828cb2a58`
- Pinned upstream PaddleOCR-VL base digest:
  `sha256:6c735bcb3a995704fa0b6ee8c8994249493fe761096fc62e2ef1de103b7a814e`
- PaddleOCR framework/pipeline/model: `3.6.0`, `1.6`, `PaddleOCR-VL-1.6-0.9B`
- Layout model: `PP-DocLayoutV3`
- Conventional OCR image:
  `sha256:d80b0c2d2647475c683e0685452b6269d7ac49ace6cc26f453f473d5b5defda3`
- Authoritative checkout: `$HARDYAI_RUNTIME_ROOT` (operator-configured Ubuntu path)
- Durable storage/model root: `$DOCUMENTS_STORAGE_ROOT` (operator-configured encrypted mount)

Every production Compose operation includes `--env-file .env`. No model, OCR, benchmark, test, or
deployment work ran on Windows.

## Quality and latency evidence

Reports are retained mode `0600` below `$DOCUMENTS_STORAGE_ROOT/jarvis/benchmarks/reports/` and contain
scores/status only, not OCR text.

| Check | Result |
|---|---|
| Three-image occlusion holdout | Accepted; conventional mean 0.9255, VLM mean 0.9511, best improvement 0.0434 |
| VLM holdout latency | 4.62-4.99 seconds per image |
| Resident trusted Discord conversation | Accepted; 3/3 valid, p50 2.50 seconds, p95 2.56 seconds |
| Trusted Discord conversation during VLM | Accepted; 3/3 conversations and 3/3 VLM jobs valid; conversation p50 6.03 seconds, p95 6.43 seconds; VLM mean 5.20 seconds |
| Cold Main conversation during VLM from no resident Ollama model | Valid in 16.66 seconds; VLM valid in 4.80 seconds |

The first coexistence attempt exposed the direct cause of the reported minute-scale Discord regression:
the initial document-VLM preparation policy unloaded both Ollama models. The next Main request then paid
approximately 100 seconds to reload `gpt-oss:20b`. The final policy explicitly protects Main and unloads
only Micro. The trusted Discord benchmark is important: an operator-authenticated `/ask` probe is
correctly normalized to the dashboard path and includes a Micro classification pass, so it is not an
accurate measurement of unprefixed Discord conversation.

Long document compute remains allowed to take longer, but Discord confirms acceptance before ingress or
OCR begins. Ordinary resident Discord conversation retains the historical approximately three-second
baseline when VLM is idle. Active VLM work causes bounded contention, not model eviction or a minute-scale
reload.

## Verification evidence

- The exact clean code candidate passed the public-tree release scan before Ubuntu testing.
- The full Ubuntu suite ran with networking disabled: `563 passed`, with only the four known
  dependency deprecation warnings.
- Unit coverage proves acknowledgment ordering by holding attachment submission open and asserting that
  the processing message was already sent.
- Admission tests cover priority order, fencing, expired-lease recovery, authentication, strict payload
  bounds, and fail-closed model/lane policy.
- Architecture and Compose tests prove production GPU callers use the admission proxy and cannot resolve
  or reach the Ollama backend directly.
- VLM tests cover typed normalization, conventional comparison, mandatory review, worker fallback,
  provider failure, timeout, and source-loss prevention.
- The selected holdout uses generated non-sensitive occlusion variants. No private document text is
  present in source, reports, logs, or generic Core state.
- Core, accelerator admission, and PaddleOCR-VL recorded zero restarts during the final coexistence run;
  the RTX 3090 recorded no OOM.
- The pre-Phase-5 coordinated encrypted backup is retained at
  `$DOCUMENTS_STORAGE_ROOT/backups/pre-phase5-20260825-rc3` and its verification manifest reports no
  failures.

## Security and trust boundary

The admission proxy accepts only authenticated, bounded, non-streaming calls and allowlisted model/lane
combinations. It exposes no model names in readiness beyond counts. Ollama joins only the internal
accelerator-backend network. Core, ticket workers, email model paths, research, health checks, and the
document worker use the control-side proxy.

The VLM service receives one validated bounded image and no Paperless, Core, Discord, or archive
credential. It uses local read-only model artifacts, a read-only root, `1001:1001`, dropped capabilities,
`no-new-privileges`, no host port, and no public-egress network. Each inference runs in a bounded child
process so a timeout releases GPU memory. Generative output remains untrusted and mandatory-review-only.

## Backup and rollback

The Phase 4 application/image and the pre-Phase-5 coordinated backup remain available. VLM rollback is
additive and non-destructive:

1. Set `DOCUMENTS_PADDLEOCR_VL_ENABLED=false` in the protected Ubuntu environment.
2. Recreate the admission service, document worker, gateway, and Core with the required profiles and
   `--env-file .env`.
3. Stop the `documents-phase5` VLM service/profile.
4. Continue conventional PP-OCRv6/Docling processing and route difficult results to human review.
5. Preserve leases, processing runs, artifacts, originals, reviews, and the encrypted backup. Do not
   unload a live Main model from a rollback command.

Rolling back the admission proxy itself requires retagging the retained Phase 4 application and Compose
definition together because every current Ollama caller now fails closed without admission.

## Remaining review gates and risks

- A fresh image upload from the user is required after the final Discord restart to close live channel
  acceptance. Automated authorization, image recognition, acknowledgment ordering, attachment scoping,
  and queue handoff tests pass.
- VLM output can improve difficult pages but can also disagree or hallucinate. It never bypasses review
  or makes content active/searchable automatically.
- The accepted holdout is deliberately small and non-sensitive. Expand it before changing route
  thresholds or enabling document classes.
- A cold Main load remains slower than a resident turn. Main is protected from document eviction, while
  normal Ollama keep-alive policy still determines eventual idle expiration.
- Phase 6 classification, field extraction, corrections, metadata proposals, and downstream actions are
  separate work and remain off.
