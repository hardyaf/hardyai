# OCR Business-Card Review Presentation Correction

Status: deployed and verified on the authoritative Ubuntu runtime

Date: 2026-08-26

## Outcome

A `needs_review` business-card result is no longer presented as a hard OCR failure. The authorized uploader
receives a bounded candidate preview labeled as unverified. Raw OCR blocks remain review-gated, and the
preview performs no contact mutation.

The deterministic structured extractor is versioned from 1 to 2 and now conservatively recognizes
organization and job-title lines in addition to name, email, phone, and website. Low-confidence observations
continue to create shared field-correction reviews.

An authorized user can now reply conversationally with a correction such as `the company is Field Works`
or confirm all displayed fields. A correction can also add a missing allowlisted business-card field. Jarvis
immediately displays the effective field set with `(corrected)` and `(confirmed)` labels. This still does not
create or update a canonical contact.

## Reuse map

| Need | Decision | Existing authority reused |
| --- | --- | --- |
| Discord follow-up delivery | Adapt | Existing `document.discord_completion.v1` durable job and completion service |
| Authorized document read | Adapt | Existing `DocumentQueryService` and recent-attachment Discord scope |
| Candidate contact fields | Reuse | Existing DocumentGateway `fields` port and effective field observations |
| Human review | Reuse | Existing shared `HumanReviewService` and immutable review records |
| Applied field correction | Adapt | Existing `document_field_decisions` projection and effective-field precedence |
| Conversational correction | Adapt | Existing Documents handler, Main intent contracts, context resolver, and gateway client |
| Business-card extraction | Adapt | Existing deterministic `StructuredExtractorPort` implementation |
| Contact persistence | Deferred | ADR-002 remains authoritative; no contact provider or parallel address book was added |

Two narrow Documents-domain services separate isolated field application from Core review coordination.
No new database, queue, scheduler, identity model, approval system, contact store, or provider was introduced.

## Data ownership

| Datum or side effect | Authority | Change |
| --- | --- | --- |
| Original card image | Paperless | None |
| OCR runs, classifications, and field observations | Documents database | Extractor version 2 may append organization/job-title observations on future processing runs |
| Review items and Discord completion jobs | Core database | Existing lifecycle is reused; correction reviews store actor, hashes, and opaque receipts only |
| Corrected or confirmed field value | Documents database | Version-bound field decisions override machine observations and survive reprocessing |
| Discord response | Core Discord adapter | Message may contain only allowlisted, bounded candidate contact fields for the scoped uploader/channel |
| Canonical contact | Future selected contact provider | None; no create/update occurs |

The preview is an ephemeral restricted-read projection. It is not a second source of truth and does not
change observation, review, proposal, or contact state.

## Security and review boundary

- Preview is limited to `full_name`, `organization`, `job_title`, `email`, `phone`, and `website`.
- Values are bounded and projected without raw OCR text, evidence payloads, hashes, provider references, or
  unsupported fields.
- Only the existing authenticated operator scope or the exact recent Discord attachment scope can reach the
  document read path.
- `identity` and `highly_restricted` documents remain excluded by classification and field-access policy.
- Every machine-only value is labeled unverified; a prior human correction or confirmation may be labeled
  confirmed.
- The shared Core review ledger never receives the corrected value. It stores a SHA-256-bound decision and
  an opaque Documents receipt; the encrypted Documents database remains the sole value owner.
- Replaying the same request is idempotent, and confirming remaining fields cannot erase a prior correction.
- Preview-only presentation never approves a review. Only an explicit authorized correction/confirmation
  approves its exact version-bound field decision, and neither path executes `contacts.create_or_update`.

## Verification and rollback

Targeted Ubuntu tests cover generic review-held documents, allowlisted business-card previews, unsupported
field suppression, raw-evidence suppression, extractor versioning, organization/title extraction, and the
durable Discord completion path.

Initial preview verification completed with 24 targeted tests and the complete 623-test offline suite
passing. Its five application services ran image digest
`sha256:89ddcbee7cfa826bc493a45970de48e3205954aedfa1d7ef32d1c343051134fd`. A content-safe live query against
the existing review-held card returned the truthful preview with
four allowlisted field names and no raw evidence. That prior processing run was not mutated or reprocessed;
organization and job-title extraction version 2 applies to new or explicitly reprocessed runs.

Rollback restores the prior query-service and extractor files or retags the retained pre-release image.
Existing originals, processing runs, field observations, reviews, proposals, completion jobs, and contact
authority state must be preserved. The retained rollback image is
`sha256:edfbb386e4e9801eb02d19c234ee086b369cf5d199465f99cf6e096bca40e95f`.

The conversational correction extension passed 51 focused Ubuntu tests and the complete 626-test offline
suite. Coverage includes existing-value correction, missing-field addition, confirm-all behavior, exact
recent-attachment scope, version/binding checks, retry safety, and proof that corrected values do not enter
the Core review ledger. It is deployed to all five application services as image digest
`sha256:f97129ac4e8cb169c9193609002e8e411f4a368290c457a2ea3bf1d959ab7ffd`; every service reports healthy with
zero restarts. The live SQL-backed capability catalog exposes both typed intents, and the DocumentGateway
mounts the fixed-operation field-decision route. The immediate rollback image is retained as
`jarvis-poc-app:rollback-ocr-corrections-20260826` at digest
`sha256:89ddcbee7cfa826bc493a45970de48e3205954aedfa1d7ef32d1c343051134fd`.
