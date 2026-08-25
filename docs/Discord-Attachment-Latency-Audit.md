# Discord Attachment And Latency Audit

Status: deployed and verified on `hardybot`

Date: 2026-08-25

## Latency finding

Discord authorization and routing remained correct. Explicit `!` commands enter Micro;
unprefixed messages intentionally bypass Micro and enter Main. Across 147 completed Discord
turns, explicit Micro turns had a 3.36 second median. Before the incident, ordinary Main
turns were typically 5-8 seconds.

The regression was caused by a stale NVIDIA/NVML binding in the long-running Ollama
container. `gpt-oss:20b` reported `100% CPU`, the RTX 3090 was idle, and recent Main turns
took 56-108 seconds. Model telemetry showed roughly 35 seconds of CPU prompt evaluation for
about 7,600 input tokens before generation began. The host driver and a disposable GPU
container were healthy, proving the fault was isolated to the existing Ollama container.

Recreating only Ollama restored `100% GPU` residency. A warm content-free probe completed in
0.49 seconds. After the attachment deployment recreated the model runtime with its hardened
health check, full `/ask` Main probes completed in 10.24 seconds cold and 4.26 seconds warm.
Both configured models reported `100% GPU`. The Compose health check now requires both NVIDIA
visibility and the Ollama API so this fallback becomes visible as an unhealthy service instead
of silently degrading.

## Discord attachment boundary

Authorized Discord channels now recognize PDF, JPEG, and PNG attachments, including
attachment-only messages. Core passes only bounded metadata and the signed Discord CDN URL to
an isolated ingress sidecar. The sidecar:

- authenticates the internal request;
- accepts only allowlisted HTTPS Discord CDN attachment paths;
- enforces filename, MIME, metadata/CDN size caps, exact streamed CDN length, timeout, and
  per-message limits;
- preflights an opaque durable ingress receipt for retry idempotency;
- streams one file per DocumentGateway upload without buffering the source in Core; and
- returns only a bounded receipt, never document content.

The sidecar has no Core database, document storage, Discord bot token, model access, or host
port. It alone bridges a dedicated public-egress network to the internal DocumentGateway
control network. DocumentGateway, Paperless data services, the document worker, and Docling
remain no-egress.

## Deployment and operational verification

Attachment intake is deployed with the `documents`, `documents-phase3`, and
`discord-attachments` profiles. The final clean exported tree passed 542 Linux tests with networking
disabled. Jarvis, Ollama, the document worker, DocumentGateway, and the attachment ingress
sidecar were healthy with zero restarts and no error markers after cutover. The live internal
auth, source-identity rejection, and durable-receipt preflight boundaries passed. A Core
privacy scan found no Discord CDN path exposure across 43 tables and 466 text columns, and all
25 existing document durable-job payloads passed the opaque-field audit.

The first worker recreation exposed a Compose scoping defect: the enabled attachment flag from
the shared environment reached the offline document processes, which correctly failed closed.
The rollout stopped at that boundary, the worker was restored, and the final Compose contract
now explicitly forces the flag off for both the document worker and DocumentGateway. A
regression test enforces that isolation.

The first real JPEG acceptance test exposed that Discord's attachment metadata size can differ
from the byte length served by its signed CDN URL. The original exact comparison rejected the
valid image. The ingress now treats the signed CDN `Content-Length` as authoritative for the
multipart transfer, applies the same hard upload cap to both sizes, and verifies the streamed
byte count exactly against the CDN length. Regression tests cover the differing-size success
case and an oversized CDN representation. The original failed attachment was retried without
reading or displaying its contents and reached `ready` with a durable receipt, no failure code,
and no new service error markers.
