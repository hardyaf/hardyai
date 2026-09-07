from __future__ import annotations

import argparse
import signal
from dataclasses import dataclass
from threading import Event
from typing import Any
from uuid import uuid4

from app.core.approved_action_execution import ApprovedActionExecutionService
from app.jobs.repository import DurableJobRepository
from app.jobs.types import (
    REVIEW_ACTION_EXECUTION_JOB,
    REVIEW_NOTIFICATION_DISCORD_JOB,
    REVIEW_OUTCOME_DISCORD_JOB,
)
from app.services.discord.approval_delivery import ApprovalDelivery, ApprovalDeliveryError


@dataclass(frozen=True, slots=True)
class ActionApprovalWorkerConfig:
    enabled: bool = False
    poll_seconds: float = 2.0
    batch_size: int = 10
    lease_seconds: int = 60

    def __post_init__(self) -> None:
        if not 1.0 <= float(self.poll_seconds) <= 60.0:
            raise ValueError("action_approval_poll_seconds_invalid")
        if not 1 <= int(self.batch_size) <= 50:
            raise ValueError("action_approval_batch_size_invalid")
        if not 15 <= int(self.lease_seconds) <= 600:
            raise ValueError("action_approval_lease_seconds_invalid")


class ActionApprovalWorker:
    WORKER_TYPE = "action_approval"

    def __init__(
        self,
        *,
        jobs: DurableJobRepository,
        delivery: ApprovalDelivery,
        execution: ApprovedActionExecutionService,
        config: ActionApprovalWorkerConfig,
        worker_id: str | None = None,
        record_startup_heartbeat: bool = True,
    ) -> None:
        self._jobs = jobs
        self._delivery = delivery
        self._execution = execution
        self.config = config
        self.worker_id = str(worker_id or f"action-approval-{uuid4()}")
        self._stop = Event()
        if record_startup_heartbeat:
            self._heartbeat(status="starting", metadata={"enabled": config.enabled})

    def request_stop(self) -> None:
        self._stop.set()

    def readiness(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.config.enabled else "disabled",
            "worker_enabled": self.config.enabled,
            "job_types": [
                REVIEW_ACTION_EXECUTION_JOB,
                REVIEW_NOTIFICATION_DISCORD_JOB,
                REVIEW_OUTCOME_DISCORD_JOB,
            ],
        }

    def run_once(self) -> dict[str, Any]:
        if not self.config.enabled:
            self._heartbeat(status="disabled", metadata={"claimed_count": 0})
            return {"status": "disabled", "claimed_count": 0}
        self._heartbeat(status="polling", metadata={})
        remaining = int(self.config.batch_size)
        notification_jobs = self._jobs.claim_jobs(
            job_type=REVIEW_NOTIFICATION_DISCORD_JOB,
            worker_id=self.worker_id,
            limit=remaining,
            lease_seconds=float(self.config.lease_seconds),
        )
        remaining -= len(notification_jobs)
        execution_jobs = (
            self._jobs.claim_jobs(
                job_type=REVIEW_ACTION_EXECUTION_JOB,
                worker_id=self.worker_id,
                limit=remaining,
                lease_seconds=float(self.config.lease_seconds),
            )
            if remaining > 0
            else []
        )
        remaining -= len(execution_jobs)
        outcome_jobs = (
            self._jobs.claim_jobs(
                job_type=REVIEW_OUTCOME_DISCORD_JOB,
                worker_id=self.worker_id,
                limit=remaining,
                lease_seconds=float(self.config.lease_seconds),
            )
            if remaining > 0
            else []
        )
        completed = 0
        retried = 0
        dead_lettered = 0
        for job in notification_jobs:
            try:
                self._delivery.deliver(job)
            except ApprovalDeliveryError as exc:
                code = str(exc)[:120] or "approval_delivery_failed"
                if self._delivery_error_retryable(code):
                    self._retry(job, code=code)
                    retried += 1
                else:
                    self._dead_letter(job, code=code)
                    dead_lettered += 1
            except Exception:
                self._retry(job, code="approval_delivery_unexpected")
                retried += 1
            else:
                self._complete(job)
                completed += 1
        for job in execution_jobs:
            try:
                outcome = self._execution.execute(job)
            except Exception:
                self._dead_letter(job, code="approval_execution_unexpected")
                dead_lettered += 1
                continue
            status = str(outcome.get("status") or "")
            if status == "retryable":
                self._retry(job, code=str(outcome.get("reason_code") or status))
                retried += 1
            elif status == "reconciliation_required":
                self._dead_letter(job, code=str(outcome.get("reason_code") or status))
                dead_lettered += 1
            else:
                self._complete(job)
                completed += 1
        for job in outcome_jobs:
            try:
                self._delivery.deliver_outcome(job)
            except ApprovalDeliveryError as exc:
                code = str(exc)[:120] or "approval_outcome_delivery_failed"
                if self._delivery_error_retryable(code):
                    self._retry(job, code=code)
                    retried += 1
                else:
                    self._dead_letter(job, code=code)
                    dead_lettered += 1
            except Exception:
                self._retry(job, code="approval_outcome_delivery_unexpected")
                retried += 1
            else:
                self._complete(job)
                completed += 1
        claimed = len(notification_jobs) + len(execution_jobs) + len(outcome_jobs)
        degraded = bool(retried or dead_lettered)
        self._heartbeat(
            status="degraded" if degraded else "idle",
            last_error_code="action_approval_job_failed" if degraded else None,
            metadata={
                "claimed_count": claimed,
                "completed_count": completed,
                "retry_count": retried,
                "dead_letter_count": dead_lettered,
            },
        )
        return {
            "status": "degraded" if degraded else "ok",
            "claimed_count": claimed,
            "completed_count": completed,
            "retry_count": retried,
            "dead_letter_count": dead_lettered,
        }

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(float(self.config.poll_seconds))

    def _complete(self, job: dict[str, Any]) -> None:
        if not self._jobs.complete_job(
            job_id=str(job["job_id"]),
            worker_id=self.worker_id,
            fencing_token=int(job["lease_fencing_token"]),
        ):
            raise RuntimeError("action_approval_job_completion_lost")

    def _retry(self, job: dict[str, Any], *, code: str) -> None:
        attempt = max(1, int(job.get("attempt_count") or 1))
        if not self._jobs.retry_job(
            job_id=str(job["job_id"]),
            worker_id=self.worker_id,
            fencing_token=int(job["lease_fencing_token"]),
            error_code=str(code)[:120],
            delay_seconds=min(300.0, 5.0 * (2 ** (attempt - 1))),
        ):
            raise RuntimeError("action_approval_job_retry_lost")

    def _dead_letter(self, job: dict[str, Any], *, code: str) -> None:
        if not self._jobs.dead_letter_job(
            job_id=str(job["job_id"]),
            worker_id=self.worker_id,
            fencing_token=int(job["lease_fencing_token"]),
            error_code=str(code)[:120],
        ):
            raise RuntimeError("action_approval_job_dead_letter_lost")

    def _heartbeat(
        self,
        *,
        status: str,
        metadata: dict[str, Any],
        last_error_code: str | None = None,
    ) -> None:
        self._jobs.record_worker_heartbeat(
            worker_type=self.WORKER_TYPE,
            worker_id=self.worker_id,
            status=status,
            last_error_code=last_error_code,
            metadata=metadata,
        )

    @staticmethod
    def _delivery_error_retryable(code: str) -> bool:
        terminal_markers = (
            "binding_mismatch",
            "binding_changed",
            "not_pending",
            "not_terminal",
            "proposal_missing",
            "payload_invalid",
            "card_",
        )
        return not any(marker in code for marker in terminal_markers)


def _build_worker(*, record_startup_heartbeat: bool = True) -> ActionApprovalWorker:
    from app import runtime
    from app.config import settings
    from app.services.discord.approval_delivery import DiscordRestApprovalGateway

    delivery = ApprovalDelivery(
        reviews=runtime.human_review_repository,
        permissions_path=settings.discord_permissions_path,
        gateway=DiscordRestApprovalGateway(bot_token=settings.discord_bot_token),
    )
    execution = ApprovedActionExecutionService(
        reviews=runtime.human_review_repository,
        authorized_executor=runtime.router.authorized_skill_executor,
        identity_service=runtime.external_identity_service,
        available_runtime_dependencies=runtime.router.available_runtime_dependencies,
    )
    return ActionApprovalWorker(
        jobs=runtime.job_repository,
        delivery=delivery,
        execution=execution,
        config=ActionApprovalWorkerConfig(
            enabled=settings.action_approval_worker_enabled,
            poll_seconds=settings.action_approval_worker_poll_seconds,
            batch_size=settings.action_approval_worker_batch_size,
            lease_seconds=settings.action_approval_worker_lease_seconds,
        ),
        record_startup_heartbeat=record_startup_heartbeat,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded action approval worker.")
    parser.add_argument("--readiness-only", action="store_true")
    args = parser.parse_args()
    from app.config import settings
    from app.services.offline_runtime_policy import validate_offline_runtime

    validate_offline_runtime(settings, entrypoint="action-approval-worker")
    if not settings.action_approval_worker_enabled:
        print('{"status":"disabled","worker_enabled":false}')
        return 0
    worker = _build_worker(record_startup_heartbeat=not args.readiness_only)
    if args.readiness_only:
        import json

        print(json.dumps(worker.readiness(), sort_keys=True, separators=(",", ":")))
        return 0
    signal.signal(signal.SIGINT, lambda *_: worker.request_stop())
    signal.signal(signal.SIGTERM, lambda *_: worker.request_stop())
    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
