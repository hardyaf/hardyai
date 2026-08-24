from __future__ import annotations

from typing import Any

from app.tickets.repository import TicketRepository


class ExternalIdentityService:
    """Resolves immutable external identities to household users and agents."""

    CHILD_POLICY_PROFILES = {"child_conversation_only", "child_limited"}

    def __init__(self, *, repository: TicketRepository, skill_registry: Any) -> None:
        self._repository = repository
        self._skill_registry = skill_registry

    def resolve(self, *, source: str, external_user_id: str) -> dict[str, Any] | None:
        if not str(source or "").strip() or not str(external_user_id or "").strip():
            return None
        return self._repository.get_identity_binding(
            source=source,
            external_user_id=external_user_id,
            active_only=True,
        )

    def upsert(self, **values: Any) -> dict[str, Any]:
        agent_id = str(values.get("agent_id") or "").strip().lower()
        profile = self._skill_registry.get_agent_profile(agent_id)
        if profile is None or not bool(profile.get("active")):
            raise ValueError("Identity binding requires an active agent profile.")
        policy_profile = str(values.get("policy_profile") or "adult").strip().lower()
        age_band = str(values.get("age_band") or "").strip().lower() or None
        if age_band and policy_profile == "adult":
            raise ValueError("A child age band cannot use the adult policy profile.")
        return self._repository.upsert_identity_binding(**values)

    @classmethod
    def is_child_binding(cls, binding: dict[str, Any] | None) -> bool:
        if not binding:
            return False
        return bool(binding.get("age_band")) or str(binding.get("policy_profile") or "").lower() in cls.CHILD_POLICY_PROFILES

