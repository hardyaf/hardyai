from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.sqlite_store import SQLiteStore
from app.services.event_log import EventLogService
from app.skills.registry_service import SkillRegistryService


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScheduledJobsService:
    MAIN_IDLE_TRIGGER = "event:main_idle"
    CRITICAL_SKILLS_JOB_ID = "job.system.compile_critical_skills_on_main_idle"
    CRITICAL_SKILLS_JOB_NAME = "compile_critical_skills"
    ROUTINE_SESSION_ID = "system:routines"

    def __init__(
        self,
        *,
        sqlite_store: SQLiteStore,
        skill_registry: SkillRegistryService,
        event_log: EventLogService | None = None,
        critical_skills_output_path: str = "app/prompts/skills/critical_skills.md",
        critical_skills_min_level: int = 1,
        micro_skills_output_path: str = "app/prompts/micro_jarvis_skills.md",
    ) -> None:
        self._sqlite_store = sqlite_store
        self._skill_registry = skill_registry
        self._event_log = event_log
        self._critical_skills_output_path = str(critical_skills_output_path).strip() or "app/prompts/skills/critical_skills.md"
        self._critical_skills_min_level = max(0, int(critical_skills_min_level))
        self._micro_skills_output_path = str(micro_skills_output_path).strip() or "app/prompts/micro_jarvis_skills.md"

    def seed_defaults(self, *, ensure_compiled_artifacts: bool = True) -> None:
        now = _utc_now()
        self._sqlite_store.upsert_scheduled_job(
            job_id=self.CRITICAL_SKILLS_JOB_ID,
            skill_id="skill.conversation.general",
            job_name=self.CRITICAL_SKILLS_JOB_NAME,
            cron_expr=self.MAIN_IDLE_TRIGGER,
            payload={
                "output_path": self._critical_skills_output_path,
                "min_critical_level": self._critical_skills_min_level,
                "micro_output_path": self._micro_skills_output_path,
            },
            enabled=True,
            created_by="jarvis",
            created_at=now,
            updated_at=now,
        )
        if ensure_compiled_artifacts:
            self._ensure_compiled_critical_skills_file_exists()

    def handle_runtime_transition(self, *, previous_active: bool, current_active: bool) -> list[dict[str, Any]]:
        if not previous_active or current_active:
            return []
        return self.run_trigger(cron_expr=self.MAIN_IDLE_TRIGGER, reason="main_runtime_cooled_down")

    def run_trigger(self, *, cron_expr: str, reason: str) -> list[dict[str, Any]]:
        jobs = self._sqlite_store.list_scheduled_jobs(enabled_only=True, cron_expr=cron_expr)
        results: list[dict[str, Any]] = []
        for job in jobs:
            results.append(self._run_job(job=job, reason=reason))
        return results

    def _run_job(self, *, job: dict[str, Any], reason: str) -> dict[str, Any]:
        job_id = str(job.get("job_id") or "").strip()
        job_name = str(job.get("job_name") or "").strip().lower()
        payload = job.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        now = _utc_now()
        status = "failed"
        result: dict[str, Any] = {"status": "error", "message": "Unsupported scheduled job."}

        self._record_event(
            event_type="routine.job.started",
            payload={
                "job_id": job_id,
                "job_name": job_name,
                "reason": reason,
                "trigger": str(job.get("cron_expr") or ""),
            },
        )

        try:
            if job_name == self.CRITICAL_SKILLS_JOB_NAME:
                sync_result = self._skill_registry.sync_skills_from_markdown()
                output_path = str(payload.get("output_path") or self._critical_skills_output_path).strip()
                min_critical_level_raw = payload.get("min_critical_level")
                if isinstance(min_critical_level_raw, int):
                    min_critical_level = min_critical_level_raw
                elif isinstance(min_critical_level_raw, str) and min_critical_level_raw.strip().isdigit():
                    min_critical_level = int(min_critical_level_raw.strip())
                else:
                    min_critical_level = self._critical_skills_min_level

                compiled = self._skill_registry.compile_critical_skills_markdown(
                    output_path=output_path,
                    min_critical_level=min_critical_level,
                    compile_if_stale=True,
                )
                micro_output_path = str(payload.get("micro_output_path") or self._micro_skills_output_path).strip()
                micro_compiled = self._skill_registry.compile_micro_skills_markdown(
                    output_path=micro_output_path,
                    compile_if_stale=True,
                )
                compile_statuses = {
                    str(compiled.get("status") or "").strip().lower(),
                    str(micro_compiled.get("status") or "").strip().lower(),
                }
                status = "skipped" if compile_statuses == {"skipped"} else "ok"
                result = {
                    "status": status,
                    "sync": sync_result,
                    "compiled": {
                        "critical": compiled,
                        "micro": micro_compiled,
                    },
                }
            else:
                status = "failed"
                result = {
                    "status": "error",
                    "message": f"Unsupported scheduled job: {job_name}.",
                }
        except Exception as exc:  # pragma: no cover - defensive logging path
            status = "failed"
            result = {
                "status": "error",
                "message": str(exc),
            }

        self._sqlite_store.mark_scheduled_job_run(
            job_id=job_id,
            last_status=status,
            last_run_at=now,
            updated_at=now,
        )
        self._record_event(
            event_type="routine.job.completed" if status == "ok" else "routine.job.failed",
            payload={
                "job_id": job_id,
                "job_name": job_name,
                "status": status,
                "reason": reason,
                "trigger": str(job.get("cron_expr") or ""),
                "result": result,
            },
        )
        return {
            "job_id": job_id,
            "job_name": job_name,
            "status": status,
            "result": result,
        }

    def _ensure_compiled_critical_skills_file_exists(self) -> None:
        jobs = self._sqlite_store.list_scheduled_jobs(
            enabled_only=False,
            cron_expr=self.MAIN_IDLE_TRIGGER,
        )
        critical_job = next(
            (job for job in jobs if str(job.get("job_name") or "").strip().lower() == self.CRITICAL_SKILLS_JOB_NAME),
            None,
        )
        payload = (critical_job or {}).get("payload")
        if not isinstance(payload, dict):
            payload = {}
        critical_path_value = str(payload.get("output_path") or self._critical_skills_output_path).strip()
        micro_path_value = str(payload.get("micro_output_path") or self._micro_skills_output_path).strip()
        critical_path = Path(critical_path_value)
        micro_path = Path(micro_path_value)
        if not critical_path.is_absolute():
            critical_path = (Path.cwd() / critical_path).resolve()
        if not micro_path.is_absolute():
            micro_path = (Path.cwd() / micro_path).resolve()
        if critical_path.exists() and micro_path.exists():
            return
        self._run_job(
            job=critical_job
            or {
                "job_id": self.CRITICAL_SKILLS_JOB_ID,
                "job_name": self.CRITICAL_SKILLS_JOB_NAME,
                "cron_expr": self.MAIN_IDLE_TRIGGER,
                "payload": payload,
            },
            reason="startup_missing_compiled_file",
        )

    def _record_event(self, *, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_log is None:
            return
        self._event_log.record(
            event_type=event_type,
            session_id=self.ROUTINE_SESSION_ID,
            payload=payload,
        )
