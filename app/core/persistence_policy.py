from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PersistencePolicyName(StrEnum):
    STANDARD = "standard"
    SENSITIVE_DOMAIN = "sensitive_domain"
    RESTRICTED_READ = "restricted_read"
    EPHEMERAL = "ephemeral"


@dataclass(frozen=True)
class PersistencePolicy:
    name: PersistencePolicyName
    record_entity_context: bool
    record_recent_turns: bool
    record_conversation_history: bool
    record_memory: bool
    capture_ticket: bool


_POLICIES = {
    PersistencePolicyName.STANDARD: PersistencePolicy(
        name=PersistencePolicyName.STANDARD,
        record_entity_context=True,
        record_recent_turns=True,
        record_conversation_history=True,
        record_memory=True,
        capture_ticket=True,
    ),
    PersistencePolicyName.SENSITIVE_DOMAIN: PersistencePolicy(
        name=PersistencePolicyName.SENSITIVE_DOMAIN,
        record_entity_context=True,
        record_recent_turns=False,
        record_conversation_history=False,
        record_memory=False,
        capture_ticket=True,
    ),
    PersistencePolicyName.RESTRICTED_READ: PersistencePolicy(
        name=PersistencePolicyName.RESTRICTED_READ,
        record_entity_context=True,
        record_recent_turns=False,
        record_conversation_history=False,
        record_memory=False,
        capture_ticket=False,
    ),
    PersistencePolicyName.EPHEMERAL: PersistencePolicy(
        name=PersistencePolicyName.EPHEMERAL,
        record_entity_context=False,
        record_recent_turns=False,
        record_conversation_history=False,
        record_memory=False,
        capture_ticket=False,
    ),
}

_POLICY_RANK = {
    PersistencePolicyName.STANDARD: 0,
    PersistencePolicyName.SENSITIVE_DOMAIN: 1,
    PersistencePolicyName.RESTRICTED_READ: 2,
    PersistencePolicyName.EPHEMERAL: 3,
}


def persistence_policy(value: PersistencePolicy | PersistencePolicyName | str | None) -> PersistencePolicy:
    if isinstance(value, PersistencePolicy):
        return value
    try:
        name = PersistencePolicyName(str(value or PersistencePolicyName.STANDARD.value))
    except ValueError:
        name = PersistencePolicyName.EPHEMERAL
    return _POLICIES[name]


def persistence_policy_for_intent(intent: str) -> PersistencePolicy:
    """Return the fail-closed baseline before a skill result exists."""

    normalized = str(intent or "").strip().casefold()
    if normalized.startswith("documents."):
        return _POLICIES[PersistencePolicyName.RESTRICTED_READ]
    if normalized.startswith("email."):
        return _POLICIES[PersistencePolicyName.SENSITIVE_DOMAIN]
    return _POLICIES[PersistencePolicyName.STANDARD]


def most_restrictive_persistence_policy(
    *values: PersistencePolicy | PersistencePolicyName | str | None,
) -> PersistencePolicy:
    """Combine declarations without allowing a later layer to weaken policy."""

    policies = [persistence_policy(value) for value in values if value is not None]
    if not policies:
        return _POLICIES[PersistencePolicyName.STANDARD]
    return max(policies, key=lambda item: _POLICY_RANK[item.name])
