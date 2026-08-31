# OCR Negative-Feedback GPU Escalation

Status: deployed and verified on the authoritative Ubuntu runtime

Date: 2026-08-26

## Outcome

An authorized Discord user can say that Jarvis read their recent image incorrectly or incompletely. Main
maps that feedback to the typed `documents.escalate_ocr` capability. The Documents service appends an
immutable PaddleOCR-VL fallback run, submits it through the shared GPU-admission lane, immediately replies
that deeper processing has started, and uses the existing durable completion path to post the terminal
review-only result back to the same Discord channel.

This behavior is intentionally distinct from a correction and from training. An exact replacement such as
`the company is Field Works` continues to use `documents.correct_field`. Negative feedback requests a new
inference run; it does not change weights, construct a training set, or promote model output as verified.

## Reuse map

| Need | Decision | Existing authority reused |
| --- | --- | --- |
| Interpret negative OCR feedback | Adapt | Documents Markdown capability contract and Main typed turn decision |
| Select deeper image processing | Adapt | Existing `DocumentReprocessingService` with a provider-neutral `review_fallback` tier |
| Execute GPU work | Reuse | Existing `vlm_fallback` route, `gpu_vlm` lane, shared accelerator admission, and PaddleOCR-VL provider |
| Persist processing state | Reuse | Existing append-only Documents processing runs and durable document job ledger |
| Correlate the response | Adapt | Existing content-free `document.discord_completion.v1` job with an exact processing-run ID |
| Deliver the final result | Reuse | Existing Discord completion service and authorized same-channel response policy |
| Human review and correction | Reuse | Existing shared review authority and version-bound field decisions |
| Model training | Deferred | No online training, weight mutation, or automatic promotion was introduced |

No new queue, scheduler, database, identity model, review store, Discord worker, or provider process was
added.

## Data ownership

| Datum or side effect | Authority | Change |
| --- | --- | --- |
| Original image | Paperless | None; remains immutable archive evidence |
| CPU and GPU OCR runs | Documents database | A new fallback run links to the latest conventional OCR run for the same source version |
| Run scheduling, lease, retries, and dead letter | Documents durable job ledger | Existing bounded lifecycle is reused on the GPU lane |
| Completion subscription | Core durable job ledger | Stores only opaque Discord IDs, document ID, and processing-run ID |
| Candidate OCR and extracted fields | Documents database | GPU output remains review-only and does not replace human-corrected values |
| Discord message | Discord adapter | Immediate processing acknowledgement followed by one terminal same-channel result |
| Human correction | Documents database plus opaque Core review receipt | Existing version-bound correction flow is unchanged |
| Training examples or model weights | None | No training data or weight updates are created |

## Authorization and lifecycle

- Discord authorization remains limited to the exact recent attachment IDs minted by the trusted adapter
  for that user and channel.
- Only JPEG and PNG documents can enter the review-fallback tier. The isolated Documents service enforces
  this independently of Main's decision.
- The API exposes a content-free, owner-authorized processing-run status seam so the completion worker can
  wait for the exact GPU run. It does not expose OCR content or provider details.
- A repeated request ID is idempotent. The fallback run records the preceding conventional OCR run when one
  exists, enabling later disagreement review without overwriting either result.
- Failed or cancelled GPU work preserves the source and earlier result and posts a bounded failure notice.
- Human-confirmed and human-corrected values retain precedence; fallback inference cannot silently undo
  them.

## Verification and rollback

The focused Ubuntu suite covers fallback route selection, image-only enforcement, idempotent run creation,
CPU-to-GPU evidence linkage, exact recent-attachment authorization, typed result receipts, content-free
completion jobs, exact-run polling, same-channel registration, and context requirements. The final focused
suite passed 72 tests. Bytecode compilation and the complete offline suite also passed, with 633 tests and
four dependency deprecation warnings.

The final image is deployed to the accelerator-admission, document-worker, document-gateway, Jarvis, and
Discord attachment-ingress services at digest
`sha256:71502628849e3a5d52b7a0e6a70965f529da81d49b40d38733440df286cc8ed4`. All five services reported
healthy with zero restarts, the live SQL catalog resolved `documents.escalate_ocr`, and Discord reconnected.
Both `qwen2.5:7b` and `gpt-oss:20b` were verified at 100% GPU after inference; a warm Main control turn
completed in 3.41 seconds.

A final live test used the real Discord-adapter principal, the literal message `it wasn't right`, and a
nonexistent scoped document ID. The request resolved to `documents.escalate_ocr` with no missing fields.
The terminal `error` was the expected nonexistent-document response, proving the route without changing a
real card or scheduling a document-processing job. Contract tests also prove that an explicit field plus
replacement remains `documents.correct_field`.

Rollback retags the retained pre-release application image and recreates only the five affected application
services. Existing originals, CPU/GPU runs, field observations, reviews, corrections, and durable jobs must
be preserved. Disabling `DOCUMENTS_PADDLEOCR_VL_ENABLED` also fails closed for future escalation requests;
it must not delete already-created processing evidence.

The immediate rollback tag is `jarvis-poc-app:rollback-ocr-gpu-feedback-20260826` at digest
`sha256:f97129ac4e8cb169c9193609002e8e411f4a368290c457a2ea3bf1d959ab7ffd`.
