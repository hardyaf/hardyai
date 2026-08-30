# Qwen3.8 Main Model Migration Plan

Status: phases 0-4 implemented; production cutover blocked by the single-GPU coexistence gate

Last verified: 2026-08-26

## Objective

Replace the Main Jarvis model `gpt-oss:20b` with `qwen3.8:27b` without changing MicroJarvis, weakening typed action boundaries, or degrading Discord and OCR/VLM service. Keep `gpt-oss:20b` installed and immediately selectable as the rollback model.

The candidate uses a 32,768-token Jarvis context even though the upstream model supports a larger native window. That bounded context matches the current deployed Main lanes and prevents context growth from becoming an unmeasured GPU-memory change.

## Current verified baseline

- HardyAI is the canonical source checkout; Hardybot `/home/codex/jarvis-poc` is the only build, test, model, GPU, and deployment host.
- Production currently selects `gpt-oss:20b` for Main and `qwen2.5:7b` for Micro.
- Main conversation, repair, web-research decision, email semantic work, and action-ticket review all derive from the configured Main model.
- Production Main contexts are already 32,768; some source/example defaults were stale at 12,288 and are corrected by this migration.
- Accelerator admission is the only application path to Ollama and PaddleOCR-VL.
- The server currently exposes one RTX 3090 (24 GB), not the intended two-GPU topology. Qwen candidate benchmarking may proceed, but production cutover requires the coexistence gate below.
- The legacy named Ollama volume is on the 98 GB OS filesystem, which had only 5.6 GB free at the migration gate. Model storage must use the isolated `models/ollama` directory on the encrypted storage NVMe before Qwen is pulled.

## Reuse map

| Concern | Decision | Existing authority | Migration use |
| --- | --- | --- | --- |
| Model calls | reuse | Existing Ollama backends and `OllamaCallObserver` | Add one typed thinking option; do not create a Qwen-specific client. |
| Model selection | adapt | Existing `JARVIS_MAIN_MODEL` and `Settings` | Add a candidate allowlist without changing the active production tag. |
| GPU serialization | adapt | Accelerator admission lease queue | Admit the candidate and preserve protected/evictable semantics. |
| GPU placement | adapt | Docker Compose device reservation | Select explicit device IDs instead of nondeterministic `count: 1`. |
| Quality evaluation | adapt | Existing Main contracts and benchmark scripts | Add a content-free A/B acceptance harness and reuse Discord/coexistence benchmarks. |
| Telemetry | reuse | Existing content-free Ollama observer and event log | Record timing, token counts, retries, and outcomes; never persist model thinking text. |
| Durable state | reuse | Existing SQLite/runtime authorities | No new database, queue, session, memory, or job subsystem. |

## Data ownership and side effects

| Datum or side effect | Authoritative owner | Persistence/visibility | Deletion or rollback behavior |
| --- | --- | --- | --- |
| Active Main model tag | Protected deployment `.env` on Hardybot | Loaded into Compose/Jarvis at startup | Restore `JARVIS_MAIN_MODEL=gpt-oss:20b` and recreate affected services. |
| Candidate model tag | Compose admission configuration | Runtime configuration only | Remove candidate env entry after evaluation if abandoned. |
| Model blobs and manifest | Ollama bind mount under the protected storage NVMe | Local server storage | Keep both models through the observation window; remove only with explicit approval. |
| Per-lane thinking policy | Tracked source defaults plus deployment env overrides | Runtime configuration; safe values may appear in status | Roll back source/config or override individual lanes. Thinking content is not stored. |
| Acceptance results | Operator-selected benchmark output path | Content-free JSON artifact | May be archived or deleted independently; contains no prompt/response text. |
| GPU assignment | Protected deployment `.env` | Docker device reservation | Restore the previous explicit device selection or prior Compose revision. |

## Workload policy

Thinking is a workload property, not a model-wide switch:

| Lane | Initial policy | Reason |
| --- | --- | --- |
| Main conversation | `low` | Preserve useful reasoning while bounding conversational latency. |
| Email summary | `low` | Quality-sensitive semantic synthesis. |
| Main repair | `false` | Strict action JSON and low latency are more important than hidden deliberation. |
| Main turn decision | `false` | Typed routing should be direct, deterministic, and easy to validate. |
| Research decision | `false` | Small structured routing decision. |
| Email classifier | `false` | Small constrained classification. |
| Action-ticket review | `false` initially | Structured validation; increase only if acceptance evidence warrants it. |
| MicroJarvis | unchanged | Remains `qwen2.5:7b`, 4,096 context, explicit `!` command boundary. |

Only the separate Ollama `thinking` response field may contain reasoning. Jarvis does not place it in replies, events, traces, sessions, or durable memory.

## Phases and gates

### Phase 0 - topology and rollback readiness

1. Record GPU UUIDs, driver/runtime versions, Compose resolution, current container/image digests, model list/manifests, service health, and current Main latency.
2. Back up the protected `.env` and identify the exact source revision/deployment image used for rollback.
3. Use explicit GPU device IDs for Ollama and PaddleOCR-VL. If a second GPU is installed, assign Ollama and VLM deliberately by UUID after memory/load measurements; do not infer order from PCI numbering.
4. Keep Ollama offline/cloud-disabled in production.
5. Copy the existing Ollama model store into `/mnt/hardyai-documents/models/ollama`, verify its checksum/file count, and retain the old named volume as a rollback source until the observation window ends.

Gate: rollback commands are verified and the effective Compose configuration names only intended GPU devices.

### Phase 1 - reusable controls and candidate admission

1. Add typed per-lane Ollama thinking configuration.
2. Permit only booleans or the allowlisted effort levels `low`, `medium`, `high`, and `max` through accelerator admission.
3. Add a candidate-model allowlist. Candidate models are evictable while inactive and become protected only when selected as the active Main model.
4. Align source/example context defaults at 32,768.

Gate: unit and architecture tests prove payload, allowlist, and configuration behavior. Production still runs GPT-OSS.

### Phase 2 - deterministic acceptance harness

1. Run the same content-free case manifest against GPT-OSS and Qwen.
2. Validate conversation non-emptiness, typed turn modes/intents, structured repair output, authorization-safe behavior, latency, output-token exhaustion, and adaptive retry loops.
3. Write only case IDs, expected/observed modes and intents, pass/fail, timing, and content-free Ollama metrics.

Gate: Qwen has no safety/contract regressions and meets the configured pass-rate and latency thresholds.

### Phase 3 - provision Qwen on Hardybot

1. Pull `qwen3.8:27b` on Hardybot through the managed Ollama service.
2. Capture the immutable local model digest and manifest/BOM.
3. Verify an offline restart and an admitted candidate request before changing `JARVIS_MAIN_MODEL`.

Gate: the exact model survives an offline service restart and is callable only through admission.

### Phase 4 - GPU and OCR coexistence

1. Run cold and warm A/B acceptance cases.
2. Run the existing accelerator coexistence benchmark with a representative OCR image and simultaneous Main conversation.
3. Inspect GPU residency, OOM/restart events, admission queue/lease recovery, and time-to-first acknowledgement/final response behavior.

Gate: every Main and VLM request succeeds, no service restarts or OOM events occur, expired leases recover, and conversational p95 is within the approved threshold. Failure leaves GPT-OSS active.

### Phase 5 - controlled cutover

1. Set `JARVIS_MAIN_MODEL=qwen3.8:27b` in the protected Hardybot `.env`.
2. Recreate only the model-aware admission/Jarvis services required by the configuration change.
3. Verify `/ready`, runtime lane status, Discord unprefixed conversation, typed action routing, OCR asynchronous acknowledgement/final notification, email semantic lanes, and action-ticket review.

Gate: all live probes pass before declaring the cutover complete.

### Phase 6 - observation and tuning

1. Observe content-free latency, token escalation, invalid structured output, dead letters, admission contention, and GPU residency.
2. Tune thinking or output budgets per lane from evidence. Do not lower quality merely to avoid token use; adaptive output growth remains available unless it enters a bounded failed loop.
3. Retain GPT-OSS through the observation window.

## Acceptance criteria

- Qwen passes every safety-critical typed-action case and has no unauthorized execution.
- Conversation, repair, classification, summarization, and review lanes return valid outputs without exposing thinking.
- The content-free benchmark has no failed token-exhaustion loop.
- Discord conversation and OCR/VLM coexistence pass with no OOM or container restart.
- The active model, exact model digest, context, thinking policy, GPU assignment, and rollback are operator-inspectable.
- MicroJarvis behavior and explicit Discord command semantics are unchanged.

## Rollback

1. Restore `JARVIS_MAIN_MODEL=gpt-oss:20b` in the protected Hardybot `.env`.
2. Restore the prior per-lane thinking overrides if any were changed.
3. Recreate admission and Jarvis with `docker compose --env-file .env -f deploy/docker/compose.yaml ...`.
4. Verify health, runtime model status, one Discord conversation turn, one typed action, and one OCR request.
5. Preserve failed benchmark results and Qwen model metadata for diagnosis; do not delete runtime data or the rollback model.

## Deferred dual-GPU expansion

This is the Main-model migration, not the later dual-GPU coding-mode plan. The second GPU may be used to isolate conversational Ollama from OCR/VLM after both devices are physically visible and measured. Any future coding model remains a separate capability with its own admission lane, authorization, context, and acceptance gates.

## Implementation record - 2026-08-26

### Release and rollback facts

- Pre-migration application image: `sha256:5842c2285e3f8569ffe0ff5a0393e401368e8f855671dd5d2cc96e86ef6ca2d4`, retained as `jarvis-poc-app:pre-qwen38-20260826`.
- Migration infrastructure image: `sha256:0cc178ebc3da0428b399612292f68518543bbe6a26d09530d88fcb50312af509`.
- Protected rollback snapshot: `/mnt/hardyai-documents/backups/pre-qwen38-20260826/`.
- Source archive SHA-256: `37647fa1e2613d162bdf879c7111d0ca431aa1b552a1c405f7510535ea3736b8`.
- Protected `.env` backup SHA-256: `2d9ab27d5fb3670a6b7af0ab89af9fe9d3df5312abf924f104380744aa2c4f7f`.

### Model storage and bill of materials

- The original named volume and the new NVMe store each contained 19 files with the same relative-path/size manifest hash, `0b8bd3e9543d51d48357e904c0ae6b2a2c8117018ef3aa0b5323515fe7cf0cf6`, before Qwen provisioning.
- Ollama now uses the bind mount `/mnt/hardyai-documents/models/ollama`; the original named volume remains untouched for rollback.
- Qwen local manifest digest: `22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643`.
- Qwen model blob: `f5f1dd8920d417aac2718b0bda3403da274301efdd6760b4f0f4b864ff2ad57d`.
- Qwen projector blob: `ac3714bfdddeca31351f2752bf1a63f266f4df87c0b68c895e44945ca704448e`.
- GPT-OSS rollback manifest digest: `17052f91a42e97930aa6e28a6c6c06a983e6a58dbb00434885a0cf5313e376f7`.
- Provisioning used a one-purpose egress network attached only to Ollama. The network was removed automatically after Ollama verified the blob digests and wrote the manifest.

### Verification results

| Gate | Result |
| --- | --- |
| Focused migration tests on Ubuntu | 38 passed |
| Full suite on Ubuntu | 658 passed; six existing deprecation warnings |
| Compose render | Accepted |
| GPT-OSS synthetic acceptance | 6/6; p50 1.64 s; p95 7.23 s; no failed token loop |
| Qwen synthetic acceptance | 6/6; p50 2.79 s; p95 20.74 s including cold load; no failed token loop |
| Qwen safety-critical unauthorized action | Passed; failed closed to conversation |
| Qwen protected + VLM on one RTX 3090 | Failed: Main succeeded in 20.90 s, VLM returned an HTTP failure |
| Restored GPT-protected/Qwen-evictable control | Passed: Main 8.84 s, VLM 4.74 s |
| Final explicit-device GPT/VLM control | Passed: Main 5.90 s, VLM 5.27 s; zero restarts/OOM flags |
| Post-infrastructure-deploy Discord path | Passed twice at 2.08-2.42 s |

Qwen occupies 20,022 MiB at 32,768 context on the only visible 24 GB RTX 3090. The identical VLM fixture succeeds after admission unloads Qwen, and all containers remain healthy with zero OOM flags or restarts. This isolates the failed production gate to insufficient simultaneous VRAM rather than a model-quality, routing, fixture, provider-health, or lease-recovery problem.

### Deployed state and remaining blocker

The reusable thinking controls, candidate admission, explicit device `0` selection for both Ollama and PaddleOCR-VL, NVMe model store, acceptance harness, and Qwen model are deployed. Production Main remains explicitly `gpt-oss:20b`, with 32,768 context, conversation thinking `low`, and turn-decision thinking disabled. Qwen remains an admitted but evictable candidate.

Do not perform Phase 5 cutover until either:

1. a second GPU is physically visible to Docker and Ollama/VLM receive explicit, tested device assignments; or
2. a revised single-GPU policy (smaller context/model or deliberate Main eviction/reload) is explicitly approved and passes the same Discord/OCR gates.
