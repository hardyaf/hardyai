# ADR-002: Contact authority remains unselected

Status: Accepted for the Phase 8 capability-gated release

Date: 2026-08-25

## Context

HardyAI can extract private contact fields from business cards, but this repository has no canonical
mutable person/contact directory. Calendar aliases are configuration hints, and external identity
bindings are authentication records. Neither may become a contact authority by accident.

## Decision

Introduce a provider-neutral `ContactProvider` boundary and deterministic, explainable matching, but
do not ship a production adapter or executor. The Documents database may retain a versioned proposal,
candidate comparison, evidence reference, and shared-review link; it must not become a parallel contact
directory. Production business-card proposals therefore report `capability_unavailable` until a later
ADR selects one authority and defines ownership, merge semantics, permissions, backup, read-back, and
rollback.

Every future create, update, or merge remains human-approved. Merely approving a proposal while the
capability is unavailable performs no write.

## Consequences

- Business cards can be extracted and inspected now without inventing hidden contact storage.
- Exact email/phone matches and fuzzy name/organization scores are explainable and tested.
- Ambiguous candidates are never auto-selected.
- Contact execution and source provenance to an accepted contact remain blocked until an authority is
  explicitly selected and implemented.
