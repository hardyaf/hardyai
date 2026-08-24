from __future__ import annotations

from typing import Any

from app.db.sqlite_store import SQLiteStore


class RuntimeStateRepository:
    """Bounded session/event persistence adapter."""

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def upsert_session(self, *args: Any, **kwargs: Any) -> None:
        self._store.upsert_session(*args, **kwargs)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self._store.get_session(session_id)

    def insert_event(self, *args: Any, **kwargs: Any) -> None:
        self._store.insert_event(*args, **kwargs)

    def recent_events(self, limit: int) -> list[dict[str, Any]]:
        return self._store.recent_events(limit)


class SkillCatalogRepository:
    """Bounded skill/catalog/agent-profile persistence adapter."""

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def upsert_model_boot_memory(self, **kwargs: Any) -> None:
        self._store.upsert_model_boot_memory(**kwargs)

    def list_model_boot_memory(self, model_name: str) -> list[dict[str, Any]]:
        return self._store.list_model_boot_memory(model_name)

    def upsert_skill(self, **kwargs: Any) -> None:
        self._store.upsert_skill(**kwargs)

    def list_skills(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        return self._store.list_skills(active_only=active_only)

    def find_skill_for_intent(
        self,
        *,
        intent: str,
        user_id: str,
        agent_id: str | None = None,
    ) -> dict[str, Any] | None:
        return self._store.find_skill_for_intent(
            intent=intent,
            user_id=user_id,
            agent_id=agent_id,
        )

    def record_skill_run(self, **kwargs: Any) -> str:
        return self._store.record_skill_run(**kwargs)

    def upsert_agent_profile(self, **kwargs: Any) -> None:
        self._store.upsert_agent_profile(**kwargs)

    def list_agent_profiles(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        return self._store.list_agent_profiles(active_only=active_only)

    def get_agent_profile(self, agent_id: str) -> dict[str, Any] | None:
        return self._store.get_agent_profile(agent_id)

    def find_agent_by_wake_alias(self, alias: str) -> dict[str, Any] | None:
        return self._store.find_agent_by_wake_alias(alias)


class ScheduledJobsRepository:
    """Bounded scheduler control-plane persistence adapter."""

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def upsert_scheduled_job(self, **kwargs: Any) -> None:
        self._store.upsert_scheduled_job(**kwargs)

    def list_scheduled_jobs(
        self,
        *,
        enabled_only: bool = True,
        cron_expr: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._store.list_scheduled_jobs(
            enabled_only=enabled_only,
            cron_expr=cron_expr,
        )

    def mark_scheduled_job_run(self, **kwargs: Any) -> None:
        self._store.mark_scheduled_job_run(**kwargs)
