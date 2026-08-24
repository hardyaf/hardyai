from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from app.db.sqlite_store import SQLiteStore
from app.services.event_log import EventLogService
from app.services.scheduled_jobs_service import ScheduledJobsService
from app.skills.registry_service import SkillRegistryService


def test_main_idle_transition_runs_critical_skills_compile_job():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-scheduled-jobs-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "scheduled.db"
        output_path = scratch / "critical_skills.md"
        micro_output_path = scratch / "micro_jarvis_skills.md"
        store = SQLiteStore(database_path=str(db_path))
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()
        registry.sync_skills_from_markdown()
        event_log = EventLogService(persistence=store)
        service = ScheduledJobsService(
            sqlite_store=store,
            skill_registry=registry,
            event_log=event_log,
            critical_skills_output_path=str(output_path),
            critical_skills_min_level=1,
            micro_skills_output_path=str(micro_output_path),
        )
        service.seed_defaults()

        jobs = store.list_scheduled_jobs(
            enabled_only=True,
            cron_expr=ScheduledJobsService.MAIN_IDLE_TRIGGER,
        )
        assert any(str(job.get("job_name") or "").strip() == ScheduledJobsService.CRITICAL_SKILLS_JOB_NAME for job in jobs)

        results = service.handle_runtime_transition(previous_active=True, current_active=False)
        assert results
        assert results[0]["status"] in {"ok", "skipped"}
        assert output_path.exists()
        assert micro_output_path.exists()

        jobs_after = store.list_scheduled_jobs(
            enabled_only=False,
            cron_expr=ScheduledJobsService.MAIN_IDLE_TRIGGER,
        )
        compile_job = next(
            job for job in jobs_after if str(job.get("job_id") or "").strip() == ScheduledJobsService.CRITICAL_SKILLS_JOB_ID
        )
        assert str(compile_job.get("last_status") or "").strip() in {"ok", "skipped"}
        assert str(compile_job.get("last_run_at") or "").strip()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_seed_defaults_can_skip_missing_artifact_compilation():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-scheduled-jobs-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        store = SQLiteStore(database_path=str(scratch / "scheduled.db"))
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()
        service = ScheduledJobsService(
            sqlite_store=store,
            skill_registry=registry,
            critical_skills_output_path=str(scratch / "missing-critical.md"),
            micro_skills_output_path=str(scratch / "missing-micro.md"),
        )

        service.seed_defaults(ensure_compiled_artifacts=False)

        assert not (scratch / "missing-critical.md").exists()
        assert not (scratch / "missing-micro.md").exists()
        assert store.list_scheduled_jobs(
            enabled_only=True,
            cron_expr=ScheduledJobsService.MAIN_IDLE_TRIGGER,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
