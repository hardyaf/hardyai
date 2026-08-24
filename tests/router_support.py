from __future__ import annotations

from typing import Any

from app.core.router import JarvisRouter as ProductionJarvisRouter


class PermissiveTestSkillRegistry:
    """Explicit test fixture for router tests that predate the SQL registry."""

    _EXECUTION_REFS = {
        "lists.": "app.skills.domains.lists.handler:run",
        "calendar.": "app.skills.domains.calendar.handler:run",
        "home.": "app.skills.domains.lights.handler:run",
        "email.": "app.skills.domains.email_agent.handler:run",
        "conversation.": "app.skills.domains.conversation.handler:run",
        "private_notes.": "app.skills.domains.private_notes.handler:run",
    }

    def resolve_skill(self, *, intent: str, user_id: str, agent_id: str) -> dict[str, Any] | None:
        del user_id, agent_id
        normalized = str(intent or "").strip().casefold()
        for prefix, execution_ref in self._EXECUTION_REFS.items():
            if normalized.startswith(prefix):
                return {
                    "skill_id": f"test.{prefix.rstrip('.')}",
                    "active": True,
                    "execution_ref": execution_ref,
                    "micro_enabled": True,
                    "intents": [normalized],
                }
        return None

    @staticmethod
    def resolve_agent_context(
        *,
        text: str,
        fallback_user_id: str,
        fallback_agent_id: str,
    ) -> dict[str, Any]:
        return {
            "agent_id": fallback_agent_id,
            "display_name": fallback_agent_id,
            "wake_alias": None,
            "normalized_text": text,
            "resolved_user_id": fallback_user_id,
            "personality_doc_path": None,
        }

    @staticmethod
    def get_agent_profile(agent_id: str) -> None:
        del agent_id
        return None

    @staticmethod
    def runtime_capability_catalog(*, user_id: str, agent_id: str) -> list[dict[str, Any]]:
        del user_id, agent_id
        return []

    @staticmethod
    def is_micro_allowed_for_intent(*, skill: dict[str, Any] | None, intent: str) -> bool:
        return isinstance(skill, dict) and bool(intent)

    @staticmethod
    def record_skill_run(**_: Any) -> None:
        return None


class RegistryBackedTestRouter(ProductionJarvisRouter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("skill_registry", PermissiveTestSkillRegistry())
        super().__init__(*args, **kwargs)
